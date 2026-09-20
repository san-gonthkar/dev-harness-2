# ADR-0001: IPC Transport — Length-Prefixed JSON over AF_UNIX

- **Status**: Accepted
- **Date**: 2026-09-20
- **Applies to**: Phase 2 (IPC Transport & Event Bus)

## Context

The engine daemon and the TUI are separate processes on a single host. They must
exchange a closed vocabulary of 9 typed events (V11 0.6) with backpressure and
no drift. The transport must be:

- **Single-host**: engine and TUI always share a machine.
- **Low fanout**: one producer (engine) to one consumer (TUI), plus a broker.
- **Typed**: the wire format must round-trip the Pydantic `Envelope` losslessly.
- **Backpressure-safe**: a slow consumer must not grow memory without bound.

## Decision

Use **length-prefixed JSON over `AF_UNIX` stream sockets**.

- Each frame is a 4-byte big-endian unsigned length prefix followed by UTF-8
  JSON encoding of the `Envelope` (V11 0.6).
- Per-type maximum frame sizes: 1 MiB for streaming/control types, 16 MiB for
  `SNAPSHOT` (see ADR-0003).
- Socket permissions are `0600`; stale sockets are unlinked on bind.
- Socket scope follows ADR-0002: workspace-scoped for engines, host-scoped for
  the broker.

## Rejected Alternatives

| Alternative | Why rejected |
| :--- | :--- |
| gRPC | Codegen toolchain (protoc) is heavyweight for a single-host, low-fanout channel; adds a build step and a second schema to keep in sync with the Pydantic models. |
| HTTP/JSON-RPC over TCP | TCP brings port allocation, firewall surface, and no natural socket-permission story; AF_UNIX gives filesystem permissions for free. |
| Raw JSON with newline delimiters | No framing guarantees; a multi-line pretty-printed payload would corrupt the stream. |
| MessagePack / CBOR | Adds a binary codec dependency; JSON is already the Pydantic serialization format and is debuggable with `jq`. |

## Consequences

- **Positive**: single schema (Pydantic) drives both validation and wire format;
  AF_UNIX permissions give `0600` socket security; framing is trivial to
  implement and test.
- **Negative**: AF_UNIX is POSIX-only — Windows requires WSL2 (see 2.7
  `UnsupportedPlatformError`).
- **Negative**: JSON is larger than binary codecs; mitigated by the 1 MiB /
  16 MiB frame limits and the fact that events are small except `SNAPSHOT`.
