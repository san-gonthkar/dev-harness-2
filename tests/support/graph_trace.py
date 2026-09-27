"""LangGraph node-execution trace for the 10.C graph-node coverage contract.

A pipeline can reach high line coverage while an entire node never executes in
any end-to-end test (V11 §10.C). The two E2E runs (10.1 full SDLC, 10.2 parallel
SDLC) each attach a :class:`NodeTrace` to the graph's ``RunnableConfig``
``callbacks``; LangGraph stamps every node invocation with
``metadata['langgraph_node']``, which this records.

Both runs merge their observed node names into ``reports/graph_trace.json`` so
``scripts/generate_traceability.py --graph-coverage`` can assert that every node
in the compiled graph executed at least once across the two runs.

No new dependencies: ``BaseCallbackHandler`` ships with ``langchain-core``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

#: Where the merged node trace is written (repo-relative from this file).
TRACE_PATH = Path(__file__).resolve().parents[2] / "reports" / "graph_trace.json"


class NodeTrace(BaseCallbackHandler):
    """Records the name of every graph node that executes during a run."""

    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[str] = []

    def on_chain_start(
        self, serialized: dict[str, Any], inputs: Any, **kwargs: Any
    ) -> None:
        """Record ``metadata['langgraph_node']`` when the run enters a node."""
        metadata: dict[str, Any] = kwargs.get("metadata") or {}
        node = metadata.get("langgraph_node")
        if isinstance(node, str) and node:
            self.nodes.append(node)

    def merge_into(self, path: Path = TRACE_PATH) -> list[str]:
        """Union this run's nodes into ``path``; return the merged set."""
        existing: set[str] = set()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            existing = set(data.get("executed_nodes", []))
        merged = sorted(existing | set(self.nodes))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"executed_nodes": merged}, indent=2) + "\n",
            encoding="utf-8",
        )
        return merged
