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
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(default="server", max_length=30)
    internal_ip: str = Field(min_length=1, max_length=64)
    vpn_ip: str | None = Field(default=None, max_length=64)


class ProjectPayload(BaseModel):
    project_name: str = Field(default="Validação de projeto", max_length=180)
    ticket_number: str | None = Field(default=None, max_length=40)
    scenario: Literal[
        "linux_prod_std",
        "linux_monitoring",
        "management_interface",
        "firewall",
        "windows",
        "dns_vpn",
    ]
    role: Literal["production", "standby", "monitoring", "unknown"] = "production"
    target_name: str = Field(default="Servidor do projeto", max_length=180)
    target_vpn_ip: str = Field(min_length=1, max_length=64)
    target_internal_ip: str | None = Field(default=None, max_length=64)
    os_family: Literal[
        "oracle7",
        "oracle8",
        "oracle9",
        "rhel",
        "ubuntu",
        "debian",
        "windows",
        "pfsense",
        "fortigate",
        "unknown",
    ] = "unknown"
    install_agent: bool = True
    has_monitoring_server: bool = False
    monitoring_name: str | None = Field(default=None, max_length=180)
    monitoring_vpn_ip: str | None = Field(default=None, max_length=64)
    monitoring_internal_ip: str | None = Field(default=None, max_length=64)
    related_hosts: list[RelatedHostPayload] = Field(default_factory=list, max_length=50)
    management_interface_type: Literal["auto", "idrac", "ilo", "ilom", "xclarity", "none"] = "auto"
    management_interface_ip: str | None = Field(default=None, max_length=64)
    firewall_type: Literal["pfsense", "fortigate", "fortinet", "unknown"] = "unknown"
    gateway_dns: str | None = Field(default=None, max_length=64)
    vpn_dns_name: str = Field(default="vpn.oracledba.com.br", max_length=255)
    monitor1_ip: str = Field(default="10.17.181.1", max_length=64)
    monitor1_user: str = Field(default="jose.moraes", max_length=120)
    cmk05_ip: str = Field(default="10.17.181.44", max_length=64)
    whatsapp_host: str = Field(default="ws.2comconsulting.com.br", max_length=255)
    provider: str | None = Field(default="auto", max_length=80)
    model: str | None = Field(default=None, max_length=255)


@router.get("/templates")
def templates(request: Request) -> dict[str, Any]:
    _require_access(request)
    return project_templates()


def _plan(payload: ProjectPayload) -> dict[str, Any]:
    try:
        return build_project_plan(payload.model_dump())
    except ProjectPlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/plan")
def plan_project(payload: ProjectPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    return _plan(payload)


@router.post("/start")
def start_project(payload: ProjectPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    settings = get_settings()
    plan = _plan(payload)
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
                    "project_name": plan["project_name"],
                    "ticket_number": plan.get("ticket_number"),
                    "scenario": plan["scenario"],
                    "target_label": item.get("label"),
                    "automatic_scope": "read_only",
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
        "project_name": plan["project_name"],
        "scenario": plan["scenario"],
        "jobs": jobs,
        "errors": errors,
        "plan": plan,
        "message": (
            "Validações de leitura enfileiradas. Instalações, ajustes de rede, listeners e reinícios "
            "permanecem no checklist manual e não foram executados."
        ),
    }
