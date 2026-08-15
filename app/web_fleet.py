from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.services.checkmk_master_patrol import (
    checkmk_master_patrol_cycle,
    checkmk_master_patrol_status,
)
from app.services.checkmk_operational import checkmk_operational_overview, checkmk_site_detail
from app.services.fleet_control import fleet_control_status
from app.services.fleet_patrol import fleet_patrol_cycle, fleet_patrol_status
from app.services.fleet_scope_control import (
    has_active_fleet_discovery,
    scoped_fleet_discovery_tick,
    start_fleet_discovery,
)
from app.services.noc_action_policy import (
    list_noc_action_history,
    list_noc_automation_policies,
    update_noc_automation_policy,
)
from app.services.noc_autonomy_control import (
    get_noc_autonomy_control,
    get_selected_run,
    request_selected_run,
    update_noc_autonomy_control,
)
from app.services.noc_skills import load_noc_skills
from app.web import _operator_name, _require_access, _require_mutation


router = APIRouter(tags=["interface-fleet"])


class FleetStartPayload(BaseModel):
    scope: str | None = Field(default=None, max_length=80)


class NocPolicyPayload(BaseModel):
    enabled: bool


class NocAutonomyPayload(BaseModel):
    enabled: bool
    mode: Literal["automatic", "selected"] = "automatic"
    sites: list[str] = Field(default_factory=list, max_length=1000)
    hosts: list[str] = Field(default_factory=list, max_length=2000)
    problem_keys: list[str] = Field(default_factory=list, max_length=5000)


class NocSelectedRunPayload(BaseModel):
    sites: list[str] = Field(default_factory=list, max_length=1000)
    hosts: list[str] = Field(default_factory=list, max_length=2000)
    problem_keys: list[str] = Field(default_factory=list, max_length=5000)
    playbook_id: str | None = Field(default=None, max_length=120)
    skill_id: str | None = Field(default=None, max_length=64)
    operator_instruction: str | None = Field(default=None, max_length=4000)


@router.get("/ui/api/noc/fleet")
def noc_fleet_status(request: Request) -> dict:
    _require_access(request)
    try:
        return {
            **fleet_control_status(),
            "checkmk_master": checkmk_master_patrol_status(),
            "fallback_patrol": fleet_patrol_status(),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"estado do NOC indisponível: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/ui/api/noc/checkmk-master/overview")
def noc_checkmk_master_overview(request: Request) -> dict:
    """Retorna sites, falhas Livestatus e problemas ativos organizados."""
    _require_access(request)
    try:
        return checkmk_operational_overview(problem_limit=1000, site_limit=1000)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"visão operacional do CMK05 indisponível: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/ui/api/noc/checkmk-master/sites/{site_id}")
def noc_checkmk_master_site_detail(request: Request, site_id: str) -> dict:
    """Mostra hosts e problemas pertencentes exclusivamente a um site/cliente."""
    _require_access(request)
    try:
        detail = checkmk_site_detail(site_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="site não encontrado no inventário do CMK05")
        return detail
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"detalhe do site indisponível: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/ui/api/noc/autonomy")
def noc_autonomy_status(request: Request) -> dict:
    """Estado runtime da chave que autoriza os agentes a acessar ambientes."""
    _require_access(request)
    try:
        return get_noc_autonomy_control()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"controle de autonomia indisponível: {type(exc).__name__}: {exc}") from exc


