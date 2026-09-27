"""Live-run composition root: real provider -> broker -> SDLC pipeline.

This is the only module that wires a **live** provider adapter into the
pipeline. It sits above both ``engine/`` and ``providers/`` (a composition
root), so neither package gains a dependency on the other:

* ``engine/`` still never imports an adapter (the AST guard holds).
* ``providers/`` never imports the engine.

Flow::

    config + secrets -> providers.factory.build_client  (live adapter)
                     -> providers.broker_routed.BrokerRoutedClient (metered)
                     -> engine.pipeline.build_graph      (unchanged pipeline)

Usage::

    dev-harness-live --workspace <repo> --requirement <file> --provider openrouter

The broker must be reachable (fail-closed). On native Windows start it with
``--endpoint tcp:127.0.0.1:8765``; on POSIX the default AF_UNIX socket is used.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from langchain_core.runnables import RunnableConfig

from dev_harness.broker.client import BrokerClient
from dev_harness.config import HarnessConfig, load_config
from dev_harness.contracts.errors import HarnessError
from dev_harness.engine.cli import parse_chunks
from dev_harness.engine.pipeline import PipelineConfig, build_graph, initial_state
from dev_harness.engine.worker_workspace import WorkerWorkspace
from dev_harness.providers.broker_routed import BrokerRoutedClient
from dev_harness.providers.factory import build_client, default_model_for
from dev_harness.providers.registry import ModelRegistry
from dev_harness.secrets import SecretsProvider

EXIT_OK = 0
EXIT_ERROR = 2


def _load_config(path: str | None) -> HarnessConfig:
    """Load config from an explicit path or the working directory default."""
    if path:
        return load_config(path)
    return load_config()


def run_live(
    *,
    workspace: str,
    requirement: str,
    provider: str,
    model: str | None,
    config_path: str | None,
    endpoint: str | None,
    max_parallel: int,
) -> int:
    """Build the live client and execute the SDLC graph end to end."""
    config = _load_config(config_path)
    requirement_path = Path(requirement)
    if not requirement_path.is_file():
        raise HarnessError(
            f"requirement file not found: {requirement}",
            remediation="Point --requirement at an existing markdown file.",
        )
    text = requirement_path.read_text(encoding="utf-8")
    chunks = parse_chunks(text)

    resolved_model = model or default_model_for(provider)
    adapter = build_client(
        provider,
        config=config,
        secrets=SecretsProvider(),
    )
    broker = BrokerClient(endpoint=endpoint, config=config)
    client = BrokerRoutedClient(
        adapter,
        broker,
        provider=provider,
        model=resolved_model,
    )

    registry = ModelRegistry(config)
    model_entry = (
        registry.resolve(resolved_model) if resolved_model in registry else None
    )

    pipeline_config = PipelineConfig(
        client=client,
        workspace=WorkerWorkspace(workspace),
        chunks=chunks,
        max_parallel_workers=max_parallel,
        thread_id="live",
        model=resolved_model,
        model_entry=model_entry,
        raw_input=text,
    )
    graph = build_graph(pipeline_config)
    run_config: RunnableConfig = {
        "configurable": {"thread_id": pipeline_config.thread_id}
    }
    asyncio.run(graph.ainvoke(initial_state(pipeline_config), run_config))

    values = dict(graph.get_state(run_config).values)
    from dev_harness.contracts.state import HarnessState

    state = HarnessState.model_validate(values)
    for chunk in state.chunk_dag:
        print(f"{chunk.chunk_id}: {chunk.status.value}")
    print(f"gate: {state.tui_state.critic_gatekeeper_status.value}")
    client.close()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """``dev-harness-live`` entry point."""
    parser = argparse.ArgumentParser(prog="dev-harness-live")
    parser.add_argument("--workspace", default=".", help="Target git repository")
    parser.add_argument(
        "--requirement", required=True, help="Requirement markdown file"
    )
    parser.add_argument(
        "--provider", default="openrouter", help="Provider id (openrouter, anthropic)"
    )
    parser.add_argument(
        "--model", default=None, help="Model id (defaults per provider)"
    )
    parser.add_argument("--config", default=None, help="Path to a TOML config")
    parser.add_argument(
        "--endpoint",
        default=None,
        help="Broker endpoint: unix:/path/to.sock or tcp:127.0.0.1:8765",
    )
    parser.add_argument("--max-parallel", type=int, default=1, dest="max_parallel")
    args = parser.parse_args(argv)

    try:
        return run_live(
            workspace=args.workspace,
            requirement=args.requirement,
            provider=args.provider,
            model=args.model,
            config_path=args.config,
            endpoint=args.endpoint,
            max_parallel=args.max_parallel,
        )
    except HarnessError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        if exc.remediation:
            print(f"remediation: {exc.remediation}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
