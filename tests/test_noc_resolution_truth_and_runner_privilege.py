from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

from app.services import noc_selected_status


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _settings():
    return SimpleNamespace(
        redis_url="redis://127.0.0.1:6379/1",
        noc_incident_prefix="agent-ia:noc",
        agent_queue_name="agent-ia:jobs",
    )


def _item() -> dict:
    return {
        "job_id": "job-1",
        "incident_id": "incident-1",
        "host": "sma-dbstandby",
        "service": "Systemd Socket Summary",
        "created_at": "2026-08-14T18:00:00+00:00",
    }


def test_completed_worker_without_checkmk_confirmation_is_not_resolved(monkeypatch) -> None:
    monkeypatch.setattr(
        noc_selected_status,
        "get_job",
        lambda *args, **kwargs: {
            "job_id": "job-1",
            "status": "completed",
            "percent": 100,
            "current_phase": {
                "stage": "snapshot",
                "detail": "Comando no host interno finalizado com código 0",
            },
        },
    )
    monkeypatch.setattr(noc_selected_status, "_incident", lambda *args, **kwargs: {})

    view = noc_selected_status._job_view(_item(), _settings())

    assert view["status"] == "failed"
    assert view["resolution_status"] == "unverified"
    assert "não recebeu confirmação de OK do Checkmk" in view["detail"]


def test_watching_incident_remains_running_until_checkmk_is_green(monkeypatch) -> None:
    monkeypatch.setattr(
        noc_selected_status,
        "get_job",
        lambda *args, **kwargs: {"job_id": "job-1", "status": "completed", "percent": 100},
    )
    monkeypatch.setattr(
        noc_selected_status,
        "_incident",
        lambda *args, **kwargs: {
            "id": "incident-1",
            "status": "watching",
            "manual_correction_requested": True,
        },
    )

    view = noc_selected_status._job_view(_item(), _settings())

    assert view["status"] == "running"
    assert view["resolution_status"] == "watching"
    assert view["percent"] == 100
    assert "Aguardando o Checkmk confirmar" in view["detail"]


def test_only_resolved_incident_can_be_presented_as_completed(monkeypatch) -> None:
    monkeypatch.setattr(
        noc_selected_status,
        "get_job",
        lambda *args, **kwargs: {"job_id": "job-1", "status": "completed", "percent": 100},
    )
    monkeypatch.setattr(
        noc_selected_status,
        "_incident",
        lambda *args, **kwargs: {"id": "incident-1", "status": "resolved"},
    )

    view = noc_selected_status._job_view(_item(), _settings())

    assert view["status"] == "completed"
    assert view["resolution_status"] == "resolved"
    assert view["detail"] == "Problema corrigido e Checkmk confirmou o sensor em OK."


def test_runner_privilege_bootstrap_is_syntax_valid_and_restricted() -> None:
    path = PROJECT_ROOT / "deploy" / "scripts" / "install_runner_privilege.sh"
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    text = path.read_text(encoding="utf-8")
    assert 'readonly WEB="agent-ia-web.service"' in text
    assert 'readonly WORKER="agent-ia-worker.service"' in text
    assert 'stop|start|enable|disable' in text
    assert "NOPASSWD: AGENT_IA_LEGACY_SYSTEMCTL" in text
    assert "probe" in text
