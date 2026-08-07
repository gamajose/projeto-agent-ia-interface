from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.policies import EnvironmentType
from app.core.settings import get_settings
from app.services.ansible_project import execute_project_steps
from app.services.jobs import enqueue_investigation
from app.services.project_macro_result import (
    build_project_macro_result,
    project_blueprint,
    public_checklist,
)
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


def _environment(value: Any) -> EnvironmentType:
    try:
        return EnvironmentType(str(value or "unknown"))
    except ValueError:
        return EnvironmentType.UNKNOWN


def _needs_sudo(item: dict[str, Any]) -> bool:
    step_id = str(item.get("id") or "")
    command = str(item.get("command") or "").strip().casefold()
    return step_id in {"root-access", "hardware", "management-detect"} or command.startswith("dmidecode ") or command.startswith("ipmitool ")


def _ansible_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Transforma somente as validações automáticas da macro em tarefas Ansible."""
    environment_by_reference = {
        str(item.get("reference") or ""): str(item.get("environment") or "unknown")
        for item in plan.get("execution_targets") or []
    }
    default_environment = next(iter(environment_by_reference.values()), "unknown")
    rows: list[dict[str, Any]] = []
    for group in plan.get("groups") or []:
        reference = str(group.get("target") or "").strip()
        if not reference or str(group.get("kind") or "remote") == "manual":
            continue
        environment = environment_by_reference.get(reference, default_environment)
        for item in group.get("items") or []:
            command = str(item.get("command") or "").strip()
            if item.get("kind") != "command" or not item.get("automated") or not command:
                continue
            rows.append(
                {
                    "id": item.get("id"),
                    "reference": reference,
                    "environment": environment,
                    "title": item.get("title"),
                    "purpose": item.get("purpose"),
                    "command": command,
                    "sudo": _needs_sudo(item),
                    "automated": True,
                }
            )
    return rows


def _plan_snapshot(plan: dict[str, Any]) -> dict[str, Any]:
    blueprint = project_blueprint(plan)
    return {
        "plan_id": plan.get("plan_id"),
        "scenario": plan.get("scenario"),
        "scenario_label": plan.get("scenario_label"),
        "target": plan.get("target") or {},
        "warnings": plan.get("warnings") or [],
        "summary": plan.get("summary") or {},
        "safety": plan.get("safety") or {},
        "ticket_macro": plan.get("ticket_macro"),
        "checklist": public_checklist(blueprint),
    }


@router.post("/plan")
def plan_project(payload: ProjectPayload, request: Request) -> dict[str, Any]:
    _require_mutation(request)
    return _plan(payload, discover=True)


@router.post("/start")
def start_project(payload: ProjectPayload, request: Request) -> dict[str, Any]:
    """Executa exclusivamente a macro de projeto.

    Esta rota não inicia troubleshooting, não procura causa raiz e não gera
    proposta corretiva. Projetos é um checklist operacional: executa o que pode
    ser automatizado, preserva as saídas para print e marca o restante como
    manual/pendente conforme a macro.
    """
    _require_mutation(request)
    settings = get_settings()
    queue_mode = settings.agent_execution_mode.strip().casefold() == "queue"
    plan = _plan(payload, discover=not queue_mode)
    targets = list(plan.get("execution_targets") or [])
    if not targets:
        raise HTTPException(status_code=422, detail="a macro não possui alvo elegível para validação")

    all_ansible_steps = _ansible_steps(plan)
    jobs: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for index, item in enumerate(targets):
        reference = str(item["reference"])
        environment = _environment(item.get("environment"))
        target_ansible_steps = [step for step in all_ansible_steps if str(step.get("reference")) == reference]
        blueprint = project_blueprint(plan, reference=reference, include_manual=index == 0)
        metadata = {
            "source": "project_validation",
            "project_macro_only": True,
            "plan_id": plan["plan_id"],
            "scenario": plan["scenario"],
            "scenario_label": plan["scenario_label"],
            "target_label": item.get("label"),
            "project_target": {"name": item.get("label"), "vpn_ip": reference},
            "ticket_macro": plan.get("ticket_macro") or "",
            "project_blueprint": blueprint,
            "access_monitor_id": "monitor1",
            "access_monitor_label": "Monitor 1",
            "access_monitor_host": settings.ssh_bastion_host,
            "ansible_steps": target_ansible_steps,
        }
        try:
            if queue_mode:
                queued = enqueue_investigation(
                    reference,
                    f"Executar somente a macro de projeto {plan['scenario_label']}.",
                    environment=environment,
                    mode="investigate",
                    approve=False,
                    ssh_port=int(item.get("ssh_port") or 22),
                    playbook_mode="none",
                    playbook_id=None,
                    metadata=metadata,
                    settings=settings,
                )
                jobs.append(
                    {
                        "job_id": queued["job_id"],
                        "status": queued["status"],
                        "reference": reference,
                        "label": item.get("label"),
                        "environment": environment.value,
                    }
                )
                continue

            evidence, diagnostics = execute_project_steps(
                target_ansible_steps,
                access_monitor_id="monitor1",
                settings=settings,
            )
            result = build_project_macro_result(
                blueprint=blueprint,
                evidence=evidence,
                diagnostics=diagnostics,
                target={"name": item.get("label"), "vpn_ip": reference},
                scenario=str(plan["scenario"]),
                scenario_label=str(plan["scenario_label"]),
                ticket_macro=str(plan.get("ticket_macro") or ""),
            )
            executions.append(
                {
                    "status": "completed",
                    "reference": reference,
                    "label": item.get("label"),
                    "environment": environment.value,
                    "result": result,
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "reference": reference,
                    "label": str(item.get("label") or ""),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    if not jobs and not executions:
        detail = errors[0]["error"] if errors else "nenhuma validação iniciada"
        raise HTTPException(status_code=503, detail=f"a macro não pôde ser executada: {detail}")

    return {
        "plan_id": plan["plan_id"],
        "scenario": plan["scenario"],
        "execution_mode": "queue" if queue_mode else "inline",
        "orchestrator": "ansible" if settings.agent_ansible_enabled else "agent",
        "plan": _plan_snapshot(plan),
        "jobs": jobs,
        "executions": executions,
        "errors": errors,
        "message": (
            "Executando somente as validações previstas na macro do projeto. "
            "As etapas automáticas são coletadas pelo Ansible e as etapas realmente manuais ficam marcadas para o analista. "
            "Esta tela não inicia investigação de causa raiz nem proposta de correção."
        ),
    }
