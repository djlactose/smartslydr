"""Test fixtures for the smartslydr integration."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from unittest.mock import patch

import aiohttp
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations,
) -> Generator[None, None, None]:
    """Allow HA to load custom_components/smartslydr in tests.

    enable_custom_integrations is provided by
    pytest-homeassistant-custom-component; activating it via autouse
    means every test in this directory gets it without ceremony.
    """
    yield


@pytest.fixture(scope="session", autouse=True)
def mock_zeroconf_resolver() -> Generator[None, None, None]:
    """Override the upstream HA-test fixture that requires aiodns.

    pytest-homeassistant-custom-component's mock_zeroconf_resolver
    instantiates aiohttp.resolver.AsyncResolver(), which raises
    RuntimeError("Resolver requires aiodns library") when aiodns is
    missing - and CI strips aiodns to dodge an unrelated pycares
    version mismatch (see .github/workflows/validate.yml). SmartSlydr
    is a cloud_polling integration with no zeroconf or local
    discovery in its path, so a no-op replacement is safe.

    Pytest's fixture-override rule: a fixture defined in a closer
    conftest with the same name and scope wins over one from an
    installed plugin. Both must be session-scoped autouse for the
    override to apply.
    """
    yield


@pytest.fixture(autouse=True)
async def _patched_aiohttp_clientsession() -> AsyncGenerator[None, None]:
    """Bypass HA's zeroconf-based client-session construction.

    homeassistant.helpers.aiohttp_client.async_get_clientsession constructs
    a ClientSession whose connector resolves DNS via HA's zeroconf
    integration. zeroconf.async_get_async_zeroconf creates a UDP socket
    (port 5353) - which pytest-socket blocks in tests. The integration's
    async_setup_entry calls async_get_clientsession, so any HA-fixture
    test that exercises setup hits SocketBlockedError before the test
    body runs.

    Patch async_get_clientsession to return a plain aiohttp.ClientSession.
    Construction doesn't create sockets at init time; aioresponses
    intercepts every actual HTTP call, so no real DNS or networking
    happens during tests.

    Patched in both modules: the helper exposes the function from
    aiohttp_client, and the integration imports it via that path.
    """
    sessions: list[aiohttp.ClientSession] = []

    def _fake_get_clientsession(*_args, **_kwargs) -> aiohttp.ClientSession:
        session = aiohttp.ClientSession()
        sessions.append(session)
        return session

    with patch(
        "homeassistant.helpers.aiohttp_client.async_get_clientsession",
        side_effect=_fake_get_clientsession,
    ), patch(
        "custom_components.smartslydr.async_get_clientsession",
        side_effect=_fake_get_clientsession,
    ), patch(
        "custom_components.smartslydr.config_flow.async_get_clientsession",
        side_effect=_fake_get_clientsession,
    ):
        yield

    for session in sessions:
        if not session.closed:
            await session.close()
