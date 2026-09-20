---
name: textual-tui
description: 'Textual TUI development per the V11 plan. Use when working with tui/: app shell, four-panel layout, widgets, pilot testing, call_from_thread/post_message bridge, 20 Hz throttle, keybindings, and safe rendering. Covers the tui/ coverage split and snapshot baselines.'
user-invocable: true
---

# Textual TUI (V11 Phase 7)

## When to Use

- Working with `tui/` — app, panels, bridge, throttle, bindings, render
- Writing or fixing any Textual widget code

## App Shell (task 7.1)

`HermesApp` + CSS grid, four regions: `#repo-manager`, `#execution-canvas`, `#model-registry`, `#critic-bar`.

## Pilot Testing

Headless mount with `run_test`:

```python
async with app.run_test(size=(100, 50)) as pilot:
    ...
```

- Use `pilot.pause()` to wait for pending messages to be processed before asserting.
- Use `pilot.press("ctrl+c")` for keybinding tests.
- Snapshot baselines must be **reviewed in the PR that introduces them** — never commit an unreviewed baseline.

## Thread Marshalling (task 7.7)

- IPC→UI bridge via `call_from_thread` / `post_message`.
- `SNAPSHOT` (full `HarnessState`) is applied as the first frame on attach, before any delta.
- 5,000 cross-thread events: 0 `NoActiveAppError`, 0 dropped control events.

## Throttle (task 7.8)

20 Hz coalescing: 2s / 10k tokens → ≤44 `RichLog.write` calls; max loop iteration < 50ms.

## Keybindings (task 7.9)

`Ctrl+C` priority-bound to PAUSE (app stays running); `Ctrl+Q` opens confirm modal.

## Safe Rendering (task 7.10)

Markdown + diff colorizer escaping Rich markup: `[bold red]` renders literally; no `MarkupError`.

## Coverage Split

- `tui/` overall: 75% line / 65% branch — widget wiring is snapshot- and pilot-tested.
- `tui/bridge.py`, `tui/throttle.py`, `tui/render.py`: 95% line / 90% branch — these carry logic (thread marshalling, coalescing math, markup escaping), not rendering.