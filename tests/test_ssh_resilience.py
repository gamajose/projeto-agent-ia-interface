from __future__ import annotations

from pathlib import Path

import paramiko

from app.services.ssh_resilience import _retryable_banner_error


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_protocol_banner_error_is_retryable() -> None:
    assert _retryable_banner_error(paramiko.SSHException("Error reading SSH protocol banner")) is True
    assert _retryable_banner_error(EOFError("fim prematuro")) is True
    assert _retryable_banner_error(paramiko.AuthenticationException("Authentication failed")) is False


def test_resilience_extends_only_handshake_windows_and_retries_transient_banner() -> None:
    source = (PROJECT_ROOT / "app" / "services" / "ssh_resilience.py").read_text(encoding="utf-8")
    assert 'args["banner_timeout"] = max(45' in source
    assert 'args["auth_timeout"] = max(30' in source
    assert "delays = (0.0, 1.25, 3.0)" in source
    assert "_retryable_banner_error" in source


def test_web_and_worker_install_same_ssh_resilience() -> None:
    web = (PROJECT_ROOT / "app" / "web_main.py").read_text(encoding="utf-8")
    worker = (PROJECT_ROOT / "app" / "worker.py").read_text(encoding="utf-8")
    assert "install_ssh_resilience()" in web
    assert "install_ssh_resilience()" in worker
