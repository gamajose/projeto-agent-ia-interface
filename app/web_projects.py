from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.policies import EnvironmentType
from app.core.settings import get_settings
from app.services.jobs import enqueue_investigation
from app.services.project_validation import ProjectPlanError, build_project_plan, project_templates
from app.web import _require_access, _require_mutation


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


@router.post("/plan")
def plan_project(payload: ProjectPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    return _plan(payload, discover=True)


@router.post("/start")
def start_project(payload: ProjectPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    settings = get_settings()
    # Refaz a descoberta antes de enfileirar para que os objetivos entregues aos
    # workers carreguem os IPs internos e a interface de gerenciamento realmente
    # observados no ambiente, sem depender de campos preenchidos pelo operador.
    plan = _plan(payload, discover=True)
    targets = list(plan.get("execution_targets") or [])
    if not targets:
        raise HTTPException(status_code=422, detail="o plano não possui alvo elegível para validação assistida")

    jobs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for item in targets:
        try:
            environment_value = str(item.get("environment") or "unknown")
            try:
                environment = EnvironmentType(environment_value)
            except ValueError:
                environment = EnvironmentType.UNKNOWN
            queued = enqueue_investigation(
                str(item["reference"]),
                str(item["objective"]),
                environment=environment,
                mode="propose",
                approve=False,
                ssh_port=int(item.get("ssh_port") or 22),
                provider_name=(payload.provider or "auto").strip().lower(),
                model_name=(payload.model or "").strip() or None,
                playbook_mode="manual",
                playbook_id=str(item["playbook_id"]),
                metadata={
                    "source": "project_validation",
                    "plan_id": plan["plan_id"],
                    "scenario": plan["scenario"],
                    "target_label": item.get("label"),
                    "automatic_scope": "read_only",
                    "input_contract": "vpn_ip_only",
                    "discovery_source": (plan.get("discovery") or {}).get("source"),
                },
                settings=settings,
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
        except Exception as exc:
            errors.append(
                {
                    "reference": str(item.get("reference") or ""),
                    "label": str(item.get("label") or ""),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    if not jobs:
        detail = errors[0]["error"] if errors else "fila indisponível"
        raise HTTPException(status_code=503, detail=f"nenhuma validação foi enfileirada: {detail}")

    return {
        "plan_id": plan["plan_id"],
        "scenario": plan["scenario"],
        "jobs": jobs,
        "errors": errors,
        "message": (
            "Validações de leitura enfileiradas. A IA recebe os IPs VPN e os fatos descobertos no ambiente "
            "(SO, IP interno, hardware e gerenciamento). Instalações e alterações permanecem manuais."
        ),
    }