@router.post("/ui/api/noc/autonomy")
def noc_autonomy_update(payload: NocAutonomyPayload, request: Request) -> dict:
    """Liga/desliga a atuação contínua e persiste exatamente o escopo escolhido."""
    _require_mutation(request)
    try:
        return update_noc_autonomy_control(
            enabled=payload.enabled,
            mode=payload.mode,
            sites=payload.sites,
            hosts=payload.hosts,
            problem_keys=payload.problem_keys,
            operator=_operator_name(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"não foi possível atualizar a autonomia: {type(exc).__name__}: {exc}") from exc


@router.post("/ui/api/noc/autonomy/run-selected")
def noc_autonomy_run_selected(payload: NocSelectedRunPayload, request: Request) -> dict:
    """Enfileira uma atuação pontual somente no cliente/host/sensor selecionado."""
    _require_mutation(request)
    try:
        return request_selected_run(
            sites=payload.sites,
            hosts=payload.hosts,
            problem_keys=payload.problem_keys,
            playbook_id=payload.playbook_id,
            skill_id=payload.skill_id,
            operator_instruction=payload.operator_instruction,
            operator=_operator_name(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"não foi possível enfileirar o escopo selecionado: {type(exc).__name__}: {exc}") from exc


@router.get("/ui/api/noc/autonomy/runs/{run_id}")
def noc_autonomy_run_status(run_id: str, request: Request) -> dict:
    _require_access(request)
    try:
        result = get_selected_run(run_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"execução selecionada indisponível: {type(exc).__name__}: {exc}") from exc
    if not result:
        raise HTTPException(status_code=404, detail="execução selecionada não encontrada ou expirada")
    return result


@router.get("/ui/api/noc/skills")
def noc_skills(request: Request) -> dict:
    """Catálogo visual dos especialistas/skills disponíveis para o roteamento NOC."""
    _require_access(request)
    try:
        items = [skill.as_dict() for skill in load_noc_skills()]
        return {"total": len(items), "items": items}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"skills do NOC indisponíveis: {type(exc).__name__}: {exc}") from exc


@router.get("/ui/api/noc/history")
def noc_history(
    request: Request,
    status: str | None = Query(default=None, max_length=80),
    category: str | None = Query(default=None, max_length=80),
    query: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict:
    _require_access(request)
    try:
        return list_noc_action_history(status=status, category=category, query=query, limit=limit)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"histórico do NOC indisponível: {type(exc).__name__}: {exc}",
        ) from exc


@router.get("/ui/api/noc/policies")
def noc_policies(request: Request) -> dict:
    _require_access(request)
    try:
        return list_noc_automation_policies()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"políticas do NOC indisponíveis: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/policies/{category}")
def noc_policy_update(category: str, payload: NocPolicyPayload, request: Request) -> dict:
    _require_mutation(request)
    try:
        return update_noc_automation_policy(category, payload.enabled)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"não foi possível atualizar a política: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/checkmk-master/sync")
def noc_checkmk_master_sync(request: Request) -> dict:
    """Sincroniza sites, hosts e problemas em modo observação, sem iniciar SSH."""
    _require_mutation(request)
    try:
        return checkmk_master_patrol_cycle(force_sync=True, passive=True)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"sincronização do CMK05 indisponível: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/checkmk-master/poll")
def noc_checkmk_master_poll(request: Request) -> dict:
    """Atualiza imediatamente os problemas em modo observação, sem iniciar agentes."""
    _require_mutation(request)
    try:
        return checkmk_master_patrol_cycle(force_sync=False, passive=True)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"ronda do CMK05 indisponível: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/fleet/start")
def noc_fleet_start(request: Request, payload: FleetStartPayload | None = None) -> dict:
    """Inicia a descoberta de rede de contingência na faixa escolhida."""
    _require_mutation(request)
    try:
        return start_fleet_discovery(scope=(payload.scope if payload else None))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"não foi possível iniciar a descoberta: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/fleet/tick")
def noc_fleet_tick(request: Request) -> dict:
    """Executa um lote usando os CIDRs persistidos na descoberta ativa."""
    _require_mutation(request)
    if not has_active_fleet_discovery():
        raise HTTPException(status_code=409, detail="nenhuma descoberta ativa; use Iniciar descoberta")
    try:
        return scoped_fleet_discovery_tick()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"descoberta de rede indisponível: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/fleet/patrol")
def noc_fleet_patrol(request: Request) -> dict:
    """Executa manualmente a ronda legada de contingência."""
    _require_mutation(request)
    try:
        return fleet_patrol_cycle()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"ronda legada indisponível: {type(exc).__name__}: {exc}",
        ) from exc
