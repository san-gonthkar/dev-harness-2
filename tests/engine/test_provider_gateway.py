"""Provider gateway tests (V11 5.5).

Validation matrix: AST + monkeypatch — 0 adapter calls bypass the broker;
broker down -> node raises, no provider call. Coverage contract: 100% line /
95% branch for engine/provider_gateway.py.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dev_harness.contracts.enums import EventType, ProviderId
from dev_harness.contracts.errors import BrokerUnavailableError
from dev_harness.contracts.events import Envelope, ModelConfigChangePayload
from dev_harness.engine.provider_gateway import ProviderGateway

pytestmark = pytest.mark.unit

ENGINE_DIR = Path(__file__).resolve().parents[2] / "src" / "dev_harness" / "engine"

# Provider adapter modules that engine code must never import or call.
_ADAPTER_MODULES = (
    "dev_harness.providers.anthropic",
    "dev_harness.providers.openrouter",
    "dev_harness.providers.ollama",
    "dev_harness.providers.base",
    "dev_harness.providers.registry",
)


class _FakeBroker:
    """A minimal BrokerClient stand-in recording calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.reply_ok = True
        self.reply_data: dict[str, object] = {"reservation_id": "res-1"}
        self.raise_on: str | None = None

    def reserve(self, provider: object, *, tokens: float = 1.0, callback_endpoint: str = "") -> object:
        self.calls.append(("reserve", (provider,), {"tokens": tokens, "callback_endpoint": callback_endpoint}))
        if self.raise_on == "reserve":
            raise BrokerUnavailableError("down", remediation="start broker")
        return _Reply(self.reply_ok, self.reply_data)

    def commit(self, provider: object, reservation_id: str, **kwargs: object) -> object:
        self.calls.append(("commit", (provider, reservation_id), kwargs))
        if self.raise_on == "commit":
            raise BrokerUnavailableError("down", remediation="start broker")
        return _Reply(self.reply_ok, {})

    def release(self, provider: object, reservation_id: str) -> object:
        self.calls.append(("release", (provider, reservation_id), {}))
        if self.raise_on == "release":
            raise BrokerUnavailableError("down", remediation="start broker")
        return _Reply(self.reply_ok, {})

    def metrics(self) -> object:
        self.calls.append(("metrics", (), {}))
        if self.raise_on == "metrics":
            raise BrokerUnavailableError("down", remediation="start broker")
        return _Reply(self.reply_ok, {"tpm": 100})

    def health(self) -> object:
        self.calls.append(("health", (), {}))
        if self.raise_on == "health":
            raise BrokerUnavailableError("down", remediation="start broker")
        return _Reply(self.reply_ok, {})

    def close(self) -> None:
        self.calls.append(("close", (), {}))


class _Reply:
    def __init__(self, ok: bool, data: dict[str, object]) -> None:
        self.ok = ok
        self.data = data


@pytest.fixture
def fake_broker() -> _FakeBroker:
    return _FakeBroker()


@pytest.fixture
def gateway(fake_broker: _FakeBroker) -> ProviderGateway:
    return ProviderGateway(fake_broker)  # type: ignore[arg-type]


# --- AST guard: no adapter calls bypass the broker ---------------------------


@pytest.mark.unit
def test_engine_never_imports_provider_adapters() -> None:
    """AST: no engine module imports or references a provider adapter."""
    for py in ENGINE_DIR.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(_ADAPTER_MODULES), (
                        f"{py.name} imports provider adapter {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert node.module is None or not node.module.startswith(_ADAPTER_MODULES), (
                    f"{py.name} imports provider adapter {node.module}"
                )
            elif (
                                isinstance(node, ast.Attribute)
                                and isinstance(node.value, ast.Name)
                                and node.value.id == "providers"
                            ):
                                # e.g. providers.anthropic.something — never allowed in engine/
                                raise AssertionError(f"{py.name} references providers.{node.attr}")


@pytest.mark.unit
def test_gateway_uses_broker_client_not_adapters() -> None:
    """The gateway's only provider path is the BrokerClient."""
    src = (ENGINE_DIR / "provider_gateway.py").read_text(encoding="utf-8")
    assert "BrokerClient" in src
    for mod in _ADAPTER_MODULES:
        assert mod not in src


# --- routing through the broker ---------------------------------------------


@pytest.mark.unit
def test_reserve_routes_through_broker(gateway: ProviderGateway, fake_broker: _FakeBroker) -> None:
    """reserve() calls the broker and returns the reservation id."""
    rid = gateway.reserve(ProviderId.ANTHROPIC, tokens=2.5, callback_endpoint="ep")
    assert rid == "res-1"
    assert fake_broker.calls[0][0] == "reserve"
    assert fake_broker.calls[0][1] == (ProviderId.ANTHROPIC,)
    assert fake_broker.calls[0][2] == {"tokens": 2.5, "callback_endpoint": "ep"}


