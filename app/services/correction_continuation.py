from __future__ import annotations

from typing import Any

from app.core.policies import EnvironmentType, environment_allows_correction
from app.core.settings import Settings, get_settings
from app.services.approvals import create_approval_token
from app.services.persistence import get_investigation, resolve_saved_target


class CorrectionContinuationError(RuntimeError):
    pass


def prepare_correction_continuation(
    investigation_id: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Gera uma nova autorização temporária a partir de uma proposta já validada.

    Nenhuma coleta ou correção é executada aqui. A execução real continua exigindo
    o endpoint de aprovação humana, que revalida ambiente, assinatura e ações.
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
    if not actions:
        raise CorrectionContinuationError(
            "esta investigação não possui ação corretiva validada; selecione ou crie um playbook corretivo antes de tentar corrigir"
        )

    review = dict(analysis.get("review") or {})
    if not review.get("approved"):
        raise CorrectionContinuationError("a segunda IA não aprovou as ações propostas")

    critic = dict(analysis.get("critic") or {})
    if critic:
        verdict = str(critic.get("verdict") or "insufficient")
        if verdict != "accept" or not bool(critic.get("safe_to_propose")):
            raise CorrectionContinuationError("a crítica independente não liberou a proposta para correção")

    try:
        environment = EnvironmentType(
            str(investigation.get("environment") or EnvironmentType.UNKNOWN.value)
        )
    except ValueError as exc:
        raise CorrectionContinuationError("ambiente da investigação é inválido") from exc
    if not environment_allows_correction(environment):
        raise CorrectionContinuationError(
            f"o ambiente {environment.value} permite investigação e proposta, mas não correção pelo agente"
        )

    target = str(investigation.get("target") or "").strip()
    if not target:
        raise CorrectionContinuationError("alvo da investigação não está disponível")
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
        raise CorrectionContinuationError(
            "não foi possível gerar a autorização; valide APPROVAL_SECRET e as ações propostas"
        )
    return {
        "investigation_id": investigation_id,
        "target": target,
        "environment": environment.value,
        "actions_count": len(actions),
        "approval_token": token,
        "expires_in_minutes": settings.approval_ttl_minutes,
        "message": "Proposta revalidada. Revise e confirme a execução das ações seguras.",
    }
