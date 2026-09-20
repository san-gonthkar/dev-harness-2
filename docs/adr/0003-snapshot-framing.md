# ADR-0003: Snapshot Framing — Per-Type Max Frame Sizes

- **Status**: Accepted
- **Date**: 2026-09-20
- **Applies to**: Phase 2 (IPC Transport & Event Bus), task 2.2

## Context

The framing codec (2.2) needs a maximum frame size. A full V7 `HarnessState`
carries `openapi_spec` and `db_schema` fields that can exceed 1 MiB when the
state includes a large interface contract. A single global limit would either
reject legitimate `SNAPSHOT` frames or allow unbounded streaming frames.

## Decision

Use **per-type maximum frame sizes**:

| Event type class | Max frame size |
| :--- | :--- |
| Streaming / control types (`AGENT_TOKEN_STREAM`, `FILE_CHANGE`, `GIT_STATUS_UPDATE`, `TEST_PROGRESS`, `MODEL_CONFIG_CHANGE`, `INTERRUPT_REQUEST`, `INTERRUPT_ACK`, `METRICS_UPDATE`) | 1 MiB |
| `SNAPSHOT` | 16 MiB |

The limit is enforced on the **encoded frame length** (4-byte prefix + JSON
body). A frame exceeding its type's limit raises `FrameTooLargeError` before
any bytes are written to the socket.

## Rejected Alternatives

| Alternative | Why rejected |
| :--- | :--- |
| Multi-frame chunking | Adds reassembly state, sequence bookkeeping, and a second failure mode (partial chunk sets) for a case that fits in 16 MiB. |
| Compression | Adds a codec dependency and CPU cost; JSON compresses well but the 16 MiB ceiling already covers the worst realistic state. |
| Single global limit (e.g. 16 MiB) | Would let a runaway token stream grow to 16 MiB before rejection; the 1 MiB streaming limit bounds memory for the high-frequency path. |
| No limit | A malformed or hostile peer could send an unbounded frame and exhaust memory. |

## Consequences

- **Positive**: memory is bounded per event class; `SNAPSHOT` can carry any
  realistic V7 state; the limit is testable (16 MiB accepted, 17 MiB rejected).
- **Negative**: the codec must know the event type before enforcing the limit —
  it reads the 4-byte prefix, then the JSON body, then validates the type. A
  frame whose body exceeds the *global* 16 MiB ceiling is rejected before
  parsing.
- **Negative**: two limits to document and test instead of one.