@pytest.mark.unit
def test_commit_routes_through_broker(gateway: ProviderGateway, fake_broker: _FakeBroker) -> None:
    """commit() calls the broker with usage details."""
    gateway.commit(
        "anthropic",
        "res-1",
        actual=3.0,
        model="claude-x",
        usage_in=10,
        usage_out=20,
        callback_endpoint="ep",
    )
    assert fake_broker.calls[0][0] == "commit"
    assert fake_broker.calls[0][1] == ("anthropic", "res-1")
    assert fake_broker.calls[0][2] == {
        "actual": 3.0,
        "model": "claude-x",
        "usage_in": 10,
        "usage_out": 20,
        "callback_endpoint": "ep",
    }


@pytest.mark.unit
def test_release_routes_through_broker(gateway: ProviderGateway, fake_broker: _FakeBroker) -> None:
    """release() calls the broker."""
    gateway.release("anthropic", "res-1")
    assert fake_broker.calls[0][0] == "release"
    assert fake_broker.calls[0][1] == ("anthropic", "res-1")


@pytest.mark.unit
def test_metrics_routes_through_broker(gateway: ProviderGateway, fake_broker: _FakeBroker) -> None:
    """metrics() returns the broker's snapshot."""
    assert gateway.metrics() == {"tpm": 100}
    assert fake_broker.calls[0][0] == "metrics"


@pytest.mark.unit
def test_health_true_when_broker_ok(gateway: ProviderGateway, fake_broker: _FakeBroker) -> None:
    """health() is True when the broker replies ok."""
    assert gateway.health() is True


@pytest.mark.unit
def test_health_false_when_broker_down(gateway: ProviderGateway, fake_broker: _FakeBroker) -> None:
    """health() is False when the broker is unreachable."""
    fake_broker.raise_on = "health"
    assert gateway.health() is False


@pytest.mark.unit
def test_close_closes_broker(gateway: ProviderGateway, fake_broker: _FakeBroker) -> None:
    """close() closes the underlying broker connection."""
    gateway.close()
    assert fake_broker.calls[-1][0] == "close"


# --- broker down -> node raises, no provider call ----------------------------


@pytest.mark.unit
def test_reserve_raises_when_broker_down(gateway: ProviderGateway, fake_broker: _FakeBroker) -> None:
    """Broker down: reserve() raises BrokerUnavailableError."""
    fake_broker.raise_on = "reserve"
    with pytest.raises(BrokerUnavailableError):
        gateway.reserve(ProviderId.ANTHROPIC)


@pytest.mark.unit
def test_commit_raises_when_broker_down(gateway: ProviderGateway, fake_broker: _FakeBroker) -> None:
    """Broker down: commit() raises BrokerUnavailableError."""
    fake_broker.raise_on = "commit"
    with pytest.raises(BrokerUnavailableError):
        gateway.commit("anthropic", "res-1")


@pytest.mark.unit
def test_release_raises_when_broker_down(gateway: ProviderGateway, fake_broker: _FakeBroker) -> None:
    """Broker down: release() raises BrokerUnavailableError."""
    fake_broker.raise_on = "release"
    with pytest.raises(BrokerUnavailableError):
        gateway.release("anthropic", "res-1")


@pytest.mark.unit
def test_metrics_raises_when_broker_down(gateway: ProviderGateway, fake_broker: _FakeBroker) -> None:
    """Broker down: metrics() raises BrokerUnavailableError."""
    fake_broker.raise_on = "metrics"
    with pytest.raises(BrokerUnavailableError):
        gateway.metrics()


# --- MODEL_CONFIG_CHANGE emission --------------------------------------------


@pytest.mark.unit
def test_set_active_model_emits_model_config_change() -> None:
    """Changing the active model emits MODEL_CONFIG_CHANGE."""
    seen: list[Envelope] = []
    g = ProviderGateway(_FakeBroker(), on_model_change=seen.append)  # type: ignore[arg-type]
    assert g.set_active_model("claude-x") is True
    assert len(seen) == 1
    env = seen[0]
    assert env.type == EventType.MODEL_CONFIG_CHANGE
    assert isinstance(env.payload, ModelConfigChangePayload)
    assert env.payload.model == "claude-x"


@pytest.mark.unit
def test_set_active_model_same_model_no_emit() -> None:
    """Setting the same model again does not emit a second time."""
    seen: list[Envelope] = []
    g = ProviderGateway(_FakeBroker(), on_model_change=seen.append)  # type: ignore[arg-type]
    g.set_active_model("claude-x")
    assert g.set_active_model("claude-x") is False
    assert len(seen) == 1


@pytest.mark.unit
def test_set_active_model_no_callback() -> None:
    """set_active_model works without a callback."""
    g = ProviderGateway(_FakeBroker())  # type: ignore[arg-type]
    assert g.set_active_model("claude-x") is True
    assert g.active_model == "claude-x"


@pytest.mark.unit
def test_active_model_tracks_changes() -> None:
    """active_model reflects the last set model."""
    g = ProviderGateway(_FakeBroker())  # type: ignore[arg-type]
    assert g.active_model == ""
    g.set_active_model("a")
    assert g.active_model == "a"
    g.set_active_model("b")
    assert g.active_model == "b"