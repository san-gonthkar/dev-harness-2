# Blackbox End-to-End Testing

Drive the **real** Dev Harness — the actual TUI process in a real
pseudo-terminal, and the actual SDLC pipeline against a real LLM. Nothing is
constructed in-process and no driver is injected, so the application cannot
tell it is under test.

This is the Cypress/Selenium model applied to a terminal app: the harness
launches the app, observes what a user would see, and asserts on it.

---

## Why not Selenium

Selenium drives **web browsers**. This application has no web UI — it is a
terminal app (Textual TUI) plus background daemons over sockets. There is
nothing for Selenium to attach to.

| Layer | Tool | Why |
| :--- | :--- | :--- |
| Browser UI | Selenium / Playwright / Cypress | ❌ not applicable |
| **Terminal UI** | **`pywinpty` + `pyte`** | ✅ a real PTY + a screen model |
| Test runner | pytest | already the project standard |

`pywinpty` spawns the app in a real **ConPTY** (Windows) or pty (POSIX).
`pyte` interprets the ANSI byte stream into a screen grid, so tests assert on
**rendered text**, never on widget IDs or internal state.

---

## Quick start

```bat
rem 1. Blackbox TUI tests — no LLM, no cost
run-tests.bat tui

rem 2. Real-LLM calculator build — costs money
rem    First edit live-test.env.bat (key + model), then:
run.bat start --no-tui
run-tests.bat live

rem 3. Both
run-tests.bat all
```

---

## The DSL (Cypress-style)

Tests read as user stories. Every action returns the session, so calls chain;
every assertion **retries until a timeout**, so a slow render is not a flake.

```python
from tests.blackbox.dsl import launch

with launch(workspace) as tui:
    tui.should_see("field")                 # retries until visible
    tui.press("ctrl+c").should_still_be_running()
    tui.press("ctrl+q").should_see("Quit Hermes?")
    tui.press("enter").should_have_exited()
```

| Method | Purpose |
| :--- | :--- |
| `launch(ws, cols=, rows=, cast_path=)` | Start the real app in a PTY |
| `should_see(text)` | Assert text becomes visible (retries) |
| `should_not_see(text)` | Assert text never appears |
| `should_still_be_running()` | Assert the app has not exited |
| `should_have_exited()` | Assert the app exits |
| `press("ctrl+c", "enter")` | Press Cypress-style keys |
| `type("text")` | Type literal text |
| `resize(cols, rows)` | Resize the terminal |
| `screen()` / `lines()` | The current rendered screen |
| `screenshot(path)` | Write the screen to a text file |

Key names: `enter`, `escape`, `tab`, `space`, `up`/`down`/`left`/`right`,
`ctrl+a`…`ctrl+z`, `ctrl+\`. A single character passes through, so
`press("y")` types `y`.

---

## What is covered

### Blackbox TUI (`tests/blackbox/test_tui_blackbox.py`) — 7 tests, no cost

| Test | Asserts |
| :--- | :--- |
| `test_renders_the_four_panel_dashboard` | All four panels render; the workspace tree lists files |
| `test_degrades_below_80x24` | Below 80×24 only the execution canvas is shown (9.5) |
| `test_ctrl_c_requests_pause_without_exiting` | ctrl+c pauses; the app keeps running (7.9) |
| `test_ctrl_q_confirm_quit` | ctrl+q → modal → Enter exits (7.9) |
| `test_ctrl_q_cancel_keeps_running` | ctrl+q → Escape cancels (7.9) |
| `test_records_a_replayable_cast` | The session is a valid asciinema v2 cast |
| `test_screenshot_captures_the_screen` | A screen capture can be written to disk |

### Real-LLM calculator (`tests/blackbox/test_calculator_live.py`) — 2 tests, costs money

| Test | Asserts |
| :--- | :--- |
| `test_harness_builds_a_calculator_with_a_real_model` | The SDLC graph runs against a real provider and the workspace changes |
| `test_generated_calculator_tests_pass` | If the model wrote tests, they pass |

Both **skip** unless `DEV_HARNESS_OPENROUTER_API_KEY` is set.

---

## Configuration

Edit **`live-test.env.bat`** (gitignored):

```bat
set "DEV_HARNESS_OPENROUTER_API_KEY=sk-or-v1-..."
set "DEV_HARNESS_LIVE_MODEL=anthropic/claude-3.5-sonnet"
set "DEV_HARNESS_BROKER_ENDPOINT="
```

- **Key** — from <https://openrouter.ai/keys>.
- **Model** — OpenRouter ids are namespaced `vendor/model`
  (`anthropic/claude-3.5-sonnet`, `openai/gpt-4o-mini`, …). Blank uses the
  config default.
- **Endpoint** — blank auto-discovers the running broker (recommended).

The broker must be running for `live` (fail-closed): `run.bat start --no-tui`.

---

## Recording

Every session writes an **asciinema v2 `.cast`** file — the raw PTY byte
stream, which is exactly what asciinema records. Pass `cast_path=` to
`launch()`, or use `run-tests.bat record`.

Replay it with any asciinema player, or convert to GIF with
[`agg`](https://github.com/asciinema/agg):

```bash
agg demo.cast demo.gif
```

> I cannot record video or GIF directly — only capture the terminal stream.
> `agg` is the tool that turns a `.cast` into a GIF.

---

## Test lanes

| Lane | Command | Tier |
| :--- | :--- | :--- |
| Blackbox TUI | `pytest tests/blackbox -m e2e` | NIGHTLY (spawns real processes) |
| Real LLM | `pytest tests/blackbox -m live` | LIVE (opt-in, costs money) |
| Smoke | `make test` | PR — excludes both |

The `live` marker is registered in `pytest.ini` and allowed by
`tests/support/markers.py`. It never runs in the smoke lane.

---

## Architecture

```
tests/blackbox/
  harness.py              PTY spawn + pyte screen + .cast recording
  dsl.py                  Cypress-style chainable API (launch, should_see, press)
  test_tui_blackbox.py    TUI behaviour (no LLM)
  test_calculator_live.py real-LLM calculator build (opt-in)
```

The TUI's live feed is wired in `src/dev_harness/tui/live_feed.py`: it connects
to the engine's **streaming** socket and pushes envelopes into the bridge. The
engine broadcasts on that socket via `IpcServer(push_source=...)`.

---

## Known limits

- **Windows-only verified.** The POSIX path uses `ptyprocess`; it is written but
  untested here.
- **The live tests are non-deterministic.** A real model may produce different
  code each run, so the tests assert the *run completed and produced changes*,
  not that the generated code is correct. Judging the artifact is a human step.
- **`test_generated_calculator_tests_pass` skips** when the model writes no
  tests — a quality signal, not a harness failure.
- **The TUI feed is new.** The panels render live events, but the pipeline is
  driven by `dev-harness-live`, not by the TUI itself.
