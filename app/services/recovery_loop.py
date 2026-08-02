from __future__ import annotations

import json
from collections import Counter, deque
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings
from app.services.intelligent_agent import resilient_model_call
from app.services.redaction import redact_object
from app.services.reviewer import review_corrections
from app.services.tool_registry import describe_tools, execute_tool, resolve_tool


_RECOVERY_DIAGNOSIS_RULES = """
Você é o diagnosticador de uma recuperação AIOps que encontrou um novo erro durante uma ação aprovada.
Responda somente JSON válido. O erro da ação é uma nova evidência, não o fim da investigação.
Explique o bloqueio provável e escolha somente ferramentas estruturadas de leitura da lista recebida.
Não proponha correção nesta etapa. Não acesse banco de cliente, não reinicie servidor, container, firewall ou pacote.
Formato:
{
  "blocker_summary":"...",
  "new_symptom":"...",
  "causal_link":"como o bloqueio impede a recuperação",
  "hypotheses":["..."],
  "diagnostic_tools":[{"tool":"systemd.inspect_unit","arguments":{},"purpose":"..."}],
  "stop":false,
  "stop_reason":""
}
Máximo de 4 ferramentas de leitura.
""".strip()

_RECOVERY_REPLAN_RULES = """
Você é o planejador de uma recuperação AIOps adaptativa. Responda somente JSON válido.
Use o erro da ação e as novas evidências para escolher o próximo passo.
Só proponha uma ferramenta corretiva da lista explicitamente autorizada no envelope de segurança.
Uma ferramenta fora desse envelope deve ser devolvida como next_action, mas requires_new_approval=true.
Não escreva shell. Não proponha reboot, shutdown, banco de cliente, firewall, pacote, remoção de arquivo ou ciclo de vida de container.
Formato:
{
  "root_blocker":"...",
  "causal_chain":["causa", "bloqueio", "sintoma"],
  "next_action":{"description":"...","tool":"...","arguments":{},"evidence_reason":"..."},
  "requires_new_approval":false,
  "resolved":false,
  "stop":false,
  "stop_reason":""
}
Use next_action=null quando nenhuma ação segura puder ser proposta.
""".strip()


def recovery_scope_from_investigation(
    investigation: dict[str, Any],
    initial_actions: list[dict[str, Any]],
    settings: Settings,
) -> dict[str, Any]:
    allowed: list[str] = []
    for item in initial_actions:
        name = str(item.get("tool") or "").strip()
        if name and name not in allowed:
            allowed.append(name)
    for plan in investigation.get("plans") or []:
        playbook = plan.get("playbook") if isinstance(plan, dict) else None
        if not isinstance(playbook, dict):
            continue
        for name in playbook.get("allowed_corrections") or []:
            value = str(name or "").strip()
            if value and value not in allowed:
                allowed.append(value)

    safe_allowed: list[str] = []
    for name in allowed:
        try:
            plan = resolve_tool(name, {})
        except Exception:
            # Ferramentas que exigem argumentos são verificadas novamente antes
            # de executar; aqui basta confirmar que constam no catálogo descrito.
            descriptor = next((item for item in describe_tools() if item.get("name") == name), None)
            if descriptor and descriptor.get("correction"):
                safe_allowed.append(name)
            continue
        if plan.correction:
            safe_allowed.append(name)

    return {
        "target": investigation.get("target"),
        "environment": investigation.get("environment"),
        "allowed_correction_tools": list(dict.fromkeys(safe_allowed)),
        "max_recovery_rounds": settings.agent_recovery_max_rounds,
        "max_correction_actions": settings.agent_recovery_max_actions,
        "max_diagnostic_tools_per_round": settings.agent_recovery_max_diagnostics_per_round,
        "same_target_only": True,
        "database_access": False,
        "server_reboot": False,
        "container_lifecycle": False,
        "firewall_change": False,
        "package_change": False,
    }


