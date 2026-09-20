# ADR-0002: Broker Topology

## Status
Accepted (V11, 2026-09-20)

## Context
The rate-limit broker's purpose is to prevent 429s and runaway spend *across
projects*. A broker socket scoped to a single workspace cannot limit across
projects - the entire reason the broker exists. We must decide where the broker
socket and single-instance lock live, and how the budget kill-switch routes an
`INTERRUPT_REQUEST{STOP, reason=BUDGET}` back to the engine that reserved the
capacity.

## Decision
- **Host-scoped broker socket**: the broker binds a socket under a host-level
  location (e.g. `~/.local/share/dev-harness/broker.sock`), not under any
  workspace's `.dev-harness/`. Any workspace's engine can reach the same broker.
- **Host-scoped single-instance lock**: exactly one broker daemon per host.
  A second instance exits 3 with `AlreadyRunning`.
- **Reservation `callback_endpoint`**: every reservation carries the engine's
  socket path so the kill-switch knows where to route `INTERRUPT_REQUEST`.

## Consequences
- Cross-project rate limiting works: all engines share one broker budget.
- The broker is a host-scoped single point of failure; the fail-closed client
  (4.9) converts an outage into refusal, not overspend (residual risk R7).
- Workspace-scoped sockets are still used by engines (task 2.3); only the broker
  socket is host-scoped.

## Rejected Alternatives
- **Workspace-scoped broker socket**: cannot limit across projects; defeats the
  broker's purpose (rejected).
- **Per-project broker daemon**: N brokers with no shared ceiling; cost and rate
  limits would be per-project, not global (rejected).
