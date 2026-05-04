"""Shared pytest fixtures for relic tests.

`sample_graph` builds a small synthetic NetworkX DiGraph that mirrors what the
real indexer produces — file nodes, symbol nodes, and the three edge types
(imports / defines / extends). Tests exercise search, query, disambiguation,
and TOON serializers against it without ever touching disk.

`tmp_project` materializes a tiny project tree with a `relic.yaml` and source
files, runs `relic index` on it, and yields the project root. Use for tests
that need the real CLI / MCP handler end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

# ---------------------------------------------------------------------------
# In-memory graph fixture — fastest, used for unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_graph() -> nx.DiGraph:
    """A small graph with two subprojects, name collisions, and inbound edges.

    Layout:
        payments/processor.py    — defines PaymentProcessor (class), process (function)
        payments/models.py       — defines Order (class)
        orders/handler.py        — defines process (function)  ← collides with payments
        api/views.py             — imports payments/processor.py

    Graph also seeds inbound edges so node degree differs across files
    (api/views.py is a leaf, payments/processor.py is well-connected).
    """
    G = nx.DiGraph()

    # File nodes
    files = [
        ("payments/processor.py", "python", "payments"),
        ("payments/models.py", "python", "payments"),
        ("orders/handler.py", "python", "orders"),
        ("api/views.py", "python", "api"),
    ]
    for path, lang, sp in files:
        G.add_node(path, ntype="file", path=path, language=lang, subproject=sp)

    # Symbols
    symbols = [
        ("PaymentProcessor", "class", "payments/processor.py", 10),
        ("process", "function", "payments/processor.py", 45),
        ("Order", "class", "payments/models.py", 5),
        ("process", "function", "orders/handler.py", 12),
    ]
    for name, stype, path, line in symbols:
        sid = f"{name}@{path}"
        G.add_node(sid, ntype="symbol", name=name, stype=stype, path=path, line=line)
        G.add_edge(path, sid, etype="defines")

    # Imports
    G.add_edge("api/views.py", "payments/processor.py", etype="imports")
    G.add_edge("payments/processor.py", "payments/models.py", etype="imports")
    G.add_edge("orders/handler.py", "payments/processor.py", etype="imports")

    return G


# ---------------------------------------------------------------------------
# On-disk project fixture — slower, used for end-to-end tests
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Materialize a tiny indexable project and chdir into it.

    Yields the project root. The `relic` package's CLI/MCP handlers read
    `.knowledge/` and `relic.yaml` from the cwd, so tests must run from
    inside the project.
    """
    src = tmp_path / "src"
    src.mkdir()

    (src / "processor.py").write_text(
        "class PaymentProcessor:\n"
        "    def process(self, amount):\n"
        "        return amount * 2\n"
        "\n"
        "def process(items):\n"
        "    return [i for i in items]\n",
        encoding="utf-8",
    )
    (src / "handler.py").write_text(
        "from src.processor import PaymentProcessor\n\ndef process():\n    return PaymentProcessor().process(10)\n",
        encoding="utf-8",
    )
    (src / "views.py").write_text(
        "from src.handler import process as run_handler\n",
        encoding="utf-8",
    )

    (tmp_path / "relic.yaml").write_text(
        'subprojects:\n  app:\n    path: ./src\n    description: "App source"\n',
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    from relic.indexer import run_index

    run_index(tmp_path, tmp_path / ".knowledge", tmp_path / "relic.yaml")

    return tmp_path
