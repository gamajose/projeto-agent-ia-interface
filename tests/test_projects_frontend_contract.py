from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_frontend_never_reads_length_from_optional_api_arrays_directly() -> None:
    source = (PROJECT_ROOT / "app/ui/projects.js").read_text(encoding="utf-8")

    assert "normalizeExecutionResponse" in source
    assert "jobs: asArray(response.jobs)" in source
    assert "executions: asArray(response.executions)" in source
    assert "errors: asArray(response.errors)" in source
    assert "const response = renderExecution(rawResponse);" in source


def test_project_frontend_keeps_target_ip_visible_while_discovery_is_pending() -> None:
    source = (PROJECT_ROOT / "app/ui/projects.js").read_text(encoding="utf-8")

    assert "if (!targetFacts.vpn_ip && plan?.target?.vpn_ip) targetFacts.vpn_ip = plan.target.vpn_ip;" in source
