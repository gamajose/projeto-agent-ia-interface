from __future__ import annotations

import hmac
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.core.policies import EnvironmentType
from app.core.settings import get_settings
from app.db.base import ensure_database_schema
from app.services.approved_execution import execute_approved_investigation
from app.services.jobs import enqueue_investigation, get_job
from app.services.noc_communications import build_incident_communications, publish_incident_communications
from app.services.noc_incidents import (
    attach_job,
    incident_objective,
    normalize_checkmk_state,
    register_checkmk_event,
)
from app.services.noc_supervisor import postprocess_investigation_result
from app.services.noc_targeting import resolve_noc_target
from app.services.persistence import get_investigation, operational_metrics
from app.services.replay import replay_investigation
from app.services.runner import run_target
from app.services.secrets import get_secret, secret_backend_status


def _application_version() -> str:
    """Usa a mesma versão empacotada no pyproject para API e smoke test."""
    try:
        return package_version("agent-ia-infra")
    except PackageNotFoundError:
        return "0.0.0-dev"


app = FastAPI(title="Agent IA Infra", version=_application_version())


class CheckmkWebhookPayload(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    service: str = Field(min_length=1, max_length=255)
    state: str = Field(min_length=1, max_length=30)
    output: str = Field(default="", max_length=12000)
    site: str | None = Field(default=None, max_length=255)
    environment: EnvironmentType = EnvironmentType.UNKNOWN
    auto_correct: bool = False
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    # Opcional para notificações em que o Checkmk já conhece o IP VPN/TAP.
    # Quando ausente, o Supervisor resolve afetado x monitor pelo inventário.
    vpn_ip: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=255)


class ReplayPayload(BaseModel):
    provider: str | None = None


class ApprovalPayload(BaseModel):
    token: str = Field(min_length=20)
    requested_by: str | None = Field(default=None, max_length=255)


def _require_token(supplied: str | None, expected: str | None, name: str) -> None:
    if not expected:
        raise HTTPException(status_code=503, detail=f"{name} não configurado")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="token inválido")


def _admin_token() -> str | None:
    settings = get_settings()
    return get_secret("AGENT_API_TOKEN", settings.agent_api_token, settings=settings)


def _routing_environment(routing: dict[str, Any], fallback: EnvironmentType) -> EnvironmentType:
    try:
        routed = EnvironmentType(str(routing.get("environment") or EnvironmentType.UNKNOWN.value))
    except ValueError:
        routed = EnvironmentType.UNKNOWN
    return routed if routed != EnvironmentType.UNKNOWN else fallback


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "version": app.version,
        "default_mode": settings.agent_default_mode,
        "execution_mode": settings.agent_execution_mode,
        "worker_pool": settings.agent_worker_name,
        "strict_host_key_checking": settings.ssh_strict_host_key_checking,
        "review_required_for_corrections": settings.ai_reviewer_required_for_corrections,
        "noc_incident_manager": settings.noc_incident_enabled,
        "noc_auto_investigate": settings.noc_auto_investigate,
        "noc_autonomy_level": settings.noc_autonomy_level,
        "noc_self_heal": settings.noc_self_heal_enabled,
        "noc_checkmk_watcher": True,
        "secret_backend": secret_backend_status(settings),
    }


