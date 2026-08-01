from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.performance_config import get_performance_config
from app.services.progress import report_progress
from app.services.replay_scenarios import get_replay_scenario, list_replay_scenarios
from app.services.ui_executions import submit_ui_execution
from app.web import _require_access, _require_mutation


router = APIRouter(tags=["interface-replay"])


class ReplayStartPayload(BaseModel):
    speed: float = Field(default=1.0, ge=0.1, le=5.0)


def _ticket_report(result: dict[str, Any]) -> str:
    analysis = dict(result.get("analysis") or {})
    facts = [str(item) for item in analysis.get("facts") or []]
    recommendations = [str(item) for item in analysis.get("recommendations") or []]
    rows = [
        f"Alvo: {result.get('display_target') or result.get('hostname') or result.get('target')}",
        f"Status: {analysis.get('status') or result.get('status')}",
        f"Confiança: {analysis.get('confidence') or result.get('confidence') or 0}%",
        "",
        "Resumo operacional:",
        str(analysis.get("summary") or "Sem resumo."),
        "",
        "Fatos comprovados:",
        *[f"- {item}" for item in facts],
        "",
        "Causa provável:",
        str(analysis.get("probable_cause") or "Não confirmada."),
        "",
        "Conclusão:",
        str(analysis.get("conclusion") or "Inconclusiva."),
        "",
        "Próximo passo mais seguro:",
        str(analysis.get("next_safe_step") or (recommendations[0] if recommendations else "Revisar as evidências.")),
    ]
    return "\n".join(rows)


def _prepare_replay_result(scenario: dict[str, Any]) -> dict[str, Any]:
    result = dict(scenario.get("result") or {})
    analysis = dict(result.get("analysis") or {})
    result["analysis"] = analysis
    result["replay"] = {
        "enabled": True,
        "scenario_id": scenario.get("id"),
        "title": scenario.get("title"),
        "sanitized": True,
        "connected_to_real_target": False,
        "warning": "Demonstração local: nenhum SSH, banco, cliente ou credencial real foi acessado.",
    }
    result["provider_selection"] = {
        "provider": "replay",
        "label": "Replay local",
        "model": "cenário sanitizado",
        "reason": "modo de demonstração WSL",
    }
    result["selected_provider"] = "replay"
    result["selected_model"] = "cenário sanitizado"
    result["ticket_report"] = _ticket_report(result)
    result["operator_report"] = result["ticket_report"]
    result["duration_ms"] = int(float(scenario.get("duration_seconds") or 0) * 1000)
    return result


@router.get("/ui/api/replay/scenarios")
def replay_scenarios(request: Request) -> dict[str, Any]:
    _require_access(request)
    if not get_performance_config().replay_enabled:
        raise HTTPException(status_code=404, detail="modo replay desabilitado")
    return {
        "enabled": True,
        "safe": True,
        "items": list_replay_scenarios(),
        "notice": "Os cenários são sanitizados e não abrem conexões reais.",
    }


@router.post("/ui/api/replay/{scenario_id}")
def start_replay(
    scenario_id: str,
    payload: ReplayStartPayload,
    request: Request,
) -> dict[str, Any]:
    _require_mutation(request)
    if not get_performance_config().replay_enabled:
        raise HTTPException(status_code=404, detail="modo replay desabilitado")
    scenario = get_replay_scenario(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail="cenário de replay não encontrado")

    def operation() -> dict[str, Any]:
        report_progress(
            "execution_started",
            status="completed",
            detail="Replay sanitizado iniciado. Nenhuma conexão real será aberta.",
            percent=4,
            replay=True,
        )
        for raw_event in scenario.get("events") or []:
            event = dict(raw_event)
            delay = max(0.0, float(event.pop("delay", 0.0))) / float(payload.speed)
            if delay:
                time.sleep(delay)
            stage = str(event.pop("stage", "evidence_analysis"))
            status = str(event.pop("status", "running"))
            detail = str(event.pop("detail", ""))
            report_progress(
                stage,
                status=status,
                detail=detail,
                replay=True,
                scenario_id=scenario_id,
                **event,
            )
        report_progress(
            "result_persistence",
            status="completed",
            detail="Resultado sanitizado preparado para visualização local.",
            percent=98,
            replay=True,
        )
        return _prepare_replay_result(scenario)

    return submit_ui_execution(
        operation,
        target=str(scenario.get("target") or "replay"),
        objective=str(scenario.get("objective") or scenario.get("title") or "Replay"),
        provider="replay",
        model="cenário sanitizado",
        execution_mode="replay",
    )
