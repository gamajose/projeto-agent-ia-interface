from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.services.fleet_discovery import fleet_discovery_status, fleet_discovery_tick
from app.web import _require_access, _require_mutation


router = APIRouter(tags=["interface-fleet"])


@router.get("/ui/api/noc/fleet")
def noc_fleet_status(request: Request) -> dict:
    _require_access(request)
    try:
        return fleet_discovery_status()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"descoberta de frota indisponível: {type(exc).__name__}: {exc}",
        ) from exc


@router.post("/ui/api/noc/fleet/tick")
def noc_fleet_tick(request: Request) -> dict:
    _require_mutation(request)
    try:
        return fleet_discovery_tick()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"descoberta de frota indisponível: {type(exc).__name__}: {exc}",
        ) from exc
