from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.db.base import ensure_database_schema
from app.services.ai_providers import omniroute_route_options
from app.services.application_health import application_health
from app.services.approved_execution import execute_approved_investigation
from app.services.jobs import enqueue_investigation, get_job
from app.services.persistence import get_investigation, operational_metrics
from app.services.playbooks import list_playbooks
from app.services.provider_preflight import ProviderPreflight, preflight_all, preflight_provider
from app.services.provider_router import automatic_provider_order
from app.services.runner import run_target
from app.services.ui_queries import list_hosts, list_investigations


UI_DIR = Path(__file__).resolve().parent / "ui"
router = APIRouter(tags=["interface"])
ProviderName = Literal["auto", "gemini", "groq", "openrouter", "ollama", "omniroute"]


class InvestigationPayload(BaseModel):
    target: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=3, max_length=12000)
    environment: EnvironmentType = EnvironmentType.UNKNOWN
    mode: Literal["investigate", "propose", "correct"] = "propose"
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    provider: ProviderName | None = None
    model: str | None = Field(default=None, max_length=255)
    playbook_mode: Literal["auto", "manual", "none"] = "auto"
    playbook_id: str | None = Field(default=None, max_length=255)


class ApprovalPayload(BaseModel):
    token: str = Field(min_length=20, max_length=4096)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on", "sim"}


def _allowed_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    raw = os.getenv("AGENT_UI_ALLOWED_NETWORKS", "127.0.0.1/32,::1/128")
    networks = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise RuntimeError(f"rede inválida em AGENT_UI_ALLOWED_NETWORKS: {value}") from exc
    return tuple(networks)


def _is_allowed_client(host: str | None, networks: tuple[ipaddress._BaseNetwork, ...] | None = None) -> bool:
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in (networks or _allowed_networks()))


def _require_access(request: Request) -> None:
    if not _env_bool("AGENT_UI_ENABLED", True):
        raise HTTPException(status_code=404, detail="interface desabilitada")
    client_host = request.client.host if request.client else None
    if not _is_allowed_client(client_host):
        raise HTTPException(status_code=403, detail="origem não autorizada para a interface")


def _require_mutation(request: Request) -> None:
    _require_access(request)
    if request.headers.get("X-Agent-UI") != "1":
        raise HTTPException(status_code=403, detail="requisição da interface não reconhecida")
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if origin and host and urlparse(origin).netloc != host:
        raise HTTPException(status_code=403, detail="origem da requisição não autorizada")


def _operator_name() -> str:
    return os.getenv("AGENT_UI_OPERATOR_NAME", "Operador Agent IA").strip() or "Operador Agent IA"


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    return {
        "investigation_id": result.get("investigation_id"),
        "hostname": result.get("hostname"),
        "target": result.get("target"),
        "profile": result.get("profile"),
        "environment_classification": result.get("environment_classification"),
        "playbook": result.get("playbook"),
        "analysis": analysis,
        "review": result.get("review"),
        "corrections": result.get("corrections") or [],
        "approval_token": result.get("approval_token"),
        "duration_ms": result.get("duration_ms"),
        "evidence_count": len(result.get("evidence") or []),
        "history": result.get("history") or [],
        "similar_history": result.get("similar_history") or [],
        "ai_diagnostics": result.get("ai_diagnostics") or analysis.get("ai_diagnostics") or [],
        "selected_provider": result.get("selected_provider"),
        "selected_model": result.get("selected_model"),
        "provider_selection": result.get("provider_selection"),
        "automation": result.get("automation"),
    }


def _provider_options(result: ProviderPreflight, settings: Settings) -> list[dict[str, Any]]:
    if result.provider == "omniroute":
        configured = omniroute_route_options(settings)
        valid = set(result.valid_routes)
        rows = [
            {
                "value": route.model,
                "label": route.label,
                "default": route.is_default,
                "available": not valid or route.model in valid,
            }
            for route in configured
        ]
        if result.model and result.model not in {item["value"] for item in rows}:
            rows.insert(0, {"value": result.model, "label": result.model, "default": True, "available": result.selectable})
        return rows
    if result.model:
        return [{"value": result.model, "label": result.model, "default": True, "available": result.selectable}]
    return []


def _default_provider(settings: Settings) -> str:
    if settings.agent_autopilot_enabled and settings.agent_autopilot_default:
        return "auto"
    return str(settings.ai_provider or "gemini").strip().lower()


