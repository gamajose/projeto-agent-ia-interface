from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
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
from app.web import _require_access, _require_mutation


router = APIRouter(tags=["interface-fleet"])


class FleetStartPayload(BaseModel):
    scope: str | None = Field(default=None, max_length=80)


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


@router.post("/ui/api/noc/checkmk-master/sync")
def noc_checkmk_master_sync(request: Request) -> dict:
    """Executa ciclo completo: sites, hosts, problemas, incidentes e jobs."""
    _require_mutation(request)
    try:
        return checkmk_master_patrol_cycle(force_sync=True)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"sincronização do CMK05 indisponível: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/checkmk-master/poll")
def noc_checkmk_master_poll(request: Request) -> dict:
    """Executa imediatamente um ciclo operacional completo no CMK05/master."""
    _require_mutation(request)
    try:
        return checkmk_master_patrol_cycle(force_sync=False)
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
