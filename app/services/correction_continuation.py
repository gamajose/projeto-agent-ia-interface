from __future__ import annotations

from typing import Any

from app.core.policies import EnvironmentType, environment_allows_correction
from app.core.settings import Settings, get_settings
from app.services.approvals import create_approval_token
from app.services.correction_readiness import assess_correction_readiness
from app.services.persistence import (
    get_investigation,
    resolve_saved_target,
    update_investigation_analysis,
)


class CorrectionContinuationError(RuntimeError):
    pass


def _blocked_response(
    *,
    investigation_id: str,
    target: str,
    environment: EnvironmentType,
    actions: list[dict[str, Any]],
    readiness: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "investigation_id": investigation_id,
        "target": target,
        "environment": environment.value,
        "actions_count": len(actions),
        "actions": actions,
        "correction_readiness": readiness,
        "can_execute": False,
        "approval_token": None,
        "reason": reason,
        "message": "A solicitação de correção foi analisada, mas não está liberada para execução automática.",
    }


def prepare_correction_continuation(
    investigation_id: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Prepara a decisão humana que ocorre depois da análise.

    O endpoint nunca altera o alvo. Ele apresenta ações, impacto, reinícios de
    serviço e eventual necessidade de reinício manual da máquina antes de gerar
    a autorização temporária para as ações permitidas.
    """
    settings = settings or get_settings()
    investigation = get_investigation(investigation_id, include_evidence=True)
    if not investigation:
        raise CorrectionContinuationError("investigação não encontrada")

    analysis = dict(investigation.get("analysis") or {})
    actions = [
        dict(item)
        for item in analysis.get("proposed_actions") or []
        if isinstance(item, dict) and item.get("status") == "proposed"
    ]
    try:
        environment = EnvironmentType(
            str(investigation.get("environment") or EnvironmentType.UNKNOWN.value)
        )
    except ValueError as exc:
        raise CorrectionContinuationError("ambiente da investigação é inválido") from exc

    target = str(investigation.get("target") or "").strip()
    if not target:
        raise CorrectionContinuationError("alvo da investigação não está disponível")

    readiness = assess_correction_readiness(investigation, actions)
    analysis["correction_request"] = {
        **readiness,
        "state": "prepared",
        "actions": actions,
    }
    update_investigation_analysis(investigation_id, analysis)

    if not actions:
        return _blocked_response(
            investigation_id=investigation_id,
            target=target,
            environment=environment,
            actions=actions,
            readiness=readiness,
            reason=(
                "A análise ainda não possui ação corretiva estruturada. O resultado permanece disponível, "
                "mas é necessário associar ou revisar um playbook antes da execução."
            ),
        )

    review = dict(analysis.get("review") or {})
    if not review.get("approved"):
        return _blocked_response(
            investigation_id=investigation_id,
            target=target,
            environment=environment,
            actions=actions,
            readiness=readiness,
            reason="A segunda IA não aprovou as ações propostas.",
        )

    critic = dict(analysis.get("critic") or {})
    if critic:
        verdict = str(critic.get("verdict") or "insufficient")
        if verdict != "accept" or not bool(critic.get("safe_to_propose")):
            return _blocked_response(
                investigation_id=investigation_id,
                target=target,
                environment=environment,
                actions=actions,
                readiness=readiness,
                reason="A crítica independente não liberou a proposta para correção.",
            )

    if not environment_allows_correction(environment):
        return _blocked_response(
            investigation_id=investigation_id,
            target=target,
            environment=environment,
            actions=actions,
            readiness=readiness,
            reason=(
                f"O ambiente {environment.value} permite solicitar e revisar a correção, "
                "mas não permite alteração automática pelo Agent IA."
            ),
        )

    saved = resolve_saved_target(target, environment.value)
    ssh_port = int((saved or {}).get("ssh_port") or settings.ssh_default_port)
    token = create_approval_token(
        investigation_id,
        target,
        actions,
        ssh_port=ssh_port,
        settings=settings,
    )
    if not token:
        return _blocked_response(
            investigation_id=investigation_id,
            target=target,
            environment=environment,
            actions=actions,
            readiness=readiness,
            reason="Não foi possível gerar a autorização temporária. Valide APPROVAL_SECRET.",
        )

    return {
        "investigation_id": investigation_id,
        "target": target,
        "environment": environment.value,
        "actions_count": len(actions),
        "actions": actions,
        "correction_readiness": readiness,
        "can_execute": True,
        "approval_token": token,
        "expires_in_minutes": settings.approval_ttl_minutes,
        "message": (
            "A proposta foi revalidada. Revise os impactos e confirme se deseja executar "
            "as ações sem reiniciar a máquina."
        ),
    }