def _validate_selection(payload: InvestigationPayload, settings: Settings) -> tuple[str, str | None, str]:
    if payload.playbook_mode == "manual" and not (payload.playbook_id or "").strip():
        raise HTTPException(status_code=422, detail="selecione um playbook no modo manual")

    provider = (payload.provider or _default_provider(settings)).strip().lower()
    model = (payload.model or "").strip() or None

    if provider == "auto":
        if not settings.agent_autopilot_enabled:
            raise HTTPException(status_code=422, detail="autopilot desabilitado na configuração")
        quick_rows = preflight_all(settings, quick=True)
        if not any(item.selectable for item in quick_rows):
            raise HTTPException(
                status_code=422,
                detail="nenhuma IA está disponível para seleção automática; consulte o painel Saúde",
            )
        return "auto", None, "propose"

    result = preflight_provider(provider, settings, model, quick=False)
    if not result.selectable:
        raise HTTPException(
            status_code=422,
            detail=f"{result.label} não pode iniciar a investigação: {result.detail}",
        )

    # O modo corrigir continua sendo uma proposta revisada, seguida da aprovação
    # humana separada. A abertura da investigação nunca executa alterações.
    effective_mode = "propose" if payload.mode == "correct" else payload.mode
    return provider, model or result.model or None, effective_mode


@router.get("/ui", include_in_schema=False)
@router.get("/ui/", include_in_schema=False)
def interface(request: Request) -> FileResponse:
    _require_access(request)
    return FileResponse(UI_DIR / "index.html")


@router.get("/ui/api/session")
def ui_session(request: Request) -> dict[str, Any]:
    _require_access(request)
    settings = get_settings()
    return {
        "operator": _operator_name(),
        "execution_mode": settings.agent_execution_mode,
        "default_mode": settings.agent_default_mode,
        "default_provider": _default_provider(settings),
        "autopilot_enabled": settings.agent_autopilot_enabled,
        "autopilot_default": settings.agent_autopilot_default,
        "reviewer_provider": settings.ai_reviewer_provider,
        "review_required": settings.ai_reviewer_required_for_corrections,
        "safe_rules": [
            "Produção e standby recebem investigação e proposta, nunca correção automática.",
            "Reboot, shutdown, bancos de clientes e ciclo de vida de containers permanecem bloqueados.",
            "Toda ação corretiva exige playbook permitido, segunda IA e aprovação humana.",
        ],
    }


@router.get("/ui/api/ai/providers")
def ai_providers(request: Request) -> dict[str, Any]:
    _require_access(request)
    settings = get_settings()
    diagnostics = preflight_all(settings, quick=True)
    items: list[dict[str, Any]] = []

    selectable = [item for item in diagnostics if item.selectable]
    if settings.agent_autopilot_enabled:
        order = automatic_provider_order(settings)
        items.append(
            {
                "provider": "auto",
                "label": "Automático — melhor IA disponível",
                "state": "available" if selectable else "unavailable",
                "state_label": "disponível" if selectable else "indisponível",
                "model": "",
                "detail": (
                    "Valida e seleciona automaticamente a primeira IA saudável. "
                    f"Prioridade: {', '.join(order)}."
                    if selectable
                    else "Nenhuma IA passou no catálogo rápido."
                ),
                "latency_ms": None,
                "selectable": bool(selectable),
                "valid_routes": [],
                "invalid_routes": [],
                "options": [],
                "automatic": True,
            }
        )

    for result in diagnostics:
        items.append(
            {
                **result.model_dump(mode="json"),
                "state_label": result.state_label,
                "options": _provider_options(result, settings),
                "automatic": False,
            }
        )
    return {
        "default_provider": _default_provider(settings),
        "reviewer_provider": settings.ai_reviewer_provider,
        "items": items,
    }


@router.get("/ui/api/health")
def health(request: Request) -> dict[str, Any]:
    _require_access(request)
    return application_health(get_settings())


@router.get("/ui/api/dashboard")
def dashboard(request: Request) -> dict[str, Any]:
    _require_access(request)
    ensure_database_schema()
    return {
        "metrics": operational_metrics(),
        "recent": list_investigations(limit=8),
        "hosts": list_hosts(limit=6),
    }


