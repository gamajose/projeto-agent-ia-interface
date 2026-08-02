from __future__ import annotations

from typing import Any

from app.core.policies import EnvironmentType, environment_allows_correction
from app.core.settings import Settings, get_settings
from app.services.approvals import token_digest, verify_approval_token
from app.services.correction_comparison import build_before_after_comparison
from app.services.persistence import (
    complete_approval_execution,
    create_approval_execution,
    get_investigation,
    update_investigation_analysis,
)
from app.services.playbook_drafts import generate_playbook_draft
from app.services.recovery_loop import recovery_scope_from_investigation, run_adaptive_recovery
from app.services.runner import build_executor, resolve_target
from app.services.symptom_intake import use_reported_symptom
from app.services.tool_registry import execute_tool


class ApprovedExecutionError(RuntimeError):
    pass


def _legacy_execution(
    executor: Any,
    environment: EnvironmentType,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for item in actions:
        results.append(
            {
                **item,
                **execute_tool(
                    executor,
                    environment,
                    str(item.get("tool")),
                    dict(item.get("arguments") or {}),
                    approved=True,
                ),
            }
        )
    status = "validated" if results and all(item.get("status") == "validated" for item in results) else "failed"
    return {
        "status": status,
        "state": "resolved_and_validated" if status == "validated" else "failed",
        "scope": {},
        "results": results,
        "diagnostic_results": [],
        "rounds": [],
        "blockers": [],
        "pending_actions": [],
        "new_approval_required": False,
        "ai_diagnostics": [],
        "summary": (
            "As ações aprovadas foram executadas e validadas."
            if status == "validated"
            else "Uma ou mais ações aprovadas falharam na validação."
        ),
    }


def execute_approved_investigation(
    investigation_id: str,
    token: str,
    *,
    requested_by: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    investigation = get_investigation(investigation_id, include_evidence=True)
    if not investigation:
        raise ApprovedExecutionError("investigação não encontrada")

    analysis = investigation.get("analysis") or {}
    actions = [item for item in analysis.get("proposed_actions") or [] if item.get("status") == "proposed"]
    payload = verify_approval_token(token, actions, settings=settings)
    if payload.get("investigation_id") != investigation_id:
        raise ApprovedExecutionError("o token pertence a outra investigação")
    if not (analysis.get("review") or {}).get("approved"):
        raise ApprovedExecutionError("a segunda IA não aprovou as ações")

    environment = EnvironmentType(investigation.get("environment") or EnvironmentType.UNKNOWN.value)
    if not environment_allows_correction(environment):
        raise ApprovedExecutionError(f"ambiente {environment.value} não permite correção automática")

    target_reference = str(investigation.get("target") or "")
    approved_ssh_port = payload.get("ssh_port")
    try:
        target = resolve_target(
            target_reference,
            environment,
            int(approved_ssh_port) if approved_ssh_port is not None else None,
            settings=settings,
        )
    except LookupError as exc:
        raise ApprovedExecutionError("alvo não está mais disponível no inventário") from exc

    scope = recovery_scope_from_investigation(investigation, actions, settings)
    if scope.get("target") not in {None, "", target_reference}:
        raise ApprovedExecutionError("o envelope de recuperação pertence a outro alvo")
    if scope.get("environment") not in {None, "", environment.value}:
        raise ApprovedExecutionError("o envelope de recuperação pertence a outro ambiente")

    execution_id = create_approval_execution(
        investigation_id=investigation_id,
        token_digest=token_digest(token),
        requested_by=requested_by,
        actions=actions,
    )
    executor = build_executor(target, settings=settings)
    results: list[dict[str, Any]] = []
    comparison: dict[str, Any] = {}
    recovery: dict[str, Any] = {}
    playbook_draft: dict[str, Any] | None = None
    playbook_draft_error: str | None = None
    try:
        executor.connect()
        with use_reported_symptom(str(investigation.get("objective") or "")):
            recovery = (
                run_adaptive_recovery(
                    executor=executor,
                    environment=environment,
                    initial_actions=actions,
                    analysis=dict(analysis),
                    evidence=list(investigation.get("evidence") or []),
                    scope=scope,
                    settings=settings,
                )
                if settings.agent_recovery_enabled
                else _legacy_execution(executor, environment, actions)
            )
        results = list(recovery.get("results") or [])
        status = str(recovery.get("status") or "failed")
        comparison = build_before_after_comparison(results)
        updated_analysis = dict(analysis)
        updated_analysis["correction_validation"] = comparison
        updated_analysis["correction_status"] = status
        updated_analysis["recovery_scope"] = scope
        updated_analysis["recovery_loop"] = recovery
        updated_analysis["recovery_state"] = recovery.get("state")
        updated_analysis["recovery_blockers"] = recovery.get("blockers") or []
        updated_analysis["recovery_pending_actions"] = recovery.get("pending_actions") or []

        if status == "validated":
            try:
                playbook_draft = generate_playbook_draft(
                    investigation_id,
                    results,
                    generated_by=requested_by,
                )
                if playbook_draft:
                    updated_analysis["playbook_draft"] = {
                        "id": playbook_draft.get("id"),
                        "playbook_id": playbook_draft.get("playbook_id"),
                        "title": playbook_draft.get("title"),
                        "status": playbook_draft.get("status"),
                    }
            except Exception as exc:
                playbook_draft_error = f"{type(exc).__name__}: {exc}"
                updated_analysis["playbook_draft_error"] = playbook_draft_error

        update_investigation_analysis(investigation_id, updated_analysis)
        complete_approval_execution(execution_id, status=status, results=results)
        return {
            "execution_id": execution_id,
            "investigation_id": investigation_id,
            "target": target_reference,
            "environment": environment.value,
            "status": status,
            "state": recovery.get("state"),
            "results": results,
            "before_after": comparison,
            "recovery": recovery,
            "new_approval_required": bool(recovery.get("new_approval_required")),
            "pending_actions": recovery.get("pending_actions") or [],
            "playbook_draft": playbook_draft,
            "playbook_draft_error": playbook_draft_error,
        }
    except Exception:
        comparison = build_before_after_comparison(results)
        complete_approval_execution(execution_id, status="failed", results=results)
        updated_analysis = dict(analysis)
        if comparison:
            updated_analysis["correction_validation"] = comparison
        updated_analysis["correction_status"] = "failed"
        updated_analysis["recovery_scope"] = scope
        if recovery:
            updated_analysis["recovery_loop"] = recovery
            updated_analysis["recovery_state"] = recovery.get("state")
        update_investigation_analysis(investigation_id, updated_analysis)
        raise
    finally:
        executor.close()