def _read_tool_names() -> set[str]:
    return {
        str(item.get("name"))
        for item in describe_tools()
        if item.get("name") and not item.get("correction")
    }


def _compact_evidence(items: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items[-limit:]:
        result.append(
            {
                "tool": item.get("tool"),
                "purpose": item.get("purpose"),
                "status": item.get("status"),
                "exit_code": item.get("exit_code"),
                "stdout": str(item.get("stdout") or "")[-1600:],
                "stderr": str(item.get("stderr") or "")[-800:],
                "validations": item.get("validations") or [],
                "reason": item.get("reason"),
            }
        )
    return result


def _action_key(item: dict[str, Any]) -> str:
    return json.dumps(
        {
            "tool": item.get("tool"),
            "arguments": item.get("arguments") or {},
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


def _diagnose_blocker(
    *,
    action: dict[str, Any],
    action_result: dict[str, Any],
    analysis: dict[str, Any],
    evidence: list[dict[str, Any]],
    round_number: int,
    settings: Settings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    read_tools = [
        {
            "name": item.get("name"),
            "category": item.get("category"),
            "description": item.get("description"),
            "arguments": item.get("arguments") or {},
        }
        for item in describe_tools()
        if not item.get("correction")
    ]
    payload = {
        "root_cause": analysis.get("root_cause") or analysis.get("probable_cause"),
        "recovery_goal": analysis.get("recovery_goal") or {},
        "failed_action": action,
        "action_result": action_result,
        "previous_evidence": _compact_evidence(evidence),
        "available_read_tools": read_tools,
        "limits": {
            "diagnostic_tools": settings.agent_recovery_max_diagnostics_per_round,
        },
    }
    result, diagnostics = resilient_model_call(
        _RECOVERY_DIAGNOSIS_RULES
        + "\n\nDADOS DA RECUPERAÇÃO:\n"
        + json.dumps(redact_object(payload), ensure_ascii=False, default=str),
        f"recovery_diagnosis_round_{round_number}",
    )
    return result or {
        "blocker_summary": "A ação falhou e a IA não conseguiu classificar o novo bloqueio.",
        "new_symptom": str(action_result.get("stderr") or action_result.get("reason") or "falha não detalhada"),
        "causal_link": "O bloqueio impediu a validação da ação corretiva.",
        "hypotheses": [],
        "diagnostic_tools": [],
        "stop": True,
        "stop_reason": "diagnóstico adaptativo indisponível",
    }, diagnostics


def _execute_diagnostics(
    executor: Any,
    environment: EnvironmentType,
    diagnosis: dict[str, Any],
    *,
    settings: Settings,
) -> list[dict[str, Any]]:
    allowed = _read_tool_names()
    results: list[dict[str, Any]] = []
    for item in (diagnosis.get("diagnostic_tools") or [])[: settings.agent_recovery_max_diagnostics_per_round]:
        tool = str(item.get("tool") or "").strip()
        if tool not in allowed:
            results.append(
                {
                    "tool": tool,
                    "arguments": item.get("arguments") or {},
                    "purpose": item.get("purpose"),
                    "status": "blocked",
                    "reason": "a recuperação adaptativa só pode executar ferramentas estruturadas de leitura",
                    "exit_code": 255,
                    "stdout": "",
                    "stderr": "",
                }
            )
            continue
        result = execute_tool(
            executor,
            environment,
            tool,
            dict(item.get("arguments") or {}),
            approved=False,
        )
        result["purpose"] = item.get("purpose") or result.get("purpose")
        result["recovery_diagnostic"] = True
        results.append(result)
    return results


def _replan(
    *,
    diagnosis: dict[str, Any],
    diagnostic_results: list[dict[str, Any]],
    action: dict[str, Any],
    action_result: dict[str, Any],
    scope: dict[str, Any],
    analysis: dict[str, Any],
    round_number: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "root_cause": analysis.get("root_cause") or analysis.get("probable_cause"),
        "recovery_goal": analysis.get("recovery_goal") or {},
        "failed_action": action,
        "action_result": action_result,
        "blocker_diagnosis": diagnosis,
        "diagnostic_results": _compact_evidence(diagnostic_results, limit=8),
        "approved_envelope": scope,
    }
    result, diagnostics = resilient_model_call(
        _RECOVERY_REPLAN_RULES
        + "\n\nDADOS PARA REPLANEJAMENTO:\n"
        + json.dumps(redact_object(payload), ensure_ascii=False, default=str),
        f"recovery_replan_round_{round_number}",
    )
    return result or {
        "root_blocker": diagnosis.get("blocker_summary") or "bloqueio não classificado",
        "causal_chain": [],
        "next_action": None,
        "requires_new_approval": False,
        "resolved": False,
        "stop": True,
        "stop_reason": "replanejamento adaptativo indisponível",
    }, diagnostics


def run_adaptive_recovery(
    *,
    executor: Any,
    environment: EnvironmentType,
    initial_actions: list[dict[str, Any]],
    analysis: dict[str, Any],
    evidence: list[dict[str, Any]],
    scope: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    queue = deque(dict(item) for item in initial_actions)
    allowed_corrections = set(scope.get("allowed_correction_tools") or [])
    attempts = Counter()
    action_results: list[dict[str, Any]] = []
    diagnostic_results: list[dict[str, Any]] = []
    rounds: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    ai_diagnostics: list[dict[str, Any]] = []
    pending_actions: list[dict[str, Any]] = []
    last_validated = False
    state = "correction_in_progress"

    while queue and len(action_results) < settings.agent_recovery_max_actions:
        action = queue.popleft()
        key = _action_key(action)
        attempts[key] += 1
        if attempts[key] > settings.agent_recovery_max_repeated_action:
            state = "stopped_loop_detected"
            blockers.append(
                {
                    "summary": "A mesma ação já falhou ou foi repetida além do limite seguro.",
                    "action": action,
                }
            )
            break

        tool = str(action.get("tool") or "").strip()
        if tool not in allowed_corrections:
            state = "awaiting_new_approval"
            pending_actions.append(
                {
                    **action,
                    "status": "new_approval_required",
                    "reason": "a ação não pertence ao envelope aprovado",
                }
            )
            break

        result = {
            **action,
            **execute_tool(
                executor,
                environment,
                tool,
                dict(action.get("arguments") or {}),
                approved=True,
            ),
            "recovery_round": len(rounds) + 1,
            "adaptive": bool(action.get("adaptive")),
        }
        action_results.append(result)
        last_validated = result.get("status") == "validated"
        rounds.append(
            {
                "round": len(rounds) + 1,
                "phase": "correction",
                "state": "validated" if last_validated else "new_blocker_found",
                "action": action,
                "result": result,
            }
        )
        if last_validated:
            continue

        if len(blockers) >= settings.agent_recovery_max_rounds:
            state = "failed_limit_reached"
            break

        recovery_round = len(blockers) + 1
        diagnosis, diagnosis_diag = _diagnose_blocker(
            action=action,
            action_result=result,
            analysis=analysis,
            evidence=[*evidence, *action_results, *diagnostic_results],
            round_number=recovery_round,
            settings=settings,
        )
        ai_diagnostics.append(diagnosis_diag)
        new_diagnostics = _execute_diagnostics(
            executor,
            environment,
            diagnosis,
            settings=settings,
        )
        diagnostic_results.extend(new_diagnostics)
        blocker = {
            "round": recovery_round,
            "summary": diagnosis.get("blocker_summary"),
            "new_symptom": diagnosis.get("new_symptom"),
            "causal_link": diagnosis.get("causal_link"),
            "hypotheses": diagnosis.get("hypotheses") or [],
            "failed_action": action,
            "diagnostic_results": new_diagnostics,
        }
        blockers.append(blocker)
        rounds.append(
            {
                "round": recovery_round,
                "phase": "blocker_diagnosis",
                "state": "mapped",
                **blocker,
            }
        )
        if diagnosis.get("stop"):
            state = "failed_no_safe_path"
            blocker["stop_reason"] = diagnosis.get("stop_reason")
            break

        replanned, replan_diag = _replan(
            diagnosis=diagnosis,
            diagnostic_results=new_diagnostics,
            action=action,
            action_result=result,
            scope=scope,
            analysis=analysis,
            round_number=recovery_round,
        )
        ai_diagnostics.append(replan_diag)
        blocker["root_blocker"] = replanned.get("root_blocker")
        blocker["causal_chain"] = replanned.get("causal_chain") or []
        next_action = replanned.get("next_action")
        if replanned.get("stop") or not isinstance(next_action, dict):
            state = "failed_no_safe_path"
            blocker["stop_reason"] = replanned.get("stop_reason") or "nenhuma ação segura foi proposta"
            break

        next_tool = str(next_action.get("tool") or "").strip()
        next_action = {
            **next_action,
            "tool": next_tool,
            "arguments": dict(next_action.get("arguments") or {}),
            "status": "proposed",
            "adaptive": True,
            "derived_from_blocker_round": recovery_round,
        }
        if replanned.get("requires_new_approval") or next_tool not in allowed_corrections:
            state = "awaiting_new_approval"
            pending_actions.append(
                {
                    **next_action,
                    "status": "new_approval_required",
                    "reason": "a nova ação está fora do envelope originalmente aprovado",
                }
            )
            break

        try:
            plan = resolve_tool(next_tool, next_action.get("arguments") or {})
            if not plan.correction:
                raise ValueError("a ferramenta proposta não é corretiva")
        except Exception as exc:
            state = "failed_no_safe_path"
            blocker["stop_reason"] = f"ação adaptativa inválida: {exc}"
            break

        adaptive_analysis = {
            "status": "attention",
            "confidence": analysis.get("confidence") or 0,
            "probable_cause": replanned.get("root_blocker") or diagnosis.get("blocker_summary"),
            "conclusion": diagnosis.get("causal_link"),
            "root_cause": analysis.get("root_cause") or {},
        }
        reviewer = review_corrections(
            adaptive_analysis,
            [next_action],
            [*evidence, *action_results, *diagnostic_results],
            settings=settings,
        )
        blocker["adaptive_review"] = reviewer
        if settings.ai_reviewer_required_for_corrections and not reviewer.get("approved"):
            state = "stopped_review_rejected"
            pending_actions.append(
                {
                    **next_action,
                    "status": "review_rejected",
                    "reason": reviewer.get("reason") or "segunda IA não aprovou a ação adaptativa",
                }
            )
            break
        queue.appendleft(next_action)
        state = "plan_adjusted"

    if queue and len(action_results) >= settings.agent_recovery_max_actions:
        state = "failed_limit_reached"

    if not pending_actions and not queue and action_results and last_validated:
        state = "resolved_and_validated"
    elif state in {"correction_in_progress", "plan_adjusted"}:
        state = "failed"

    if state == "resolved_and_validated":
        status = "validated"
    elif state == "awaiting_new_approval":
        status = "approval_required"
    elif any(item.get("status") == "validated" for item in action_results):
        status = "partially_validated"
    else:
        status = "failed"

    return redact_object(
        {
            "status": status,
            "state": state,
            "scope": scope,
            "results": action_results,
            "diagnostic_results": diagnostic_results,
            "rounds": rounds,
            "blockers": blockers,
            "pending_actions": pending_actions,
            "new_approval_required": bool(pending_actions),
            "ai_diagnostics": ai_diagnostics,
            "summary": (
                "A recuperação foi concluída e validada após observar cada ação."
                if status == "validated"
                else "A recuperação encontrou um novo bloqueio que exige outra autorização."
                if status == "approval_required"
                else "A recuperação avançou parcialmente, mas ainda existem bloqueios."
                if status == "partially_validated"
                else "A recuperação não conseguiu atingir um estado validado dentro dos limites seguros."
            ),
        }
    )