@router.get("/ui/api/investigations")
def investigations(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None, max_length=30),
    mode: str | None = Query(default=None, max_length=20),
    environment: str | None = Query(default=None, max_length=30),
) -> dict[str, Any]:
    _require_access(request)
    ensure_database_schema()
    return list_investigations(
        limit=limit,
        offset=offset,
        query=q,
        status=status,
        mode=mode,
        environment=environment,
    )


@router.post("/ui/api/investigations")
def create_investigation(payload: InvestigationPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    settings = get_settings()
    ensure_database_schema()
    provider, model, effective_mode = _validate_selection(payload, settings)

    common = {
        "environment": payload.environment,
        "mode": effective_mode,
        "approve": False,
        "ssh_port": payload.ssh_port,
        "provider_name": provider,
        "model_name": model,
        "playbook_mode": "auto" if provider == "auto" else payload.playbook_mode,
        "playbook_id": None if provider == "auto" else (payload.playbook_id or "").strip() or None,
        "settings": settings,
    }

    if settings.agent_execution_mode.strip().casefold() == "queue":
        try:
            return enqueue_investigation(
                payload.target.strip(),
                payload.objective.strip(),
                metadata={
                    "source": "web_ui",
                    "operator": _operator_name(),
                    "requested_mode": payload.mode,
                    "autopilot": provider == "auto",
                },
                **common,
            )
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"fila indisponível: {type(exc).__name__}: {exc}") from exc

    try:
        result = run_target(
            payload.target.strip(),
            payload.objective.strip(),
            **common,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc
    compact = _compact_result(result)
    compact["requested_mode"] = payload.mode
    compact["requested_provider"] = provider
    compact["selected_provider"] = result.get("selected_provider") or provider
    compact["selected_model"] = result.get("selected_model") or model
    return compact


@router.get("/ui/api/jobs/{job_id}")
def job_detail(job_id: str, request: Request) -> dict[str, Any]:
    _require_access(request)
    try:
        result = get_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"fila indisponível: {type(exc).__name__}: {exc}") from exc
    if not result:
        raise HTTPException(status_code=404, detail="job não encontrado ou expirado")
    if result.get("status") == "completed" and isinstance(result.get("result"), dict):
        result = {**result, "result": _compact_result(result["result"])}
    return result


@router.get("/ui/api/investigations/{investigation_id}")
def investigation_detail(investigation_id: str, request: Request) -> dict[str, Any]:
    _require_access(request)
    ensure_database_schema()
    result = get_investigation(investigation_id, include_evidence=True)
    if not result:
        raise HTTPException(status_code=404, detail="investigação não encontrada")
    return result


@router.post("/ui/api/investigations/{investigation_id}/approve")
def approve_investigation(
    investigation_id: str,
    payload: ApprovalPayload,
    request: Request,
) -> dict[str, Any]:
    _require_mutation(request)
    ensure_database_schema()
    try:
        return execute_approved_investigation(
            investigation_id,
            payload.token,
            requested_by=_operator_name(),
            settings=get_settings(),
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}") from exc


@router.get("/ui/api/hosts")
def hosts(
    request: Request,
    limit: int = Query(default=100, ge=1, le=300),
    q: str | None = Query(default=None, max_length=255),
    environment: str | None = Query(default=None, max_length=30),
) -> dict[str, Any]:
    _require_access(request)
    ensure_database_schema()
    return list_hosts(limit=limit, query=q, environment=environment)


@router.get("/ui/api/playbooks")
def playbooks(request: Request) -> dict[str, Any]:
    _require_access(request)
    items = []
    for playbook in list_playbooks():
        items.append(
            {
                "id": playbook.id,
                "title": playbook.title,
                "priority": playbook.priority,
                "profiles": list(playbook.profiles),
                "patterns": list(playbook.patterns),
                "ssh_port": playbook.ssh_port,
                "allowed_corrections": list(playbook.allowed_corrections),
                "steps_count": len(playbook.steps),
                "validation_count": len(playbook.validation_tools),
            }
        )
    return {"total": len(items), "items": items}


def register_ui(app: FastAPI) -> None:
    if getattr(app.state, "agent_ui_registered", False):
        return
    app.mount("/ui/assets", StaticFiles(directory=UI_DIR), name="agent-ui-assets")
    app.include_router(router)
    app.state.agent_ui_registered = True
