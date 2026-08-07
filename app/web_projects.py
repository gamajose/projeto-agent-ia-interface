from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.policies import EnvironmentType
from app.core.settings import get_settings
from app.services.jobs import enqueue_investigation
from app.services.project_validation import ProjectPlanError, build_project_plan, project_templates
from app.services.provider_router import resolve_automatic_provider
from app.services.tracked_runner import run_target_tracked
from app.web import _compact_result, _require_access, _require_mutation


router = APIRouter(prefix="/ui/api/projects", tags=["interface-projects"])


class RelatedHostPayload(BaseModel):
    role: Literal["production", "standby", "server"] = "server"
    vpn_ip: str = Field(min_length=1, max_length=64)


class ProjectPayload(BaseModel):
    scenario: Literal[
        "linux_prod_std",
        "linux_monitoring",
        "management_interface",
        "firewall",
        "windows",
        "dns_vpn",
    ]
    role: Literal["production", "standby", "monitoring", "unknown"] = "production"
    target_vpn_ip: str = Field(min_length=1, max_length=64)
    install_agent: bool = True
    has_monitoring_server: bool = False
    monitoring_vpn_ip: str | None = Field(default=None, max_length=64)
    related_hosts: list[RelatedHostPayload] = Field(default_factory=list, max_length=50)
    gateway_dns: str | None = Field(default=None, max_length=64)
    vpn_dns_name: str = Field(default="vpn.oracledba.com.br", max_length=255)
    provider: str | None = Field(default="auto", max_length=80)
    model: str | None = Field(default=None, max_length=255)


@router.get("/templates")
def templates(request: Request) -> dict[str, Any]:
    _require_access(request)
    return project_templates()


def _plan(payload: ProjectPayload, *, discover: bool = True) -> dict[str, Any]:
    try:
        return build_project_plan(payload.model_dump(), perform_discovery=discover)
    except ProjectPlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _plan_snapshot(plan: dict[str, Any]) -> dict[str, Any]:
    """Retorna à interface somente o que ajuda a acompanhar a execução.

    Os comandos continuam dentro do playbook/motor operacional; a tela não precisa
    funcionar como uma folha de comandos para o analista copiar e executar.
    """
    return {
        "plan_id": plan.get("plan_id"),
        "scenario": plan.get("scenario"),
        "scenario_label": plan.get("scenario_label"),
        "target": plan.get("target") or {},
        "discovery": plan.get("discovery") or {},
        "warnings": plan.get("warnings") or [],
        "summary": plan.get("summary") or {},
        "safety": plan.get("safety") or {},
        "ticket_macro": plan.get("ticket_macro"),
    }


@router.post("/plan")
def plan_project(payload: ProjectPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    # Mantido para integrações existentes. A interface principal usa /start e já
    # inicia a investigação depois da descoberta, sem parar numa lista de comandos.
    return _plan(payload, discover=True)


def _environment(value: Any) -> EnvironmentType:
    try:
        return EnvironmentType(str(value or "unknown"))
    except ValueError:
        return EnvironmentType.UNKNOWN


def _provider_selection(payload: ProjectPayload, settings: Any) -> tuple[str, str | None, dict[str, Any] | None]:
    requested = (payload.provider or "auto").strip().lower()
    requested_model = (payload.model or "").strip() or None
    if requested != "auto":
        return requested, requested_model, None
    selection = resolve_automatic_provider(settings)
    return selection.provider, selection.model, selection.as_dict()


def _run_kwargs(
    item: dict[str, Any],
    environment: EnvironmentType,
    settings: Any,
    *,
    provider_name: str,
    model_name: str | None,
) -> dict[str, Any]:
    return {
        "environment": environment,
        "mode": "propose",
        "approve": False,
        "ssh_port": int(item.get("ssh_port") or 22),
        "provider_name": provider_name,
        "model_name": model_name,
        "playbook_mode": "manual",
        "playbook_id": str(item["playbook_id"]),
        "settings": settings,
    }


@router.post("/start")
def start_project(payload: ProjectPayload, request: Request) -> dict[str, Any]:
    """Descobre o ambiente e já manda a IA executar a validação aplicável.

    O operador informa o IP e, quando quiser, restringe o cenário. Comandos de
    leitura não voltam para a interface como tarefas manuais: são executados pelo
    motor da IA. Mudanças continuam separadas em proposta/revisão/aprovação.
    """
    _require_mutation(request)
    settings = get_settings()
    plan = _plan(payload, discover=True)
    targets = list(plan.get("execution_targets") or [])
    if not targets:
        raise HTTPException(status_code=422, detail="o plano não possui alvo elegível para validação automática")

    provider_name, model_name, automatic_selection = _provider_selection(payload, settings)
    queue_mode = settings.agent_execution_mode.strip().casefold() == "queue"
    jobs: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for item in targets:
        environment = _environment(item.get("environment"))
        common = _run_kwargs(
            item,
            environment,
            settings,
            provider_name=provider_name,
            model_name=model_name,
        )
        metadata = {
            "source": "project_validation",
            "plan_id": plan["plan_id"],
            "scenario": plan["scenario"],
            "target_label": item.get("label"),
            "automatic_scope": "execute_read_only_then_propose",
            "input_contract": "vpn_ip_first",
            "requested_provider": (payload.provider or "auto").strip().lower(),
            "selected_provider": provider_name,
            "selected_model": model_name,
            "discovery_source": (plan.get("discovery") or {}).get("source"),
        }
        try:
            if queue_mode:
                queued = enqueue_investigation(
                    str(item["reference"]),
                    str(item["objective"]),
                    metadata=metadata,
                    **common,
                )
                jobs.append(
                    {
                        "job_id": queued["job_id"],
                        "status": queued["status"],
                        "reference": item["reference"],
                        "label": item.get("label"),
                        "playbook_id": item["playbook_id"],
                        "environment": environment.value,
                    }
                )
                continue

            result = run_target_tracked(
                str(item["reference"]),
                str(item["objective"]),
                **common,
            )
            compact = _compact_result(result)
            executions.append(
                {
                    "status": "completed",
                    "reference": item["reference"],
                    "label": item.get("label"),
                    "playbook_id": item["playbook_id"],
                    "environment": environment.value,
                    "investigation_id": result.get("investigation_id"),
                    "result": compact,
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "reference": str(item.get("reference") or ""),
                    "label": str(item.get("label") or ""),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    if not jobs and not executions:
        detail = errors[0]["error"] if errors else "nenhuma execução iniciada"
        raise HTTPException(status_code=503, detail=f"nenhuma validação foi executada: {detail}")

    return {
        "plan_id": plan["plan_id"],
        "scenario": plan["scenario"],
        "execution_mode": "queue" if queue_mode else "inline",
        "provider_selection": automatic_selection,
        "selected_provider": provider_name,
        "selected_model": model_name,
        "plan": _plan_snapshot(plan),
        "jobs": jobs,
        "executions": executions,
        "errors": errors,
        "message": (
            "A IA já iniciou as validações no(s) host(s). Ela executa as coletas e testes de leitura, "
            "analisa as evidências e apresenta causa provável e proposta. Alterações só seguem depois "
            "da revisão e aprovação previstas pelas políticas."
        ),
    }
