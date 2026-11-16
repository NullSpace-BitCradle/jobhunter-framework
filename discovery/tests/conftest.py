"""Shared pytest configuration.

The suite is fully mocked. This guard makes that a hard rule: any test (or code
under test) that tries to open a real network connection fails immediately
instead of silently hitting a live job site or API, locally and in CI.
Set ALLOW_NETWORK_IN_TESTS=1 to bypass it deliberately.
"""
import os
import socket

import pytest


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    if os.environ.get("ALLOW_NETWORK_IN_TESTS") == "1":
        return

    def _blocked(self, *args, **kwargs):
        raise RuntimeError(
            "Live network access is disabled in the test suite "
            "(set ALLOW_NETWORK_IN_TESTS=1 to override)."
        )

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
