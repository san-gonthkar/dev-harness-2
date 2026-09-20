"""Broker CLI + load generator for acceptance (V11 4.12).

Subcommands: ``loadgen`` (synthetic load against the broker), ``reserve``
(manual reserve/commit/release, ``--then-kill`` for the leak demo), and
``metrics --follow`` (stream metrics updates).
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from dev_harness.broker.client import BrokerClient
from dev_harness.broker.daemon import BrokerUnavailableError
from dev_harness.config import HarnessConfig
from dev_harness.contracts.enums import ProviderId


def _make_client(args: argparse.Namespace) -> BrokerClient:
    """Build a broker client from CLI args."""
    config = HarnessConfig()
    if getattr(args, "config", None):
        from dev_harness.config import load_config

        config = load_config(args.config)
    return BrokerClient(getattr(args, "socket", None), config=config)


def _loadgen(args: argparse.Namespace) -> int:
    """Run a synthetic load generator against the broker."""
    client = _make_client(args)
    provider = ProviderId(args.provider)
    policy_rpm = int(args.policy_rpm)
    duration = float(args.duration)
    run_budget = float(args.run_budget) if args.run_budget else None
    unit_cost = float(args.unit_cost) if args.unit_cost else None
    report_dir = Path("reports")
    report_dir.mkdir(exist_ok=True)
    csv_path = report_dir / "rate_ceiling_load.csv"
    grants: list[int] = []
    window: list[float] = []
    start = time.monotonic()
    deadline = start + duration
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["t", "granted", "rolling_60s"])
        while time.monotonic() < deadline:
            reply = client.reserve(provider, tokens=1.0, callback_endpoint="loadgen")
            granted = bool(reply.data.get("granted", False))
            if granted:
                grants.append(1)
                window.append(time.monotonic())
                if run_budget is not None and unit_cost is not None:
                    client.commit(
                        provider,
                        str(reply.data.get("reservation_id", "")),
                        actual=1.0,
                        model="qwen2.5-coder:7b",
                        usage_in=1000,
                        usage_out=1000,
                        callback_endpoint="loadgen",
                    )
                else:
                    client.release(provider, str(reply.data.get("reservation_id", "")))
            else:
                grants.append(0)
            now = time.monotonic()
            rolling = sum(1 for t in window if now - t <= 60.0)
            writer.writerow([f"{now - start:.3f}", int(granted), rolling])
            time.sleep(0.05)
    client.close()
    print(f"loadgen done: {sum(grants)} grants, ceiling {policy_rpm}/min")
    return 0


def _reserve(args: argparse.Namespace) -> int:
    """Manual reserve/commit/release; --then-kill for the leak demo."""
    client = _make_client(args)
    provider = ProviderId(args.provider)
    reply = client.reserve(provider, tokens=1.0, callback_endpoint="cli")
    if not reply.data.get("granted", False):
        print(f"reserve refused: {reply.data.get('reason', 'unknown')}")
        client.close()
        return 1
    rid = str(reply.data.get("reservation_id", ""))
    print(f"reserved {rid}")
    if args.then_kill:
        # Simulate a client that dies without releasing: just close.
        client.close()
        print("client killed; capacity returns at TTL+1s")
        return 0
    if args.commit:
        client.commit(
            provider,
            rid,
            actual=float(args.commit),
            model=args.model or "",
            usage_in=int(args.usage_in),
            usage_out=int(args.usage_out),
            callback_endpoint="cli",
        )
        print(f"committed {rid}")
    else:
        client.release(provider, rid)
        print(f"released {rid}")
    client.close()
    return 0


def _metrics(args: argparse.Namespace) -> int:
    """Fetch metrics once, or --follow to stream."""
    client = _make_client(args)
    if args.follow:
        try:
            while True:
                reply = client.metrics()
                d = reply.data
                print(
                    f"p50={d.get('p50_latency_ms', 0):.1f}ms "
                    f"p95={d.get('p95_latency_ms', 0):.1f}ms "
                    f"tpm={d.get('tpm_burn', 0)} usd={d.get('cumulative_usd', 0):.4f}"
                )
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            client.close()
        return 0
    reply = client.metrics()
    d = reply.data
    print(
        f"p50={d.get('p50_latency_ms', 0):.1f}ms "
        f"p95={d.get('p95_latency_ms', 0):.1f}ms "
        f"tpm={d.get('tpm_burn', 0)} usd={d.get('cumulative_usd', 0):.4f}"
    )
    client.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Broker CLI entry point."""
    parser = argparse.ArgumentParser(prog="dev-harness-broker-cli")
    parser.add_argument("--config", default=None, help="Path to a TOML config")
    parser.add_argument("--socket", default=None, help="Broker socket path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_load = sub.add_parser("loadgen", help="Synthetic load generator")
    p_load.add_argument("--provider", default="anthropic")
    p_load.add_argument("--rpm-target", type=int, default=200)
    p_load.add_argument("--policy-rpm", type=int, default=50)
    p_load.add_argument("--duration", type=float, default=120.0)
    p_load.add_argument("--run-budget", type=float, default=None)
    p_load.add_argument("--unit-cost", type=float, default=None)
    p_load.add_argument("--max-concurrency", type=int, default=None)
    p_load.set_defaults(func=_loadgen)

    p_res = sub.add_parser("reserve", help="Manual reserve/commit/release")
    p_res.add_argument("--provider", default="anthropic")
    p_res.add_argument("--commit", type=float, default=None)
    p_res.add_argument("--model", default=None)
    p_res.add_argument("--usage-in", type=int, default=0)
    p_res.add_argument("--usage-out", type=int, default=0)
    p_res.add_argument("--then-kill", action="store_true")
    p_res.set_defaults(func=_reserve)

    p_met = sub.add_parser("metrics", help="Fetch metrics")
    p_met.add_argument("--follow", action="store_true")
    p_met.set_defaults(func=_metrics)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except BrokerUnavailableError as exc:
        print(f"BrokerUnavailableError: {exc}", file=sys.stderr)
        return 2
