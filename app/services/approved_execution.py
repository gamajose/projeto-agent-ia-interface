from __future__ import annotations

from typing import Any

from app.core.policies import EnvironmentType, environment_allows_correction
from app.core.settings import Settings, get_settings
from app.services.approvals import (
    ApprovalError,
    create_approval_token,
    token_digest,
    verify_approval_token,
)
from app.services.checkmk_post_correction import collect_target_from_monitor
from app.services.correction_comparison import build_before_after_comparison
from app.services.persistence import (
    complete_approval_execution,
    create_approval_execution,
    get_investigation,
    update_investigation_analysis,
)
from app.services.playbook_drafts import generate_playbook_draft
from app.services.recovery_loop import recovery_scope_from_investigation, run_adaptive_recovery
from app.services.reviewer import review_corrections
from app.services.site_scoped_execution import build_approved_execution_route
from app.services.symptom_intake import use_reported_symptom
from app.services.tool_registry import execute_tool


class ApprovedExecutionError(RuntimeError):
    pass


def _normalized_pending_actions(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in analysis.get("recovery_pending_actions") or []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                **item,
                "status": "proposed",
                "reason": item.get("reason"),
            }
        )
    return result


def _verify_actions(
    token: str,
    investigation_id: str,
    analysis: dict[str, Any],
    settings: Settings,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    initial = [
        item
        for item in analysis.get("proposed_actions") or []
        if item.get("status") == "proposed"
    ]
    pending = _normalized_pending_actions(analysis)
    errors: list[str] = []
    for source, actions in (("initial", initial), ("adaptive_pending", pending)):
        if not actions:
            continue
        try:
            payload = verify_approval_token(token, actions, settings=settings)
        except ApprovalError as exc:
            errors.append(str(exc))
            continue
        if payload.get("investigation_id") != investigation_id:
            raise ApprovedExecutionError("o token pertence a outra investigação")
        return actions, payload, source
    raise ApprovedExecutionError(
        "o token não corresponde às ações atualmente aguardando aprovação"
        + (f": {' | '.join(errors[-2:])}" if errors else "")
    )


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


def _review_pending_actions(
    *,
    analysis: dict[str, Any],
    pending_actions: list[dict[str, Any]],
    recovery: dict[str, Any],
    evidence: list[dict[str, Any]],
    settings: Settings,
) -> dict[str, Any]:
    blockers = recovery.get("blockers") or []
    last_blocker = blockers[-1] if blockers else {}
    review_analysis = {
        "status": "attention",
        "confidence": analysis.get("confidence") or 0,
        "probable_cause": (
            last_blocker.get("root_blocker")
            or last_blocker.get("summary")
            or analysis.get("probable_cause")
        ),
        "conclusion": last_blocker.get("causal_link") or recovery.get("summary"),
        "root_cause": analysis.get("root_cause") or {},
        "recovery_goal": analysis.get("recovery_goal") or {},
    }
    return review_corrections(
        review_analysis,
        pending_actions,
        [
            *evidence,
            *(recovery.get("results") or []),
            *(recovery.get("diagnostic_results") or []),
        ],
        settings=settings,
    )


def _pending_tools_belong_to_playbook(
    analysis: dict[str, Any],
    pending_actions: list[dict[str, Any]],
) -> bool:
    """Impede que uma nova aprovação amplie o playbook silenciosamente."""
    approved_scope = {
        str(item)
        for item in (analysis.get("recovery_scope") or {}).get("allowed_correction_tools") or []
        if str(item).strip()
    }
    requested = {
        str(item.get("tool") or "").strip()
        for item in pending_actions
        if isinstance(item, dict) and str(item.get("tool") or "").strip()
    }
    return bool(requested) and requested.issubset(approved_scope)


def _requires_checkmk_post_collection(actions: list[dict[str, Any]]) -> bool:
    """Correções do agente Checkmk só terminam depois de nova coleta no monitor."""
    for item in actions:
        tool = str(item.get("tool") or "").strip()
        arguments = dict(item.get("arguments") or {})
        if tool == "checkmk.resolve_legacy_socket_conflict":
            return True
        if tool == "systemd.recover_unit":
            unit = str(arguments.get("unit") or "").casefold()
            if any(token in unit for token in ("check-mk-agent", "check_mk", "xinetd")):
                return True
    return False


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

    analysis = dict(investigation.get("analysis") or {})
    actions, payload, approval_source = _verify_actions(
        token,
        investigation_id,
        analysis,
        settings,
    )
    review = (
        analysis.get("recovery_pending_review")
        if approval_source == "adaptive_pending"
        else analysis.get("review")
    ) or {}
    if not review.get("approved"):
        raise ApprovedExecutionError("a segunda IA não aprovou as ações selecionadas")

    environment = EnvironmentType(investigation.get("environment") or EnvironmentType.UNKNOWN.value)
    if not environment_allows_correction(environment):
        raise ApprovedExecutionError(f"ambiente {environment.value} não permite correção automática")

    target_reference = str(investigation.get("target") or "")
    approved_ssh_port = payload.get("ssh_port")
    try:
        route = build_approved_execution_route(
            investigation,
            analysis,
            environment=environment,
            approved_ssh_port=int(approved_ssh_port) if approved_ssh_port is not None else None,
            settings=settings,
        )
    except (LookupError, RuntimeError, ValueError) as exc:
        raise ApprovedExecutionError(
            f"não foi possível reconstruir a rota segura usada na investigação: {exc}"
        ) from exc

    persisted_scope = dict(analysis.get("recovery_scope") or {})
    scope = recovery_scope_from_investigation(investigation, actions, settings)
    if approval_source == "initial" and persisted_scope:
        scope = {
            **scope,
            **persisted_scope,
            "allowed_correction_tools": list(
                dict.fromkeys(
                    [
                        *(scope.get("allowed_correction_tools") or []),
                        *(persisted_scope.get("allowed_correction_tools") or []),
                    ]
                )
            ),
        }
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
    executor = route.executor
    results: list[dict[str, Any]] = []
    comparison: dict[str, Any] = {}
    recovery: dict[str, Any] = {}
    post_correction_collection: dict[str, Any] | None = None
    playbook_draft: dict[str, Any] | None = None
    playbook_draft_error: str | None = None
    next_approval_token: str | None = None
    pending_review: dict[str, Any] | None = None
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

        if status == "validated" and route.site_scoped and _requires_checkmk_post_collection(actions):
            site_scope = dict(analysis.get("site_scope") or {})
            target_host = str(site_scope.get("host_name") or "").strip()
            if not target_host:
                post_correction_collection = {
                    "stage": "checkmk_post_correction_collection",
                    "status": "failed",
                    "reason": "TARGET_HOST não foi preservado no envelope site-scoped",
                    "same_site_only": True,
                }
            else:
                post_correction_collection = collect_target_from_monitor(executor, target_host)
            recovery["post_correction_collection"] = post_correction_collection
            if post_correction_collection.get("status") != "validated":
                status = "failed"
                recovery["status"] = "failed"
                recovery["state"] = "post_correction_collection_failed"
                recovery["summary"] = (
                    "A correção no TARGET_HOST foi validada, mas a nova coleta no MONITORING_HOST não foi confirmada."
                )

        comparison = build_before_after_comparison(results)
        updated_analysis = dict(analysis)
        updated_analysis["correction_validation"] = comparison
        updated_analysis["correction_status"] = status
        updated_analysis["recovery_scope"] = scope
        updated_analysis["recovery_loop"] = recovery
        updated_analysis["recovery_state"] = recovery.get("state")
        updated_analysis["recovery_blockers"] = recovery.get("blockers") or []
        if post_correction_collection is not None:
            updated_analysis["checkmk_post_correction_collection"] = post_correction_collection
        updated_analysis["approved_execution_route"] = {
            **dict(route.metadata),
            "site_scoped": route.site_scoped,
            "context": route.context,
        }

        pending_actions = [
            {**item, "status": "proposed"}
            for item in recovery.get("pending_actions") or []
            if isinstance(item, dict)
        ]
        if pending_actions:
            if _pending_tools_belong_to_playbook(analysis, pending_actions):
                pending_review = _review_pending_actions(
                    analysis=updated_analysis,
                    pending_actions=pending_actions,
                    recovery=recovery,
                    evidence=list(investigation.get("evidence") or []),
                    settings=settings,
                )
                if pending_review.get("approved"):
                    next_approval_token = create_approval_token(
                        investigation_id,
                        target_reference,
                        pending_actions,
                        ssh_port=route.ssh_port,
                        settings=settings,
                    )
            else:
                pending_review = {
                    "approved": False,
                    "reason": (
                        "O novo bloqueio exige uma ferramenta corretiva que não está autorizada "
                        "pelo playbook desta investigação. Revise o playbook e gere outra proposta."
                    ),
                    "requires_playbook_review": True,
                }
                pending_actions = [
                    {
                        **item,
                        "status": "playbook_review_required",
                        "reason": pending_review["reason"],
                    }
                    for item in pending_actions
                ]
            updated_analysis["recovery_pending_actions"] = pending_actions
            updated_analysis["recovery_pending_review"] = pending_review
            if next_approval_token:
                updated_analysis["recovery_pending_approval"] = {
                    "required": True,
                    "expires_in_minutes": settings.approval_ttl_minutes,
                }
            else:
                updated_analysis.pop("recovery_pending_approval", None)
        else:
            updated_analysis.pop("recovery_pending_actions", None)
            updated_analysis.pop("recovery_pending_review", None)
            updated_analysis.pop("recovery_pending_approval", None)

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
            "approval_source": approval_source,
            "status": status,
            "state": recovery.get("state"),
            "results": results,
            "before_after": comparison,
            "recovery": recovery,
            "post_correction_collection": post_correction_collection,
            "execution_route": {
                **dict(route.metadata),
                "site_scoped": route.site_scoped,
                "context": route.context,
            },
            "new_approval_required": bool(next_approval_token),
            "next_approval_token": next_approval_token,
            "pending_actions": pending_actions,
            "pending_review": pending_review,
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
        updated_analysis["approved_execution_route"] = {
            **dict(route.metadata),
            "site_scoped": route.site_scoped,
            "context": route.context,
        }
        if post_correction_collection is not None:
            updated_analysis["checkmk_post_correction_collection"] = post_correction_collection
        if recovery:
            updated_analysis["recovery_loop"] = recovery
            updated_analysis["recovery_state"] = recovery.get("state")
        update_investigation_analysis(investigation_id, updated_analysis)
        raise
    finally:
        executor.close()