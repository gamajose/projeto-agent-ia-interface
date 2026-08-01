from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.web_topology import MultiHostInvestigationPayload, RelatedTargetPayload


def _base() -> dict:
    return {
        "target": "172.27.232.109",
        "objective": "Investigar alerta no ambiente do cliente",
        "environment": "monitoring",
        "provider": "gemini",
    }


def test_related_target_automatically_enables_multi_host() -> None:
    payload = MultiHostInvestigationPayload(
        **_base(),
        related_targets=[
            {
                "reference": "10.45.1.24",
                "ssh_port": 22,
                "role": "production",
                "environment": "production",
            }
        ],
    )

    assert payload.multi_host is True
    assert payload.related_targets[0].reference == "10.45.1.24"
    assert payload.related_targets[0].credential_ref == "SSH_DEFAULT_PASSWORD"


def test_payload_accepts_customer_and_manual_scope() -> None:
    payload = MultiHostInvestigationPayload(
        **_base(),
        multi_host=True,
        customer_name="Empresa José",
        auto_expand_scope=True,
        related_targets=[
            {
                "reference": "10.10.0.20",
                "role": "standby",
                "environment": "standby",
                "label": "Standby José",
            },
            {
                "reference": "10.10.0.30",
                "role": "production",
                "environment": "production",
                "label": "Produção José",
            },
        ],
    )

    assert payload.customer_name == "Empresa José"
    assert payload.auto_expand_scope is True
    assert [item.role for item in payload.related_targets] == ["standby", "production"]


def test_payload_rejects_more_than_eight_declared_hosts() -> None:
    with pytest.raises(ValidationError):
        MultiHostInvestigationPayload(
            **_base(),
            multi_host=True,
            related_targets=[
                {"reference": f"10.0.0.{index}", "role": "other"}
                for index in range(1, 10)
            ],
        )


def test_related_target_rejects_unapproved_credential_reference() -> None:
    with pytest.raises(ValidationError):
        RelatedTargetPayload(
            reference="10.45.1.24",
            credential_ref="MINHA_SENHA_CUSTOMIZADA",
        )


def test_related_target_rejects_database_connection_route_type() -> None:
    with pytest.raises(ValidationError):
        RelatedTargetPayload(
            reference="10.45.1.24",
            route_type="database",
        )
