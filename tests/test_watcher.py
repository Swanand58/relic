"""Tests for relic.watcher.DebouncedReindex.

The handler is exercised in isolation — no Observer, no real filesystem.
We synthesize FileSystemEvent objects and drive the handler's debounce timer
directly. A short debounce (50 ms) keeps the suite fast.
"""

from __future__ import annotations

import threading
import time

from watchdog.events import (
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from relic.watcher import DebouncedReindex

DEBOUNCE = 0.05  # 50 ms — fast enough for tests, slow enough to coalesce
SETTLE = 0.15  # buffer when waiting for timer fire


class _Counter:
    """Reindex stand-in that records call count and optional sleep duration."""

    def __init__(self, work_seconds: float = 0.0):
        self.calls = 0
        self.work = work_seconds
        self.lock = threading.Lock()

    def __call__(self) -> None:
        with self.lock:
            self.calls += 1
        if self.work:
            time.sleep(self.work)


# ---------------------------------------------------------------------------
# Event filtering
# ---------------------------------------------------------------------------


class TestIsRelevant:
    def test_python_file_is_relevant(self):
        c = _Counter()
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)
        assert h._is_relevant(FileModifiedEvent("/proj/src/foo.py"))

    def test_typescript_file_is_relevant(self):
        c = _Counter()
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)
        assert h._is_relevant(FileModifiedEvent("/proj/src/foo.ts"))

    def test_markdown_ignored(self):
        c = _Counter()
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)
        assert not h._is_relevant(FileModifiedEvent("/proj/README.md"))

    def test_skipdir_ignored(self):
        c = _Counter()
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)
        assert not h._is_relevant(FileModifiedEvent("/proj/node_modules/x.js"))
        assert not h._is_relevant(FileModifiedEvent("/proj/.git/HEAD"))

    def test_directory_event_ignored(self):
        c = _Counter()
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)
        evt = FileCreatedEvent("/proj/newdir")
        evt.is_directory = True
        assert not h._is_relevant(evt)

    def test_move_into_watched_path_is_relevant(self):
        c = _Counter()
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)
        # rename of a build artifact INTO src as a .py file should fire
        evt = FileMovedEvent("/tmp/scratch.bin", "/proj/src/foo.py")
        assert h._is_relevant(evt)


# ---------------------------------------------------------------------------
# Debounce behavior
# ---------------------------------------------------------------------------


class TestDebounce:
    def test_single_event_triggers_one_reindex(self):
        c = _Counter()
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)
        h.on_any_event(FileModifiedEvent("/proj/a.py"))
        time.sleep(DEBOUNCE + SETTLE)
        assert c.calls == 1

    def test_burst_coalesces_to_one_reindex(self):
        c = _Counter()
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)
        for i in range(20):
            h.on_any_event(FileModifiedEvent(f"/proj/a{i}.py"))
        time.sleep(DEBOUNCE + SETTLE)
        assert c.calls == 1

    def test_irrelevant_events_never_fire(self):
        c = _Counter()
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)
        h.on_any_event(FileModifiedEvent("/proj/README.md"))
        h.on_any_event(FileModifiedEvent("/proj/.git/HEAD"))
        time.sleep(DEBOUNCE + SETTLE)
        assert c.calls == 0

    def test_two_separate_bursts_fire_twice(self):
        c = _Counter()
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)
        h.on_any_event(FileModifiedEvent("/proj/a.py"))
        time.sleep(DEBOUNCE + SETTLE)
        h.on_any_event(FileModifiedEvent("/proj/b.py"))
        time.sleep(DEBOUNCE + SETTLE)
        assert c.calls == 2


# ---------------------------------------------------------------------------
# Re-entrance: events arriving during reindex
# ---------------------------------------------------------------------------


class TestPendingDuringReindex:
    def test_event_during_reindex_triggers_followup(self):
        # Reindex takes 200 ms. Fire a first event, wait for reindex to start,
        # then fire a second event. Expect two reindex calls total.
        c = _Counter(work_seconds=0.2)
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)

        h.on_any_event(FileModifiedEvent("/proj/a.py"))
        time.sleep(DEBOUNCE + 0.05)  # first reindex now running
        assert c.calls == 1  # mid-flight

        h.on_any_event(FileModifiedEvent("/proj/b.py"))
        # First reindex finishes → pending flag triggers immediate rerun.
        time.sleep(0.5)
        assert c.calls == 2

    def test_no_followup_when_no_events_during_reindex(self):
        c = _Counter(work_seconds=0.1)
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE)
        h.on_any_event(FileModifiedEvent("/proj/a.py"))
        time.sleep(DEBOUNCE + 0.3)  # ample time for reindex + would-be rerun
        assert c.calls == 1

    def test_reindex_exception_is_reported_and_swallowed(self):
        errors: list[BaseException] = []

        def boom():
            raise RuntimeError("simulated reindex failure")

        h = DebouncedReindex(
            boom,
            debounce_seconds=DEBOUNCE,
            on_error=errors.append,
        )

        # First event — error bubbles through on_error
        h.on_any_event(FileModifiedEvent("/proj/a.py"))
        time.sleep(DEBOUNCE + SETTLE)
        assert len(errors) == 1
        assert "simulated reindex failure" in str(errors[0])

        # Subsequent events still fire — handler is not poisoned
        h.on_any_event(FileModifiedEvent("/proj/b.py"))
        time.sleep(DEBOUNCE + SETTLE)
        assert len(errors) == 2


class TestFlush:
    def test_flush_cancels_pending_timer(self):
        c = _Counter()
        h = DebouncedReindex(c, debounce_seconds=DEBOUNCE * 4)  # longer window
        h.on_any_event(FileModifiedEvent("/proj/a.py"))
        h.flush()
        time.sleep(DEBOUNCE * 4 + SETTLE)
        assert c.calls == 0
