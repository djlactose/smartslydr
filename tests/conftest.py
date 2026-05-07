"""Test fixtures for the smartslydr integration."""

from __future__ import annotations

from collections.abc import Generator

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