@app.post("/webhooks/checkmk")
def checkmk_webhook(
    payload: CheckmkWebhookPayload,
    x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
) -> dict[str, Any]:
    settings = get_settings()
    webhook_token = get_secret("CHECKMK_WEBHOOK_TOKEN", settings.checkmk_webhook_token, settings=settings)
    _require_token(x_agent_token, webhook_token, "CHECKMK_WEBHOOK_TOKEN")
    ensure_database_schema()

    routing = resolve_noc_target(
        checkmk_host=payload.host,
        service=payload.service,
        output=payload.output,
        explicit_target=payload.vpn_ip or payload.address,
        requested_environment=payload.environment,
    )
    target_reference = str(routing.get("reference") or payload.host)
    target_environment = _routing_environment(routing, payload.environment)
    target_port = payload.ssh_port or routing.get("ssh_port")

    normalized_state = normalize_checkmk_state(payload.state)
    incident_event: dict[str, Any] | None = None
    incident_error: str | None = None
    if settings.noc_incident_enabled:
        try:
            incident_event = register_checkmk_event(
                host=payload.host,
                service=payload.service,
                state=payload.state,
                output=payload.output,
                site=payload.site,
                environment=target_environment.value,
                requested_auto_correct=payload.auto_correct,
                settings=settings,
            )
        except Exception as exc:
            # A camada NOC nunca pode derrubar o webhook de troubleshooting.
            incident_error = f"{type(exc).__name__}: {exc}"

    if incident_event and not incident_event.get("should_investigate", True):
        incident = dict(incident_event.get("incident") or {})
        communication = None
        if incident_event.get("action") == "resolved" and incident:
            try:
                communication = build_incident_communications(incident, state="resolved", settings=settings)
                communication["delivery"] = publish_incident_communications(
                    incident,
                    communication,
                    phase="resolved",
                    settings=settings,
                )
            except Exception as exc:
                communication = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
        return {
            "incident_action": incident_event.get("action"),
            "incident": incident or None,
            "state": normalized_state,
            "routing": routing,
            "investigation_started": False,
            "communication": communication,
        }

    # Recovery sem incidente aberto também não cria troubleshooting novo.
    if normalized_state["kind"] == "ok":
        return {
            "incident_action": "recovery_degraded" if incident_error else "recovery_without_open_incident",
            "incident": incident_event.get("incident") if incident_event else None,
            "state": normalized_state,
            "routing": routing,
            "investigation_started": False,
            "noc_error": incident_error,
        }

    # Com o Supervisor ativo, o webhook nunca executa correção diretamente.
    # Primeiro coleta/RCA/reviewer; somente o L4 decide se a ação é elegível.
    legacy_auto_correct = payload.auto_correct and settings.checkmk_webhook_auto_correct
    mode = "propose" if settings.noc_incident_enabled else ("correct" if legacy_auto_correct else "propose")
    incident = dict((incident_event or {}).get("incident") or {})
    objective = incident_objective(incident) if incident else (
        f"Alerta Checkmk no serviço '{payload.service}', estado {payload.state}. "
        f"Site: {payload.site or 'não informado'}. Saída do alerta: {payload.output}"
    )
    incident_id = str(incident.get("id") or "") or None
    metadata = {
        "source": "checkmk",
        "site": payload.site,
        "service": payload.service,
        "state": payload.state,
        "checkmk_host": payload.host,
        "noc_incident_id": incident_id,
        "noc_fingerprint": incident.get("fingerprint"),
        "noc_flapping": bool(incident.get("flapping")),
        "noc_autonomy_level": settings.noc_autonomy_level,
        "noc_routing": routing,
    }

    if settings.agent_execution_mode.strip().casefold() == "queue":
        try:
            queued = enqueue_investigation(
                target_reference,
                objective,
                environment=target_environment,
                mode=mode,
                approve=False,
                ssh_port=int(target_port) if target_port else None,
                metadata=metadata,
                settings=settings,
            )
            if incident_id:
                try:
                    incident = attach_job(incident_id, str(queued["job_id"]), settings=settings) or incident
                except Exception as exc:
                    incident_error = f"{type(exc).__name__}: {exc}"
            return {
                **queued,
                "incident_action": (incident_event or {}).get("action") or "degraded",
                "incident": incident or None,
                "routing": routing,
                "investigation_started": True,
                "noc_error": incident_error,
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"fila indisponível: {type(exc).__name__}: {exc}") from exc

    try:
        result = run_target(
            target_reference,
            objective,
            environment=target_environment,
            mode=mode,
            approve=mode == "correct",
            ssh_port=int(target_port) if target_port else None,
            settings=settings,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    if incident_id:
        try:
            incident = postprocess_investigation_result(incident_id, result, settings=settings) or incident
        except Exception as exc:
            incident_error = f"{type(exc).__name__}: {exc}"

    analysis = result.get("analysis") or {}
    return {
        "investigation_id": result.get("investigation_id"),
        "host": result.get("hostname"),
        "mode": mode,
        "environment": result.get("environment_classification"),
        "routing": routing,
        "playbook": result.get("playbook"),
        "status": analysis.get("status"),
        "confidence": analysis.get("confidence"),
        "probable_cause": analysis.get("probable_cause"),
        "conclusion": analysis.get("conclusion"),
        "ticket_report": analysis.get("ticket_report"),
        "proposed_actions": analysis.get("proposed_actions") or [],
        "review": result.get("review"),
        "corrections": result.get("corrections") or [],
        "approval_token": result.get("approval_token"),
        "incident_action": (incident_event or {}).get("action") or "degraded",
        "incident": incident or None,
        "investigation_started": True,
        "noc_error": incident_error,
    }


@app.get("/api/jobs/{job_id}")
def job_detail(
    job_id: str,
    x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
) -> dict[str, Any]:
    _require_token(x_agent_token, _admin_token(), "AGENT_API_TOKEN")
    try:
        result = get_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"fila indisponível: {type(exc).__name__}: {exc}") from exc
    if not result:
        raise HTTPException(status_code=404, detail="job não encontrado ou expirado")
    return result


