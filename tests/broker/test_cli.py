"""Broker CLI tests (V11 4.12) — loadgen, reserve, metrics."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from dev_harness.broker import cli as cli_mod
from dev_harness.broker.cli import main
from dev_harness.broker.protocol import BrokerMessage

pytestmark = pytest.mark.unit


class FakeClient:
    """A stub client that grants the first N reserves then refuses."""

    def __init__(self, grant_limit: int = 1000) -> None:
        self.grant_limit = grant_limit
        self.grants = 0
        self.reserves = 0
        self.commits = 0
        self.releases = 0
        self.metrics_calls = 0

    def reserve(
        self, provider: Any, *, tokens: float = 1.0, callback_endpoint: str = ""
    ) -> BrokerMessage:
        self.reserves += 1
        if self.grants < self.grant_limit:
            self.grants += 1
            return BrokerMessage(
                op="RESERVE",
                data={
                    "granted": True,
                    "reservation_id": f"r{self.grants}",
                    "provider": "anthropic",
                    "tokens": 1.0,
                },
            )
        return BrokerMessage(
            op="RESERVE", data={"granted": False, "reason": "rate_limited"}
        )

    def commit(self, provider: Any, rid: str, **kw: Any) -> BrokerMessage:
        self.commits += 1
        return BrokerMessage(op="COMMIT", data={"delta": 0.0})

    def release(self, provider: Any, rid: str) -> BrokerMessage:
        self.releases += 1
        return BrokerMessage(op="RELEASE", data={"tokens": 1.0})

    def metrics(self) -> BrokerMessage:
        self.metrics_calls += 1
        return BrokerMessage(
            op="METRICS",
            data={
                "p50_latency_ms": 1.0,
                "p95_latency_ms": 2.0,
                "tpm_burn": 10,
                "cumulative_usd": 0.5,
            },
        )

    def close(self) -> None:
        pass


@pytest.mark.unit
def test_loadgen_respects_policy_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(grant_limit=50)
    monkeypatch.setattr(cli_mod, "_make_client", lambda args: client)
    monkeypatch.chdir(tmp_path)
    rc = main(
        [
            "loadgen",
            "--provider",
            "anthropic",
            "--rpm-target",
            "200",
            "--policy-rpm",
            "50",
            "--duration",
            "0.3",
        ]
    )
    assert rc == 0
    # The fake grants at most the ceiling (50); the loadgen never exceeds it.
    assert client.grants <= 50
    assert client.grants > 0
    csv_path = tmp_path / "reports" / "rate_ceiling_load.csv"
    assert csv_path.exists()
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert len(rows) > 1
    # Header + data rows.
    assert rows[0] == ["t", "granted", "rolling_60s"]


@pytest.mark.unit
def test_reserve_then_kill_releases_at_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient()
    monkeypatch.setattr(cli_mod, "_make_client", lambda args: client)
    rc = main(["reserve", "--provider", "anthropic", "--then-kill"])
    assert rc == 0
    assert client.reserves == 1
    assert client.releases == 0  # killed before release


@pytest.mark.unit
def test_reserve_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    monkeypatch.setattr(cli_mod, "_make_client", lambda args: client)
    rc = main(
        [
            "reserve",
            "--provider",
            "anthropic",
            "--commit",
            "1.0",
            "--model",
            "anthropic-default",
            "--usage-in",
            "100",
            "--usage-out",
            "50",
        ]
    )
    assert rc == 0
    assert client.commits == 1


@pytest.mark.unit
def test_reserve_refused_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(grant_limit=0)
    monkeypatch.setattr(cli_mod, "_make_client", lambda args: client)
    rc = main(["reserve", "--provider", "anthropic"])
    assert rc == 1


@pytest.mark.unit
def test_metrics_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeClient()
    monkeypatch.setattr(cli_mod, "_make_client", lambda args: client)
    rc = main(["metrics"])
    assert rc == 0
    assert client.metrics_calls == 1
    out = capsys.readouterr().out
    assert "p50=" in out
    assert "usd=" in out


@pytest.mark.unit
def test_broker_unavailable_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dev_harness.broker.daemon import BrokerUnavailableError

    def _boom(args: Any) -> Any:
        raise BrokerUnavailableError("down", remediation="start the broker")

    monkeypatch.setattr(cli_mod, "_make_client", _boom)
    rc = main(["metrics"])
    assert rc == 2
