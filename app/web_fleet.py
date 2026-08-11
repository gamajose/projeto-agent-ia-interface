from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

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
        return {**fleet_control_status(), "patrol": fleet_patrol_status()}
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"descoberta de frota indisponível: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/fleet/start")
def noc_fleet_start(request: Request, payload: FleetStartPayload | None = None) -> dict:
    """Inicia a descoberta na faixa escolhida, sempre dentro do limite autorizado."""
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
        raise HTTPException(status_code=409, detail="nenhuma descoberta ativa; use Iniciar descoberta completa")
    try:
        return scoped_fleet_discovery_tick()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"descoberta de frota indisponível: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/fleet/patrol")
def noc_fleet_patrol(request: Request) -> dict:
    """Dispara uma ronda imediata; o worker também executa esse ciclo automaticamente."""
    _require_mutation(request)
    try:
        return fleet_patrol_cycle()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"ronda de frota indisponível: {type(exc).__name__}: {exc}",
        ) from exc
