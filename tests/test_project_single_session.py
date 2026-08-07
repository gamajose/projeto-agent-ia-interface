from __future__ import annotations

from types import SimpleNamespace

from app.services.ansible_project import _group_steps
from app import web_projects


def test_ansible_groups_all_steps_for_same_target_in_one_batch() -> None:
    steps = [
        {"reference": "172.27.233.45", "environment": "production", "command": "uname -a"},
        {"reference": "172.27.233.45", "environment": "production", "command": "free -h"},
        {"reference": "172.27.233.99", "environment": "standby", "command": "df -h"},
    ]

    grouped = _group_steps(steps)

    assert len(grouped) == 2
    assert len(grouped[("172.27.233.45", "production")]) == 2
    assert len(grouped[("172.27.233.99", "standby")]) == 1


def test_queue_project_defers_web_discovery_and_enqueues_macro_only_worker(monkeypatch) -> None:
    calls: dict[str, object] = {}
    settings = SimpleNamespace(
        agent_execution_mode="queue",
        agent_ansible_enabled=True,
        ssh_bastion_host="10.17.181.1",
    )

    monkeypatch.setattr(web_projects, "_require_mutation", lambda request: None)
    monkeypatch.setattr(web_projects, "get_settings", lambda: settings)

    def fake_plan(payload, *, discover=True):
        calls["discover"] = discover
        return {
            "plan_id": "plan-1",
            "scenario": "linux_prod_std",
            "scenario_label": "Servidor Linux — Produção/Standby",
            "target": {"vpn_ip": "172.27.233.45"},
            "discovery": {"source": "deferred", "target": {"vpn_ip": "172.27.233.45"}},
            "warnings": [],
            "summary": {"automatic_read_only_steps": 2},
            "safety": {},
            "ticket_macro": "macro",
            "groups": [
                {
                    "label": "Produção",
                    "target": "172.27.233.45",
                    "kind": "remote",
                    "items": [
                        {"id": "so", "kind": "command", "automated": True, "command": "uname -a", "title": "SO", "purpose": "SO", "evidence": "print"},
                        {"id": "ram", "kind": "command", "automated": True, "command": "free -h", "title": "RAM", "purpose": "RAM", "evidence": "print"},
                    ],
                }
            ],
            "execution_targets": [
                {
                    "reference": "172.27.233.45",
                    "label": "Produção",
                    "environment": "production",
                    "playbook_id": "project-linux-prod-std",
                    "objective": "validar",
                    "ssh_port": 22,
                }
            ],
        }

    monkeypatch.setattr(web_projects, "_plan", fake_plan)

    def fake_enqueue(reference, objective, **kwargs):
        calls["metadata"] = kwargs["metadata"]
        calls["playbook_mode"] = kwargs["playbook_mode"]
        return {"job_id": "job-1", "status": "queued"}

    monkeypatch.setattr(web_projects, "enqueue_investigation", fake_enqueue)

    payload = web_projects.ProjectPayload(
        scenario="linux_prod_std",
        role="production",
        target_vpn_ip="172.27.233.45",
    )
    result = web_projects.start_project(payload, request=object())

    assert calls["discover"] is False
    assert result["execution_mode"] == "queue"
    assert result["jobs"][0]["job_id"] == "job-1"
    assert len(calls["metadata"]["ansible_steps"]) == 2
    assert calls["metadata"]["project_macro_only"] is True
    assert calls["metadata"]["project_blueprint"][0]["id"] == "so"
    assert calls["playbook_mode"] == "none"
    assert "investigação de causa raiz" in result["message"]