@app.get("/api/investigations/{investigation_id}")
def investigation_detail(
    investigation_id: str,
    x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
) -> dict[str, Any]:
    _require_token(x_agent_token, _admin_token(), "AGENT_API_TOKEN")
    ensure_database_schema()
    result = get_investigation(investigation_id, include_evidence=True)
    if not result:
        raise HTTPException(status_code=404, detail="investigação não encontrada")
    return result


@app.post("/api/investigations/{investigation_id}/replay")
def investigation_replay(
    investigation_id: str,
    payload: ReplayPayload,
    x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_token(x_agent_token, _admin_token(), "AGENT_API_TOKEN")
    ensure_database_schema()
    try:
        return replay_investigation(investigation_id, provider_name=payload.provider, settings=settings)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc


@app.post("/api/investigations/{investigation_id}/approve")
def investigation_approve(
    investigation_id: str,
    payload: ApprovalPayload,
    x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
) -> dict[str, Any]:
    settings = get_settings()
    _require_token(x_agent_token, _admin_token(), "AGENT_API_TOKEN")
    ensure_database_schema()
    try:
        return execute_approved_investigation(
            investigation_id,
            payload.token,
            requested_by=payload.requested_by,
            settings=settings,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}") from exc


@app.get("/api/metrics")
def metrics_json(
    x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
) -> dict[str, Any]:
    _require_token(x_agent_token, _admin_token(), "AGENT_API_TOKEN")
    ensure_database_schema()
    return operational_metrics()


@app.get("/metrics", response_class=PlainTextResponse)
def metrics_prometheus(
    x_agent_token: str | None = Header(default=None, alias="X-Agent-Token"),
) -> str:
    _require_token(x_agent_token, _admin_token(), "AGENT_API_TOKEN")
    ensure_database_schema()
    metrics = operational_metrics()
    lines = [
        "# HELP agent_investigations_total Total de investigações persistidas.",
        "# TYPE agent_investigations_total gauge",
        f"agent_investigations_total {metrics['investigations_total']}",
        "# HELP agent_investigation_duration_ms_average Duração média das investigações.",
        "# TYPE agent_investigation_duration_ms_average gauge",
        f"agent_investigation_duration_ms_average {metrics['average_duration_ms']}",
    ]
    for status, count in metrics.get("by_status", {}).items():
        lines.append(f'agent_investigations_by_status{{status="{status}"}} {count}')
    for mode, count in metrics.get("by_mode", {}).items():
        lines.append(f'agent_investigations_by_mode{{mode="{mode}"}} {count}')
    for status, count in metrics.get("approval_executions", {}).items():
        lines.append(f'agent_approval_executions{{status="{status}"}} {count}')
    return "\n".join(lines) + "\n"
