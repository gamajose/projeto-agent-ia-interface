from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.services.fleet_control import (
    fleet_control_status,
    has_active_fleet_discovery,
    start_fleet_discovery,
)
from app.services.fleet_discovery import fleet_discovery_tick
from app.services.fleet_patrol import fleet_patrol_cycle, fleet_patrol_status
from app.web import _require_access, _require_mutation


router = APIRouter(tags=["interface-fleet"])


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
def noc_fleet_start(request: Request) -> dict:
    """Inicia a descoberta completa após clique explícito do operador."""
    _require_mutation(request)
    try:
        return start_fleet_discovery()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"não foi possível iniciar a descoberta: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/fleet/tick")
def noc_fleet_tick(request: Request) -> dict:
    """Executa um lote somente quando já existe uma descoberta ativa."""
    _require_mutation(request)
    if not has_active_fleet_discovery():
        raise HTTPException(status_code=409, detail="nenhuma descoberta ativa; use Iniciar descoberta completa")
    try:
        return fleet_discovery_tick()
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
