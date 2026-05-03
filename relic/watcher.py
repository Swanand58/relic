"""Filesystem watcher — passively rebuilds the index on source-file changes.

`relic watch` solves the "agent forgot to call relic_reindex" failure mode:
the developer runs it in a terminal tab, the watcher debounces filesystem
events, and the index always reflects current source state. Same security
posture as `relic index` — parse-only static analysis, no execution, symlink
and SKIP_DIRS filtering inherited from the indexer.

Architecture
------------
A `watchdog.Observer` runs in a background thread, fanning OS-native events
(FSEvents on macOS, inotify on Linux, ReadDirectoryChangesW on Windows) into
our `DebouncedReindex` handler. The handler:

1. Filters events to relevant source files (LANGUAGE_MAP suffix, not in
   SKIP_DIRS, not a symlink).
2. Resets a `threading.Timer` on every relevant event — debounces bursts
   from save-on-format, bulk find/replace, git checkout, etc.
3. When the timer fires, runs `run_index` in the foreground thread.
4. If new events arrive while a reindex is running, sets a `_pending` flag
   and reruns once the current reindex completes.

The reindex callback is injectable so tests can drive the handler without
spinning up an observer or touching disk.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from relic import style
from relic.indexer import LANGUAGE_MAP, SKIP_DIRS, run_index
from relic.style import console, err_console

DEBOUNCE_SECONDS = 0.5


class DebouncedReindex(FileSystemEventHandler):
    """Coalesces filesystem events into reindex calls.

    The handler is fully self-contained and re-entrant safe:
    - all timer/state mutations are guarded by `self._lock`
    - the reindex callback runs *outside* the lock so a slow reindex never
      blocks event delivery
    - events arriving during a reindex are recorded via `_pending` and
      trigger a single follow-up reindex when the current one completes
    """

    def __init__(
        self,
        reindex_fn: Callable[[], None],
        *,
        debounce_seconds: float = DEBOUNCE_SECONDS,
        language_map: dict | None = None,
        skip_dirs: set | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        self._reindex_fn = reindex_fn
        self._debounce = debounce_seconds
        self._language_map = language_map if language_map is not None else LANGUAGE_MAP
        self._skip_dirs = skip_dirs if skip_dirs is not None else SKIP_DIRS
        self._on_error = on_error

        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._reindex_running = False
        self._pending = False

    def on_any_event(self, event: FileSystemEvent) -> None:
        if not self._is_relevant(event):
            return
        self._schedule()

    def _is_relevant(self, event: FileSystemEvent) -> bool:
        """Skip irrelevant events early to avoid waking up the timer for
        directory creations, log rotations, .git writes, etc.

        We intentionally inspect both src_path and dest_path (for moves)
        because a rename into a watched directory should still trigger a
        reindex.
        """
        if event.is_directory:
            return False
        for raw in (event.src_path, getattr(event, "dest_path", "")):
            if not raw:
                continue
            p = Path(raw)
            if p.suffix not in self._language_map:
                continue
            if any(part in self._skip_dirs for part in p.parts):
                continue
            return True
        return False

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._tick)
            self._timer.daemon = True
            self._timer.start()

    def _tick(self) -> None:
        """Fire by the debounce timer. Runs reindex unless one is already
        underway, in which case it queues a follow-up via `_pending`.
        """
        with self._lock:
            if self._reindex_running:
                self._pending = True
                return
            self._reindex_running = True

        try:
            self._reindex_fn()
        except BaseException as exc:  # noqa: BLE001 - report and keep watching
            if self._on_error is not None:
                self._on_error(exc)
        finally:
            with self._lock:
                self._reindex_running = False
                should_rerun = self._pending
                self._pending = False
            if should_rerun:
                # Already debounced; re-run immediately to pick up events that
                # arrived while the previous reindex was running.
                self._tick()

    def flush(self) -> None:
        """Cancel any pending debounce timer. Used by tests and shutdown."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


def run_watch(
    project_root: Path,
    knowledge_dir: Path,
    config_file: Path,
    *,
    debounce_seconds: float = DEBOUNCE_SECONDS,
) -> None:
    """Block on a filesystem watcher; rebuild the index when source files change.

    The function returns when the user sends Ctrl+C. Any other exception bubbles
    up so the CLI layer can render it.
    """
    def _do_reindex() -> None:
        t0 = time.monotonic()
        G = run_index(project_root, knowledge_dir, config_file)
        elapsed = time.monotonic() - t0
        files = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "file")
        symbols = sum(1 for _, d in G.nodes(data=True) if d.get("ntype") == "symbol")
        edges = G.number_of_edges()
        ts = time.strftime("%H:%M:%S")
        console.print(
            f"[{style.DIM}]{ts}[/]  "
            f"[{style.PRIMARY}]reindex[/]  "
            f"[{style.DIM}]{style.DOT}[/]  "
            f"[bold {style.FG}]{files}[/] files  "
            f"[bold {style.FG}]{symbols}[/] symbols  "
            f"[bold {style.FG}]{edges}[/] edges  "
            f"[{style.DIM}]({elapsed:.2f}s)[/]"
        )

    def _on_error(exc: BaseException) -> None:
        err_console.print(style.error(f"reindex failed: {exc}"))

    handler = DebouncedReindex(
        _do_reindex,
        debounce_seconds=debounce_seconds,
        on_error=_on_error,
    )

    observer = Observer()
    observer.schedule(handler, str(project_root), recursive=True)
    observer.start()

    console.print(style.header("watch"))
    console.print(
        f"   [{style.DIM}]monitoring[/] {project_root}  "
        f"[{style.DIM}]{style.DOT}  {int(debounce_seconds * 1000)} ms debounce  "
        f"{style.DOT}  Ctrl+C to stop[/]\n"
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print(style.dim("\n   stopping…"))
    finally:
        handler.flush()
        observer.stop()
        observer.join()
