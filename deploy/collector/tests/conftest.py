"""Test fixtures and strict zero-network socket guard for deploy/collector tests."""

from __future__ import annotations

import socket
import urllib.request
from pathlib import Path

import pytest

from collector_stack.privacy import AuthorHasher

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def forbid_all_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee that no test in deploy/collector ever makes a live network connection."""

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"Live network call forbidden in collector tests: args={args!r}")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(urllib.request, "urlopen", _blocked)


@pytest.fixture()
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture()
def test_hasher(tmp_path: Path) -> AuthorHasher:
    salt_file = tmp_path / "author_hmac.salt"
    salt_file.write_bytes(b"host-local-test-salt-9f8e7d6c5b4a3210-never-committed")
    return AuthorHasher.from_file(salt_file)
