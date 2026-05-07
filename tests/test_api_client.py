"""Tests for custom_components.smartslydr.api_client.

These exercise SmartSlydrApiClient against an aioresponses-mocked HTTP
backend. No HA fixtures required.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession
from aioresponses import aioresponses

from custom_components.smartslydr.api_client import (
    SmartSlydrApiClient,
    SmartSlydrApiError,
    SmartSlydrAuthError,
    _redact,
    _raise_if_upstream_error,
)

BASE = "https://test.example/prod"


@pytest.fixture
async def session() -> ClientSession:
    s = ClientSession()
    try:
        yield s
    finally:
        await s.close()


# ---------------------------------------------------------------------
# _redact
# ---------------------------------------------------------------------


def test_redact_replaces_top_level_tokens() -> None:
    redacted = _redact({"access_token": "abc", "refresh_token": "def", "user": "x"})
    assert redacted == {"access_token": "***", "refresh_token": "***", "user": "x"}


def test_redact_recurses_into_nested_dicts_and_lists() -> None:
    body = {
        "data": {"access_token": "abc", "x": 1},
        "list": [{"refresh_token": "def"}, {"y": 2}],
    }
    out = _redact(body)
    assert out["data"]["access_token"] == "***"
    assert out["data"]["x"] == 1
    assert out["list"][0]["refresh_token"] == "***"
    assert out["list"][1]["y"] == 2


def test_redact_passthrough_for_scalars() -> None:
    assert _redact("hello") == "hello"
    assert _redact(42) == 42
    assert _redact(None) is None


# ---------------------------------------------------------------------
# _raise_if_upstream_error
# ---------------------------------------------------------------------


def test_raise_on_errortype_payload() -> None:
    with pytest.raises(SmartSlydrApiError):
        _raise_if_upstream_error("X", {"errorType": "TypeError", "errorMessage": "x"})


def test_raise_on_errormessage_only() -> None:
    with pytest.raises(SmartSlydrApiError):
        _raise_if_upstream_error("X", {"errorMessage": "boom"})


def test_no_raise_on_clean_payload() -> None:
    _raise_if_upstream_error("X", {"room_lists": []})


def test_no_raise_on_non_dict() -> None:
    _raise_if_upstream_error("X", None)
    _raise_if_upstream_error("X", "string")
    _raise_if_upstream_error("X", 42)


# ---------------------------------------------------------------------
# authenticate
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_success(session: ClientSession) -> None:
    with aioresponses() as m:
        m.post(
            f"{BASE}/auth",
            payload={"access_token": "abc", "refresh_token": "def"},
        )
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        await client.authenticate()
        assert client._access_token == "abc"
        assert client._refresh_token_value == "def"


@pytest.mark.asyncio
async def test_authenticate_401_raises_auth_error(session: ClientSession) -> None:
    with aioresponses() as m:
        m.post(f"{BASE}/auth", status=401, payload={"message": "bad"})
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        with pytest.raises(SmartSlydrAuthError):
            await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_403_raises_auth_error(session: ClientSession) -> None:
    with aioresponses() as m:
        m.post(f"{BASE}/auth", status=403, payload={"message": "forbidden"})
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        with pytest.raises(SmartSlydrAuthError):
            await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_missing_access_token_raises_api_error(
    session: ClientSession,
) -> None:
    with aioresponses() as m:
        m.post(f"{BASE}/auth", payload={"refresh_token": "only"})
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        with pytest.raises(SmartSlydrApiError):
            await client.authenticate()


# ---------------------------------------------------------------------
# get_devices
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_devices_returns_room_lists(session: ClientSession) -> None:
    with aioresponses() as m:
        m.post(f"{BASE}/auth", payload={"access_token": "tok"})
        m.get(
            f"{BASE}/devices",
            payload={"room_lists": [{"room_name": "Den", "device_list": []}]},
        )
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        rooms = await client.get_devices()
        assert rooms == [{"room_name": "Den", "device_list": []}]


@pytest.mark.asyncio
async def test_get_devices_missing_room_lists_raises(
    session: ClientSession,
) -> None:
    with aioresponses() as m:
        m.post(f"{BASE}/auth", payload={"access_token": "tok"})
        m.get(f"{BASE}/devices", payload={})
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        with pytest.raises(SmartSlydrApiError):
            await client.get_devices()


@pytest.mark.asyncio
async def test_get_devices_non_list_room_lists_raises(
    session: ClientSession,
) -> None:
    with aioresponses() as m:
        m.post(f"{BASE}/auth", payload={"access_token": "tok"})
        m.get(f"{BASE}/devices", payload={"room_lists": "oops"})
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        with pytest.raises(SmartSlydrApiError):
            await client.get_devices()


@pytest.mark.asyncio
async def test_get_devices_lambda_error_payload_raises(
    session: ClientSession,
) -> None:
    """A 200 response with errorType (Lambda exception) is still an error."""
    with aioresponses() as m:
        m.post(f"{BASE}/auth", payload={"access_token": "tok"})
        m.get(
            f"{BASE}/devices",
            payload={
                "errorType": "TypeError",
                "errorMessage": "boom",
            },
        )
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        with pytest.raises(SmartSlydrApiError):
            await client.get_devices()


# ---------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_devices_retries_5xx_then_succeeds(
    session: ClientSession, monkeypatch
) -> None:
    monkeypatch.setattr("asyncio.sleep", lambda *a, **kw: asyncio.sleep(0))
    with aioresponses() as m:
        m.post(f"{BASE}/auth", payload={"access_token": "tok"})
        m.get(f"{BASE}/devices", status=503)
        m.get(f"{BASE}/devices", status=502)
        m.get(f"{BASE}/devices", payload={"room_lists": []})
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        rooms = await client.get_devices()
        assert rooms == []


@pytest.mark.asyncio
async def test_get_devices_does_not_retry_4xx(
    session: ClientSession, monkeypatch
) -> None:
    import aiohttp

    monkeypatch.setattr("asyncio.sleep", lambda *a, **kw: asyncio.sleep(0))
    with aioresponses() as m:
        m.post(f"{BASE}/auth", payload={"access_token": "tok"})
        m.get(f"{BASE}/devices", status=404)
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        with pytest.raises(aiohttp.ClientResponseError):
            await client.get_devices()


@pytest.mark.asyncio
async def test_set_command_does_not_retry(
    session: ClientSession, monkeypatch
) -> None:
    """State-changing calls must not retry - could double-actuate the cover."""
    import aiohttp

    monkeypatch.setattr("asyncio.sleep", lambda *a, **kw: asyncio.sleep(0))
    with aioresponses() as m:
        m.post(f"{BASE}/auth", payload={"access_token": "tok"})
        m.post(f"{BASE}/operation", status=503)
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        with pytest.raises(aiohttp.ClientResponseError):
            await client.set_command([{"device_id": "x"}])


# ---------------------------------------------------------------------
# Concurrent token refresh
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_ensure_token_collapses_to_one_auth(
    session: ClientSession,
) -> None:
    """Two simultaneous _ensure_token calls must trigger only one /auth."""
    with aioresponses() as m:
        m.post(f"{BASE}/auth", payload={"access_token": "tok"})
        m.post(f"{BASE}/auth", payload={"access_token": "tok2"})  # would be hit on race
        client = SmartSlydrApiClient("u", "p", session, base_url=BASE)
        await asyncio.gather(client._ensure_token(), client._ensure_token())
        # aioresponses records all calls; we can count by inspecting requests.
        auth_calls = [
            call
            for (method, url), calls in m.requests.items()
            if method == "POST" and str(url).endswith("/auth")
            for call in calls
        ]
        assert len(auth_calls) == 1
