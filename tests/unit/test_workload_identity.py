"""IAM v3 contract and integration through the real CrewAI governance client."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from opentelemetry import trace
from pydantic import SecretStr

from openbox import OpenBoxAgent, OpenBoxSigningError
from openbox.core.aip_signing import AgentIdentity
from openbox.core.client import GovernanceClient
from openbox.core.config import GovernanceConfig
from openbox.core.errors import (
    GovernanceAPIError,
    OpenBoxAuthError,
    OpenBoxConfigError,
    OpenBoxNetworkError,
)
from openbox.core.spans import DbSpanData, Stage
from openbox.core.types import AgentContext, GovernanceResponse, Verdict
from openbox.core.workload_identity import (
    AUTH_BOOTSTRAP_PATH,
    WORKLOAD_TOKEN_HEADER,
    build_private_key_jwt,
    load_workload_private_key,
    parse_bootstrap_document,
    parse_optional_workload_private_key,
    parse_token_response,
)
from openbox.crewai.task import OpenBoxTask
from openbox.engine import OpenBoxEngine
from openbox.instrumentation.interceptors import _runtime
from openbox.instrumentation.span_processor import GovernanceSpanProcessor
from openbox.utils import is_governance_http_suppressed, redact_sensitive_headers

from .conftest import make_agent

API_URL = "https://core.openbox.test"
API_KEY = "obx_test_workload"
TOKEN_URL = "https://identity.openbox.test/realms/test/protocol/openid-connect/token"
BOOTSTRAP = {
    "bootstrap_version": 3,
    "contract_version": 3,
    "token_endpoint": TOKEN_URL,
    "issuer": "https://identity.openbox.test/realms/test",
    "audience": "openbox-core",
    "client_id": "agent-client",
    "service_account_id": "11111111-1111-4111-8111-111111111111",
    "activation_version": "22222222-2222-4222-8222-222222222222",
    "identity_source": "openbox",
    "kid": "key-1",
}


def pem(key) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture(scope="module")
def workload_key() -> str:
    return pem(rsa.generate_private_key(public_exponent=65537, key_size=2048))


@pytest.fixture(autouse=True)
def clean_workload_environment(monkeypatch):
    for name in (
        "AGENT_WORKLOAD_PRIVATE_KEY",
        "OPENBOX_CREWAI_WORKLOAD_PRIVATE_KEY",
        "OPENBOX_WORKLOAD_PRIVATE_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    yield
    _runtime.reset()


class WorkloadWire:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.bootstrap_status = 200
        self.bootstrap = BOOTSTRAP.copy()
        self.token_status = 200
        self.token_body = None
        self.runtime_status = 200
        self.runtime_body = {"verdict": "allow"}
        self.token_count = 0
        self.failed_path = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        # Covers the OTel hooks AND the SDK's extra Client.send body-capture patch.
        assert is_governance_http_suppressed()
        assert _runtime.should_ignore_url(str(request.url))
        if request.url.path == self.failed_path:
            raise httpx.ConnectError("unreachable", request=request)
        if request.url.path == AUTH_BOOTSTRAP_PATH:
            assert WORKLOAD_TOKEN_HEADER not in request.headers
            assert "X-OpenBox-Agent-Signature" not in request.headers
            return httpx.Response(self.bootstrap_status, json=self.bootstrap)
        if str(request.url) == TOKEN_URL:
            assert "authorization" not in request.headers
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["client_credentials"]
            assert form["client_id"] == [BOOTSTRAP["client_id"]]
            assert form["client_assertion_type"] == [
                "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
            ]
            assert len(form["client_assertion"][0].split(".")) == 3
            self.token_count += 1
            return httpx.Response(
                self.token_status,
                json=self.token_body
                or {
                    "access_token": f"workload-token-{self.token_count}",
                    "token_type": "Bearer",
                    "expires_in": 300,
                },
            )
        return httpx.Response(self.runtime_status, json=self.runtime_body)

    @property
    def runtime_calls(self):
        return [
            r for r in self.calls if r.url.path != AUTH_BOOTSTRAP_PATH and str(r.url) != TOKEN_URL
        ]


@pytest.fixture
def wire():
    return WorkloadWire()


@pytest.fixture
async def client(wire):
    client = GovernanceClient(API_URL, GovernanceConfig())
    client._http.close()
    await client._async_http.aclose()
    transport = httpx.MockTransport(wire.handle)
    client._http = httpx.Client(transport=transport)
    client._async_http = httpx.AsyncClient(transport=transport)
    yield client
    await client.aclose()


async def perform(client, operation, key, *, identity=None):
    kwargs = {"identity": identity, "workload_private_key": key}
    if operation == "validate":
        return client.validate_api_key(API_KEY, **kwargs)
    if operation == "evaluate":
        return client.evaluate({"event_type": "ActivityStarted"}, API_KEY, **kwargs)
    if operation == "aevaluate":
        return await client.aevaluate({"event_type": "ActivityStarted"}, API_KEY, **kwargs)
    args = ("workflow", "run", "activity", API_KEY)
    if operation == "poll":
        return client.poll_approval(*args, **kwargs)
    if operation == "apoll":
        return await client.apoll_approval(*args, **kwargs)
    if operation == "wait":
        return client.wait_for_approval(*args, **kwargs)
    return await client.await_for_approval(*args, **kwargs)


def decode_segment(segment):
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def test_private_key_jwt_signature_and_claims(workload_key):
    key = load_workload_private_key(workload_key)
    document = parse_bootstrap_document(BOOTSTRAP)
    token = build_private_key_jwt(key, document)
    protected, payload, signature = token.split(".")
    key.public_key().verify(
        decode_segment(signature),
        f"{protected}.{payload}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    assert json.loads(decode_segment(protected)) == {
        "alg": "RS256",
        "kid": "key-1",
        "typ": "JWT",
    }
    claims = json.loads(decode_segment(payload))
    assert claims["aud"] == TOKEN_URL
    assert claims["iss"] == claims["sub"] == "agent-client"
    assert claims["exp"] - claims["iat"] == 60
    other = build_private_key_jwt(key, document)
    assert claims["jti"] != json.loads(decode_segment(other.split(".")[1]))["jti"]


@pytest.mark.parametrize("kind", ["empty", "malformed", "ec", "small_rsa", "encrypted"])
async def test_invalid_key_rejected_locally_without_leaking(client, wire, workload_key, kind):
    bad = {"empty": "", "malformed": "secret-invalid-key"}.get(kind)
    if kind == "ec":
        bad = pem(ec.generate_private_key(ec.SECP256R1()))
    if kind == "small_rsa":
        bad = pem(rsa.generate_private_key(public_exponent=65537, key_size=1024))
    if kind == "encrypted":
        bad = (
            load_workload_private_key(workload_key)
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(b"password"),
            )
            .decode()
        )
    with pytest.raises(OpenBoxConfigError, match="workload_private_key") as caught:
        await perform(client, "aevaluate", bad)
    if bad:
        assert bad not in str(caught.value)
    assert wire.calls == []


@pytest.mark.parametrize("source", ["openbox", "okta", "entra"])
async def test_all_paths_share_auth_and_cache(client, wire, workload_key, source):
    wire.bootstrap["identity_source"] = source
    client._debug = True
    trace_logger = MagicMock()
    client._traces[API_KEY[-8:]] = trace_logger
    for operation in ("validate", "evaluate", "aevaluate", "poll", "apoll", "wait", "await"):
        await perform(client, operation, workload_key)
    assert wire.token_count == 1
    assert sum(r.url.path == AUTH_BOOTSTRAP_PATH for r in wire.calls) == 1
    assert len(wire.runtime_calls) == 7
    assert all(call.args[0].startswith("/api/v3/") for call in trace_logger.log.call_args_list)
    assert workload_key not in str(trace_logger.log.call_args_list)
    assert "workload-token-1" not in str(trace_logger.log.call_args_list)
    for request in wire.runtime_calls:
        assert request.url.path.startswith("/api/v3/")
        assert request.headers["Authorization"] == f"Bearer {API_KEY}"
        assert request.headers[WORKLOAD_TOKEN_HEADER] == "workload-token-1"
        assert "X-OpenBox-Agent-Signature" not in request.headers
    assert not is_governance_http_suppressed()


@pytest.mark.parametrize(
    "operation", ["validate", "evaluate", "aevaluate", "poll", "apoll", "wait", "await"]
)
@pytest.mark.parametrize("status", [401, 403])
async def test_rejected_identity_never_allows_or_stays_pending(
    client,
    wire,
    workload_key,
    operation,
    status,
):
    wire.runtime_status = status
    wire.runtime_body = {"reason_code": "workload_identity_revoked"}
    for _ in range(2):
        with pytest.raises(OpenBoxSigningError) as caught:
            await perform(client, operation, workload_key)
        assert caught.value.reason_code == "workload_identity_revoked"
        assert isinstance(caught.value, OpenBoxAuthError)
    assert all(r.url.path.startswith("/api/v3/") for r in wire.runtime_calls)


@pytest.mark.parametrize("operation", ["evaluate", "aevaluate", "poll", "apoll"])
async def test_unsigned_rejection_also_fails_closed(client, wire, operation):
    wire.runtime_status = 401
    with pytest.raises(OpenBoxAuthError):
        await perform(client, operation, None)
    assert wire.token_count == 0
    assert wire.calls[-1].url.path.startswith("/api/v1/")


@pytest.mark.parametrize("operation", ["evaluate", "aevaluate", "poll", "apoll"])
@pytest.mark.parametrize(
    "failure",
    [
        "bootstrap_auth",
        "bootstrap_outage",
        "bootstrap_network",
        "metadata",
        "token_auth",
        "token_outage",
        "token_network",
        "token_metadata",
    ],
)
async def test_auth_setup_failures_never_reach_governance(
    client,
    wire,
    workload_key,
    operation,
    failure,
):
    expected = OpenBoxNetworkError
    if failure == "bootstrap_auth":
        wire.bootstrap_status = 403
        expected = OpenBoxAuthError
    elif failure == "bootstrap_outage":
        wire.bootstrap_status = 503
    elif failure == "bootstrap_network":
        wire.failed_path = AUTH_BOOTSTRAP_PATH
    elif failure == "metadata":
        wire.bootstrap["contract_version"] = 999
        expected = OpenBoxConfigError
    elif failure == "token_auth":
        wire.token_status = 401
        expected = OpenBoxAuthError
    elif failure == "token_outage":
        wire.token_status = 503
    elif failure == "token_network":
        wire.failed_path = httpx.URL(TOKEN_URL).path
    else:
        wire.token_body = {"access_token": "secret"}
        expected = OpenBoxConfigError
    with pytest.raises(expected):
        await perform(client, operation, workload_key)
    assert wire.runtime_calls == []
    assert not is_governance_http_suppressed()


@pytest.mark.parametrize(
    "status,body,allowed",
    [
        (404, {}, True),
        (409, {"reason_code": "workload_identity_unavailable"}, True),
        (409, {"code": "workload_identity_unavailable"}, True),
        (409, {"reason_code": "workload_identity_revoked"}, False),
        (409, {"reason": "workload_identity_unavailable"}, False),
        (409, {}, False),
        (302, {}, False),
    ],
)
@pytest.mark.parametrize("with_did", [False, True])
async def test_only_explicit_missing_authority_uses_legacy_route(
    client,
    wire,
    workload_key,
    status,
    body,
    allowed,
    with_did,
):
    wire.bootstrap_status, wire.bootstrap = status, body
    identity = None
    if with_did:
        identity = AgentIdentity(
            did="did:aip:ece81073-60e1-5091-b284-cfd247823a77",
            private_key=base64.b64encode(
                ed25519.Ed25519PrivateKey.generate().private_bytes(
                    serialization.Encoding.Raw,
                    serialization.PrivateFormat.Raw,
                    serialization.NoEncryption(),
                )
            ).decode(),
        )
    if not allowed:
        with pytest.raises(OpenBoxNetworkError):
            await perform(client, "aevaluate", workload_key, identity=identity)
        assert wire.runtime_calls == []
        return
    for operation in ("validate", "aevaluate", "apoll"):
        await perform(client, operation, workload_key, identity=identity)
    assert wire.token_count == 0
    assert sum(r.url.path == AUTH_BOOTSTRAP_PATH for r in wire.calls) == 1
    for request in wire.runtime_calls:
        assert request.url.path.startswith("/api/v1/")
        assert WORKLOAD_TOKEN_HEADER not in request.headers
        assert ("X-OpenBox-Agent-Signature" in request.headers) == with_did


async def test_expired_negative_cache_can_activate_v3(client, wire, workload_key):
    wire.bootstrap_status = 404
    client.validate_api_key(API_KEY, workload_private_key=workload_key)
    auth = next(iter(client._workload_auth.values()))
    auth._unavailable_until = 0
    wire.bootstrap_status = 200
    await perform(client, "aevaluate", workload_key)
    assert wire.calls[-1].url.path == "/api/v3/governance/evaluate"


async def test_refresh_and_failed_refresh_do_not_downgrade(client, wire, workload_key):
    await perform(client, "evaluate", workload_key)
    auth = next(iter(client._workload_auth.values()))
    auth._token = replace(auth._token, expires_at=datetime.now(timezone.utc))
    await perform(client, "apoll", workload_key)
    assert wire.token_count == 2
    assert wire.calls[-1].headers[WORKLOAD_TOKEN_HEADER] == "workload-token-2"
    auth._token = replace(auth._token, expires_at=datetime.now(timezone.utc))
    wire.bootstrap_status, wire.token_status = 404, 403
    with pytest.raises(OpenBoxAuthError):
        await perform(client, "aevaluate", workload_key)
    assert len(wire.runtime_calls) == 2
    assert sum(r.url.path == AUTH_BOOTSTRAP_PATH for r in wire.calls) == 1


async def test_concurrent_sync_async_calls_exchange_one_token(client, wire, workload_key):
    await asyncio.gather(
        *[perform(client, "aevaluate", workload_key) for _ in range(5)],
        *[
            asyncio.to_thread(client.evaluate, {}, API_KEY, workload_private_key=workload_key)
            for _ in range(5)
        ],
    )
    assert wire.token_count == 1
    assert len(wire.runtime_calls) == 10


@pytest.mark.parametrize("difference", ["api_key", "workload_key", "did"])
async def test_credentials_have_isolated_token_caches(client, wire, workload_key, difference):
    client.evaluate({}, API_KEY, workload_private_key=workload_key)
    second_api_key, second_key, identity = API_KEY, workload_key, None
    if difference == "api_key":
        second_api_key = "obx_test_other"
    elif difference == "workload_key":
        second_key = pem(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    else:
        identity = AgentIdentity("did:aip:ece81073-60e1-5091-b284-cfd247823a77", "unused-on-v3")
    await client.aevaluate({}, second_api_key, identity, workload_private_key=second_key)
    client.evaluate({}, API_KEY, workload_private_key=workload_key)
    assert wire.token_count == 2
    assert [r.headers[WORKLOAD_TOKEN_HEADER] for r in wire.runtime_calls] == [
        "workload-token-1",
        "workload-token-2",
        "workload-token-1",
    ]


async def test_transport_fallback_and_policy_verdict_preserved(client, wire, workload_key):
    wire.runtime_status = 503
    result = await perform(client, "aevaluate", workload_key)
    assert result.verdict == Verdict.ALLOW and result.fallback_used
    client._config.on_api_error = "fail_closed"
    with pytest.raises(GovernanceAPIError):
        await perform(client, "evaluate", workload_key)
    wire.runtime_status = 200
    wire.runtime_body = {"verdict": "block", "reason": "policy", "fallback_used": True}
    assert (await perform(client, "evaluate", workload_key)).verdict == Verdict.BLOCK


@pytest.mark.parametrize(
    "field,value",
    [
        ("bootstrap_version", 2),
        ("contract_version", 4),
        ("identity_source", "unknown"),
        ("issuer", "http://identity.example.com/realm"),
        ("issuer", "https://user:pass@host/realm"),
        ("issuer", "https://host:invalid/realm"),
        ("issuer", "https://[broken"),
        ("token_endpoint", "https://other.example.com/token"),
        ("token_endpoint", TOKEN_URL + "?redirect=evil"),
        ("kid", ""),
        ("client_id", None),
        ("audience", 1),
        ("activation_version", "not-a-uuid"),
        ("service_account_id", "0" * 32),
    ],
)
def test_bootstrap_metadata_validation(field, value):
    with pytest.raises(OpenBoxConfigError):
        parse_bootstrap_document({**BOOTSTRAP, field: value})


@pytest.mark.parametrize("lifetime", [True, 0, 30, -1, "300", float("nan"), float("inf")])
def test_invalid_token_lifetime(lifetime):
    with pytest.raises(OpenBoxConfigError):
        parse_token_response(
            200,
            json.dumps(
                {
                    "access_token": "secret",
                    "token_type": "Bearer",
                    "expires_in": lifetime,
                }
            ).encode(),
        )


def test_token_cache_bound_and_secret_redaction(workload_key):
    token = parse_token_response(
        200,
        json.dumps(
            {
                "access_token": "secret-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            }
        ).encode(),
    )
    assert (
        timedelta(seconds=299)
        < token.expires_at - datetime.now(timezone.utc)
        <= timedelta(seconds=300)
    )
    assert "secret-token" not in repr(token)
    assert redact_sensitive_headers({"x-openbox-workload-token": "secret-token"}) == {
        "x-openbox-workload-token": "***REDACTED***",
    }
    assert parse_optional_workload_private_key("\n" + workload_key + "\n") == workload_key.strip()


@pytest.mark.parametrize("source", ["explicit", "agent", "crewai", "global", "absent"])
def test_agent_key_precedence_and_secret_storage(monkeypatch, workload_key, source):
    # Every lower-priority key is invalid, proving it is never selected.
    names = [
        "AGENT_WORKLOAD_PRIVATE_KEY",
        "OPENBOX_CREWAI_WORKLOAD_PRIVATE_KEY",
        "OPENBOX_WORKLOAD_PRIVATE_KEY",
    ]
    index = {"explicit": -1, "agent": 0, "crewai": 1, "global": 2, "absent": 3}[source]
    for i, name in enumerate(names):
        if i >= index:
            monkeypatch.setenv(name, workload_key if i == index else "invalid-lower-priority-key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    agent = OpenBoxAgent(
        role="researcher",
        goal="research",
        backstory="researcher",
        env_prefix="AGENT_",
        workload_private_key=workload_key if source == "explicit" else None,
    )
    expected = None if source == "absent" else workload_key.strip()
    assert agent._resolve_workload_private_key() == expected
    assert workload_key not in repr(agent)
    assert "workload_private_key" not in agent.model_dump()
    assert "workload_private_key" not in agent.model_dump_json()


def test_explicit_empty_key_does_not_fall_through(monkeypatch, workload_key):
    monkeypatch.setenv("OPENBOX_WORKLOAD_PRIVATE_KEY", workload_key)
    agent = make_agent()
    agent.workload_private_key = SecretStr("")
    with pytest.raises(OpenBoxConfigError):
        agent._resolve_workload_private_key()


async def test_engine_validates_distinct_explicit_keys_and_reconfiguration_clears(
    client,
    wire,
    monkeypatch,
    workload_key,
):
    monkeypatch.setenv("AGENT_API_KEY", API_KEY)
    first, second = make_agent(), make_agent()
    first.workload_private_key = SecretStr(workload_key)
    second.workload_private_key = SecretStr(
        pem(
            rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            )
        )
    )
    engine = OpenBoxEngine(API_URL, client._config)
    engine._client = client
    engine.validate_agent_api_keys([first, first, second])
    assert wire.token_count == 2
    assert len(wire.runtime_calls) == 2
    first.configure_governance(client, MagicMock(), client._config, "crew", "run")
    assert first._openbox_workload_private_key == workload_key.strip()
    first.workload_private_key = None
    first.configure_governance(client, MagicMock(), client._config, "crew", "run")
    assert first._openbox_workload_private_key is None


def span_data():
    return DbSpanData(
        stage=Stage.STARTED,
        name="SELECT users",
        db_system="postgresql",
        db_operation="SELECT",
        db_statement="SELECT 1",
    )


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_agent_lifecycle_hooks_and_approvals_use_workload_identity(
    client,
    wire,
    monkeypatch,
    workload_key,
    asynchronous,
):
    monkeypatch.setenv("AGENT_API_KEY", API_KEY)
    agent = make_agent()
    agent.workload_private_key = SecretStr(workload_key)
    sp = GovernanceSpanProcessor(client, client._config)
    agent.configure_governance(client, sp, client._config, "crew", "run")
    engine = OpenBoxEngine(API_URL, client._config)
    engine._client = client
    engine.validate_agent_api_keys([agent])
    _runtime.configure(sp, client, client._config, {API_URL})

    def execute(_self, _task, *_args):
        current = trace.get_current_span().get_span_context()
        wire.runtime_body = {"verdict": "require_approval", "action": "allow"}
        _runtime.evaluate_started(current.trace_id, span_data())
        wire.runtime_body = {"verdict": "allow"}
        _runtime.evaluate_completed(current.trace_id, span_data())
        return "done"

    async def aexecute(*args):
        return execute(*args)

    task = OpenBoxTask(description="Research", expected_output="report", activity_type="research")
    base = OpenBoxAgent.__bases__[0]
    if asynchronous:
        with patch.object(base, "aexecute_task", aexecute):
            assert await agent.aexecute_task(task) == "done"
        await agent._aclose_session(client, "crew", {})
    else:
        with patch.object(base, "execute_task", execute):
            assert agent.execute_task(task) == "done"
        agent.close_session(client, "crew", {})
    assert wire.token_count == 1
    events = {json.loads(r.content).get("event_type") for r in wire.runtime_calls if r.content}
    assert {
        "WorkflowStarted",
        "WorkflowCompleted",
        "ActivityStarted",
        "ActivityCompleted",
    } <= events
    assert any(r.url.path == "/api/v3/governance/approval" for r in wire.runtime_calls)
    assert all(r.headers[WORKLOAD_TOKEN_HEADER] == "workload-token-1" for r in wire.runtime_calls)


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_agent_rethrows_hook_auth_failure_even_if_framework_swallows_it(
    client,
    wire,
    monkeypatch,
    workload_key,
    asynchronous,
):
    monkeypatch.setenv("AGENT_API_KEY", API_KEY)
    agent = make_agent()
    agent.workload_private_key = SecretStr(workload_key)
    sp = GovernanceSpanProcessor(client, client._config)
    agent.configure_governance(client, sp, client._config, "crew", "run")
    _runtime.configure(sp, client, client._config, {API_URL})

    def execute(_self, _task, *_args):
        wire.runtime_status = 403
        wire.runtime_body = {"reason_code": "workload_identity_revoked"}
        try:
            current = trace.get_current_span().get_span_context()
            _runtime.evaluate_started(current.trace_id, span_data())
        except OpenBoxAuthError:
            pass
        return "must not be returned"

    async def aexecute(*args):
        return execute(*args)

    task = OpenBoxTask(description="Research", expected_output="report", activity_type="research")
    base = OpenBoxAgent.__bases__[0]
    with pytest.raises(OpenBoxSigningError):
        if asynchronous:
            with patch.object(base, "aexecute_task", aexecute):
                await agent.aexecute_task(task)
        else:
            with patch.object(base, "execute_task", execute):
                agent.execute_task(task)


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("error", [OpenBoxAuthError, OpenBoxConfigError, OpenBoxNetworkError])
def test_hook_setup_errors_propagate_and_are_recorded(completed, error):
    client = MagicMock()
    client.evaluate.side_effect = error("rejected")
    sp = GovernanceSpanProcessor(client, GovernanceConfig())
    sp.register_trace(123, AgentContext("agent", "wf", "run", API_KEY, "crew", "exec"))
    sp.set_activity_context(123, {"activity_id": "act", "activity_type": "test"})
    _runtime.configure(sp, client, GovernanceConfig())
    with pytest.raises(error):
        (_runtime.evaluate_completed if completed else _runtime.evaluate_started)(123, span_data())
    with pytest.raises(error):
        sp.raise_identity_error(123, "agent")
    sp.clear_trace(123)
    sp.raise_identity_error(123, "agent")


def test_identity_errors_are_crewai_passthrough_exceptions():
    import crewai.agent.core as core

    from openbox.crewai.crew import _register_passthrough_exceptions

    _register_passthrough_exceptions()
    for error in (OpenBoxAuthError, OpenBoxSigningError, OpenBoxConfigError, OpenBoxNetworkError):
        assert isinstance(error("rejected"), core._passthrough_exceptions)


@pytest.mark.parametrize(
    "error", [OpenBoxAuthError, OpenBoxSigningError, OpenBoxConfigError, OpenBoxNetworkError]
)
def test_delegation_propagates_identity_errors(error):
    from openbox.crewai.delegation_tools import OpenBoxDelegateWorkTool

    agent = make_agent()
    tool = OpenBoxDelegateWorkTool(agents=[agent], description="delegate")
    with patch.object(OpenBoxAgent, "execute_task", side_effect=error("rejected")):
        with pytest.raises(error):
            tool._strict_execute("researcher", "task", "context")


async def test_advertised_authority_survives_initial_token_failure(client, wire, workload_key):
    wire.token_status = 503
    with pytest.raises(OpenBoxNetworkError):
        await perform(client, "aevaluate", workload_key)
    wire.bootstrap_status = 404
    wire.token_status = 401
    with pytest.raises(OpenBoxAuthError):
        await perform(client, "evaluate", workload_key)
    assert wire.runtime_calls == []
    assert sum(r.url.path == AUTH_BOOTSTRAP_PATH for r in wire.calls) == 1


@pytest.mark.parametrize("value", ["", "token\nvalue", "token\x00value", "token value", "tökén"])
def test_invalid_token_cannot_become_a_fail_open_transport_error(value):
    with pytest.raises(OpenBoxConfigError):
        parse_token_response(
            200,
            json.dumps(
                {
                    "access_token": value,
                    "token_type": "Bearer",
                    "expires_in": 300,
                }
            ).encode(),
        )


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_sdk_requests_are_excluded_from_real_http_instrumentation(
    client,
    wire,
    workload_key,
    asynchronous,
):
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from openbox.instrumentation.interceptors.httpx_client import (
        httpx_async_request_hook,
        httpx_async_response_hook,
        httpx_request_hook,
        httpx_response_hook,
        setup_httpx_body_capture,
        teardown_httpx_body_capture,
    )

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    sp = MagicMock()
    _runtime.configure(sp, client, client._config)
    instrumentor = HTTPXClientInstrumentor()
    instrumentor.instrument_client(
        client._http,
        tracer_provider=provider,
        request_hook=httpx_request_hook,
        response_hook=httpx_response_hook,
    )
    instrumentor.instrument_client(
        client._async_http,
        tracer_provider=provider,
        request_hook=httpx_async_request_hook,
        response_hook=httpx_async_response_hook,
    )
    setup_httpx_body_capture()
    try:
        with provider.get_tracer("test").start_as_current_span("parent"):
            await perform(client, "aevaluate" if asynchronous else "evaluate", workload_key)
        assert wire.token_count == 1
        sp.store_body.assert_not_called()
        assert [s.name for s in exporter.get_finished_spans()] == ["parent"]
    finally:
        teardown_httpx_body_capture()
        instrumentor.uninstrument_client(client._http)
        instrumentor.uninstrument_client(client._async_http)
        provider.shutdown()


async def test_span_processor_forwards_credentials_and_records_auth_errors(
    client,
    wire,
    workload_key,
):
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    span = provider.get_tracer("test").start_span(
        "HTTP GET",
        attributes={"http.method": "GET", "http.url": "https://example.com"},
    )
    span.end()
    trace_id = span.get_span_context().trace_id
    sp = GovernanceSpanProcessor(client, client._config)
    sp.register_trace(
        trace_id,
        AgentContext(
            "agent",
            "wf",
            "run",
            API_KEY,
            "crew",
            "exec",
            workload_private_key=workload_key,
        ),
    )
    sp.set_activity_context(trace_id, {"activity_id": "act", "activity_type": "test"})
    try:
        sp.on_end(span)
        assert wire.calls[-1].headers[WORKLOAD_TOKEN_HEADER] == "workload-token-1"
        wire.runtime_status = 403
        with pytest.raises(OpenBoxAuthError):
            sp.on_end(span)
        with pytest.raises(OpenBoxAuthError):
            sp.raise_identity_error(trace_id, "agent")
    finally:
        provider.shutdown()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_crew_cleanup_propagates_auth_after_resetting_state(asynchronous):
    from openbox.crewai.crew import GovernedCrew

    first, second = make_agent(), make_agent()
    crew = GovernedCrew(agents=[first, second], tasks=[])
    crew._governance_client = MagicMock()
    hook = MagicMock()
    crew._llm_hook = hook
    for agent in (first, second):
        agent._session_started = True
    method = "_aclose_session" if asynchronous else "close_session"
    with (
        patch.object(OpenBoxAgent, method, side_effect=OpenBoxSigningError("rejected")) as close,
        patch("openbox.crewai.crew.unregister_before_llm_call_hook") as unregister,
    ):
        with pytest.raises(OpenBoxSigningError):
            if asynchronous:
                await crew._acleanup("crew", {})
            else:
                crew._cleanup("crew", {})
        assert close.call_count == 2
        unregister.assert_called_once_with(hook)
        assert crew._llm_hook is None
        assert not first._session_started and not second._session_started


def test_crew_cleanup_preserves_original_failure():
    from openbox.crewai.crew import GovernedCrew

    agent = make_agent()
    crew = GovernedCrew(agents=[agent], tasks=[])
    crew._governance_client = MagicMock()
    agent._session_started = True
    with patch.object(OpenBoxAgent, "close_session", side_effect=OpenBoxAuthError("rejected")):
        with pytest.raises(ValueError, match="original"):
            try:
                raise ValueError("original")
            finally:
                crew._cleanup("crew", {})


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_db_completion_does_not_swallow_identity_errors(asynchronous):
    from openbox.instrumentation.interceptors import db, governance

    method = "evaluate_async" if asynchronous else "evaluate_sync"
    with (
        patch.object(governance, "is_configured", return_value=True),
        patch.object(governance, method, side_effect=OpenBoxAuthError("rejected")),
    ):
        with pytest.raises(OpenBoxAuthError):
            if asynchronous:
                await db._evaluate_db_async("db", {}, is_completed=True)
            else:
                db._evaluate_db_sync("db", {}, is_completed=True)


def test_hook_approval_auth_failure_is_retained_for_agent_boundary():
    client = MagicMock()
    client.evaluate.return_value = GovernanceResponse(verdict=Verdict.REQUIRE_APPROVAL)
    client.wait_for_approval.side_effect = OpenBoxSigningError(
        "rejected", "workload_identity_revoked"
    )
    config = GovernanceConfig()
    sp = GovernanceSpanProcessor(client, config)
    sp.register_trace(123, AgentContext("agent", "wf", "run", API_KEY, "crew", "exec"))
    sp.set_activity_context(123, {"activity_id": "act", "activity_type": "test"})
    _runtime.configure(sp, client, config)
    with pytest.raises(OpenBoxSigningError):
        _runtime.evaluate_started(123, span_data())
    with pytest.raises(OpenBoxSigningError):
        sp.raise_identity_error(123, "agent")
