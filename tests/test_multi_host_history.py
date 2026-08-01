from __future__ import annotations

from uuid import uuid4

from app.db.base import SessionLocal, ensure_database_schema
from app.db.models import InvestigationORM
from app.services.ui_queries import list_investigations


def test_multi_host_child_is_hidden_from_main_history() -> None:
    ensure_database_schema()
    marker = f"multi-host-history-{uuid4().hex}"
    with SessionLocal() as session:
        root = InvestigationORM(
            target=f"{marker}-root",
            hostname="monitoramento-jose",
            objective=f"{marker} investigação lógica",
            environment="monitoring",
            mode="investigate",
            status="attention",
            confidence=82,
            profile="checkmk",
            model="test",
            duration_ms=100,
            plans=[],
            evidence=[],
            assessments=[],
            analysis={
                "status": "attention",
                "confidence": 82,
                "multi_host": {
                    "enabled": True,
                    "customer": {"name": "Empresa José"},
                    "hosts": [{"address": "172.27.232.100"}, {"address": "10.45.1.24"}],
                    "root_host": "172.27.232.100",
                },
            },
            diagnostics=[],
        )
        session.add(root)
        session.flush()
        child = InvestigationORM(
            target=f"{marker}-child",
            hostname="producao-jose",
            objective=f"{marker} coleta no host interno",
            environment="production",
            mode="investigate",
            status="attention",
            confidence=70,
            profile="linux_generic",
            model="test",
            duration_ms=50,
            plans=[],
            evidence=[],
            assessments=[],
            analysis={
                "status": "attention",
                "confidence": 70,
                "multi_host_parent_id": str(root.id),
                "multi_host_child": True,
            },
            diagnostics=[],
        )
        session.add(child)
        session.commit()

    result = list_investigations(query=marker, limit=10)

    assert result["total"] == 1
    assert len(result["items"]) == 1
    assert result["items"][0]["target"] == f"{marker}-root"
    assert result["items"][0]["multi_host"]["enabled"] is True
    assert result["items"][0]["multi_host"]["hosts_count"] == 2
