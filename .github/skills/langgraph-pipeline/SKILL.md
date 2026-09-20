---
name: langgraph-pipeline
description: 'LangGraph pipeline development per the V11 plan. Use when working with engine/: state channels and reducers, persona nodes, chunk DAG, worker pool, HITL gates via interrupt_before and Command(resume=...), and graph assembly compiled with SqliteSaver. Covers the engine/ concurrency surface and graph-node coverage.'
user-invocable: true
---

# LangGraph Pipeline (V11 Phase 8)

## When to Use

- Working with `engine/` — state, nodes, dag, worker pool, integrator, routing, classifier, pipeline
- Building or fixing the SDLC graph

## State & Reducers (task 8.3)

LangGraph channels and reducers over the V7 `HarnessState`. Parallel `chunk_dag` appends → 2 entries; concurrent retry increments → exactly +2.

## Graph Assembly (task 8.18)

```python
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

builder = StateGraph(HarnessState)
builder.add_node("groomer", groomer_node)
# ... add nodes and edges ...
builder.add_edge(START, "groomer")
builder.add_conditional_edges("tester", route, {...})

graph = builder.compile(checkpointer=SqliteSaver(...))
```

- Compiled with `SqliteSaver` — the checkpoint store from Phase 1.
- Invocation config requires `{"configurable": {"thread_id": ...}}`.

## HITL Gates (task 8.15)

- `interrupt_before` halts the graph; a persisted checkpoint exists.
- Resume with `Command(resume=...)` advances exactly one node.
- `Command(resume=...)` requires a checkpointer.

## Concurrency Surface (tasks 8.6–8.9)

`dag.py`, `worker_pool.py`, `worker_workspace.py`, `integrator.py` — the mutation-gated engine modules. A scheduling or isolation bug corrupts work silently. 95% line / 90% branch / ≥80% mutation.

## Graph-Node Coverage (task 10.C)

Every node in the compiled graph must be executed ≥1× across the E2E runs, asserted programmatically from the run trace.