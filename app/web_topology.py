from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field, model_validator

from app.core.policies import EnvironmentType
from app.db.base import ensure_database_schema
from app.services.customer_topology import get_customer_topology, save_customer_scope
from app.services.performance_config import get_performance_config
from app.services.runtime_cache import get_runtime_cache
from app.web import InvestigationPayload, _require_access, _require_mutation


router = APIRouter(tags=["interface-topology"])
HostRole = Literal[
    "monitoring",
    "production",
    "standby",
    "database",
    "application",
    "firewall",
    "other",
]


class RelatedTargetPayload(BaseModel):
    reference: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    role: HostRole = "other"
    environment: EnvironmentType = EnvironmentType.UNKNOWN
    label: str | None = Field(default=None, max_length=255)
    via: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    route_type: Literal["ssh"] = "ssh"
    credential_ref: Literal["SSH_DEFAULT_PASSWORD"] = "SSH_DEFAULT_PASSWORD"


class MultiHostInvestigationPayload(InvestigationPayload):
    multi_host: bool = False
    customer_name: str | None = Field(default=None, max_length=255)
    auto_expand_scope: bool = True
    related_targets: list[RelatedTargetPayload] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_multi_host_scope(self) -> "MultiHostInvestigationPayload":
        if self.related_targets and not self.multi_host:
            self.multi_host = True
        if self.multi_host and len(self.related_targets) > 8:
            raise ValueError("a investigação aceita no máximo oito hosts relacionados informados manualmente")
        return self


class TopologySavePayload(BaseModel):
    customer_name: str = Field(min_length=1, max_length=255)
    primary_reference: str = Field(min_length=1, max_length=255)
    primary_port: int = Field(default=22, ge=1, le=65535)
    primary_hostname: str | None = Field(default=None, max_length=255)
    primary_role: HostRole = "monitoring"
    primary_environment: EnvironmentType = EnvironmentType.MONITORING
    related_targets: list[RelatedTargetPayload] = Field(default_factory=list, max_length=8)


def _topology_cache_key(reference: str | None, customer: str | None) -> str:
    return get_runtime_cache().key(
        "topology",
        (reference or "").strip().casefold(),
        (customer or "").strip().casefold(),
    )


@router.get("/ui/api/topology/resolve")
def resolve_topology(
    request: Request,
    reference: str | None = Query(default=None, max_length=255),
    customer: str | None = Query(default=None, max_length=255),
) -> dict:
    _require_access(request)
    ensure_database_schema()
    cache = get_runtime_cache()
    key = _topology_cache_key(reference, customer)
    cached = cache.get(key)
    if isinstance(cached, dict):
        return {**cached, "cache": {"hit": True, "ttl_seconds": get_performance_config().topology_cache_seconds}}
    result = get_customer_topology(reference=reference, customer_name=customer)
    cache.set(key, result, get_performance_config().topology_cache_seconds)
    return {**result, "cache": {"hit": False, "ttl_seconds": get_performance_config().topology_cache_seconds}}


@router.post("/ui/api/topology")
def save_topology(payload: TopologySavePayload, request: Request) -> dict:
    _require_mutation(request)
    ensure_database_schema()
    result = save_customer_scope(
        payload.customer_name,
        primary={
            "address": payload.primary_reference,
            "ssh_port": payload.primary_port,
            "hostname": payload.primary_hostname,
            "label": payload.primary_hostname or payload.primary_reference,
            "role": payload.primary_role,
            "environment": payload.primary_environment.value,
            "direct_vpn": True,
            "metadata": {"source": "ui_topology"},
        },
        related_targets=[
            {
                **item.model_dump(mode="json"),
                "address": item.reference,
                "environment": item.environment.value,
            }
            for item in payload.related_targets
        ],
    )
    cache = get_runtime_cache()
    cache.delete(_topology_cache_key(payload.primary_reference, None))
    cache.delete(_topology_cache_key(None, payload.customer_name))
    return result
