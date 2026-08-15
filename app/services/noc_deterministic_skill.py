from __future__ import annotations

from typing import Any

from app.core.policies import EnvironmentType, environment_allows_correction
from app.core.settings import Settings
from app.services.checkmk_post_correction import collect_target_from_monitor
from app.services import noc_incidents as incident_store
from app.services.noc_skills import select_noc_skill
from app.services.redaction import redact_text
from app.services.site_scoped_execution import build_approved_execution_route
from app.services.tool_registry import execute_tool


# Procedures determinísticos da NOC Master Skill já validados em campo.
# A IA continua útil para enriquecer diagnóstico e causa, porém indisponibilidade
# do provedor não pode impedir uma correção cuja sequência já possui guardrails
# e validações funcionais determinísticas.
_DETERMINISTIC_SKILLS: dict[str, dict[str, Any]] = {
    "checkmk-systemd-socket-summary": {
        "tools": [
            {"tool": "checkmk.resolve_legacy_socket_conflict", "arguments": {}},
            {
                "tool": "systemd.recover_unit",
                "arguments": {"unit": "xinetd.service", "action": "enable --now"},
            },
            {
                "tool": "systemd.recover_unit",
                "arguments": {"unit": "xinetd.service", "action": "restart"},
            },
        ],
        "checkmk_post_collection": True,
        "require_no_failed_legacy_socket": True,
        "idempotent_local_state": True,
    },
}


_FAILED_LEGACY_SOCKET_CHECK = (
    "if systemctl --failed --type=socket --no-legend --plain 2>/dev/null "
    "| grep -Eq '(^|[[:space:]])check_mk\\.socket([[:space:]]|$)'; "
    "then echo 'LEGACY_SOCKET_FAILED=yes'; exit 1; "
    "else echo 'LEGACY_SOCKET_FAILED=no'; exit 0; fi"
)


_ALREADY_CORRECT_SOCKET_CHECK = (
    "x_active=$(systemctl is-active xinetd.service 2>/dev/null || true); "
    "x_enabled=$(systemctl is-enabled xinetd.service 2>/dev/null || true); "
    "legacy_active=$(systemctl is-active check_mk.socket 2>/dev/null || true); "
    "legacy_enabled=$(systemctl is-enabled check_mk.socket 2>/dev/null || true); "
    "echo \"XINETD_ACTIVE=$x_active XINETD_ENABLED=$x_enabled "
    "LEGACY_ACTIVE=$legacy_active LEGACY_ENABLED=$legacy_enabled\"; "
    "[ \"$x_active\" = 'active' ] || { echo 'LOCAL_STATE=NOT_READY reason=xinetd_not_active'; exit 1; }; "
    "[ \"$x_enabled\" = 'enabled' ] || { echo 'LOCAL_STATE=NOT_READY reason=xinetd_not_enabled'; exit 1; }; "
    "[ \"$legacy_active\" = 'inactive' ] || { echo 'LOCAL_STATE=NOT_READY reason=legacy_not_inactive'; exit 1; }; "
    "[ \"$legacy_enabled\" = 'disabled' ] || { echo 'LOCAL_STATE=NOT_READY reason=legacy_not_disabled'; exit 1; }; "
    "if systemctl --failed --type=socket --no-legend --plain 2>/dev/null "
    "| grep -Eq '(^|[[:space:]])check_mk\\.socket([[:space:]]|$)'; "
    "then echo 'LOCAL_STATE=NOT_READY reason=legacy_still_failed'; exit 1; fi; "
    "if ! ss -lntp 2>/dev/null | grep -E '(:|\\])6556[[:space:]]' | grep -qi xinetd; "
    "then echo 'LOCAL_STATE=NOT_READY reason=xinetd_not_owner_6556'; exit 1; fi; "
    "if ! timeout 15 bash -c 'exec 3<>/dev/tcp/127.0.0.1/6556; head -n 40 <&3' 2>/dev/null "
    "| grep -q '<<<check_mk>>>'; "
    "then echo 'LOCAL_STATE=NOT_READY reason=agent_payload_invalid'; exit 1; fi; "
    "echo 'LOCAL_STATE=ALREADY_CORRECT'; exit 0"
)


def _environment(incident: dict[str, Any], result: dict[str, Any]) -> EnvironmentType:
    # Para fallback determinístico preferimos o ambiente vindo do inventário do
    # incidente. A classificação da IA pode estar justamente indisponível.
    candidates = [
        incident.get("environment"),
        (result.get("environment_classification") or {}).get("environment"),
    ]
    for raw in candidates:
        if hasattr(raw, "value"):
            raw = raw.value
        try:
            environment = EnvironmentType(str(raw or "").strip().casefold())
        except ValueError:
            continue
        if environment != EnvironmentType.UNKNOWN:
            return environment
    return EnvironmentType.UNKNOWN


def _persist_known_cause(
    incident: dict[str, Any],
    *,
    skill_id: str,
    conclusion: str,
    settings: Settings,
) -> None:
    incident_id = str(incident.get("id") or "").strip()
    if not incident_id:
        return
    try:
        client = incident_store._redis(settings)
        current = incident_store._load(client, settings, incident_id)
        if not current:
            return
        current.update(
            {
                "analysis_status": "attention",
                "confidence": 100,
                "probable_cause": (
                    "Conflito/estado legado do agente Checkmk identificado em torno de check_mk.socket e xinetd/6556."
                ),
                "conclusion": conclusion,
                "deterministic_skill": skill_id,
                "master_skill": "noc-master",
                "procedure_id": skill_id,
            }
        )
        incident_store._store(client, settings, current)
    except Exception:
        return


def deterministic_skill_for_incident(incident: dict[str, Any]) -> dict[str, Any] | None:
    skill = select_noc_skill(
        {
            "site_id": incident.get("site") or incident.get("site_id"),
            "host": incident.get("host"),
            "host_address": incident.get("host_address"),
            "service": incident.get("service"),
            "state_name": incident.get("current_state"),
            "output": incident.get("last_output"),
        }
    )
    skill_id = str(skill.get("id") or "").strip()
    specification = _DETERMINISTIC_SKILLS.get(skill_id)
    if not specification:
        return None
    return {**skill, "deterministic": specification}


def is_ai_dependency_failure(reason: str) -> bool:
    text = str(reason or "").casefold()
    return any(
        token in text
        for token in (
            "confiança da análise abaixo",
            "classificação do ambiente abaixo",
            "segunda ia não aprovou",
            "não gerou token",
            "nenhuma ação corretiva foi proposta",
        )
    )


def _read_check(
    executor: Any,
    environment: EnvironmentType,
    command: str,
    *,
    purpose: str,
    timeout: int = 45,
) -> dict[str, Any]:
    try:
        value = executor.run_sudo(command, environment, timeout=timeout)
        return {
            "tool": "checkmk.deterministic_validation",
            "purpose": purpose,
            "command": command,
            "status": "validated" if int(value.exit_code or 0) == 0 else "failed",
            "exit_code": int(value.exit_code or 0),
            "stdout": redact_text(str(value.stdout or "")),
            "stderr": redact_text(str(value.stderr or "")),
        }
    except Exception as exc:
        return {
            "tool": "checkmk.deterministic_validation",
            "purpose": purpose,
            "command": command,
            "status": "failed",
            "exit_code": 255,
            "stdout": "",
            "stderr": redact_text(f"{type(exc).__name__}: {exc}"),
        }


def run_deterministic_skill_correction(
    incident: dict[str, Any],
    result: dict[str, Any],
    *,
    settings: Settings,
) -> dict[str, Any] | None:
    """Executa um procedure comprovado da NOC Master Skill.

    O fluxo é idempotente. Se o TARGET_HOST já estiver exatamente no estado
    desejado (xinetd active/enabled, check_mk.socket inactive/disabled, xinetd
    dono da 6556, agente válido e nenhuma unit legada FAILED), nenhuma alteração
    local é repetida: o procedure segue direto para a revalidação no Checkmk.
    """

    skill = deterministic_skill_for_incident(incident)
    if not skill:
        return None
    skill_id = str(skill.get("id") or "")
    specification = dict(skill.get("deterministic") or {})
    actions = [item for item in specification.get("tools") or [] if isinstance(item, dict)]
    if not actions:
        return None

    environment = _environment(incident, result)
    if not environment_allows_correction(environment):
        return {
            "status": "blocked",
            "state": "environment_not_allowed",
            "reason": f"ambiente {environment.value} não permite correção determinística",
            "deterministic_skill": skill_id,
            "results": [],
        }

    global_tools = {
        item.strip()
        for item in str(settings.noc_self_heal_tools or "").split(",")
        if item.strip()
    }
    requested_tools = {
        str(item.get("tool") or "").strip()
        for item in actions
        if str(item.get("tool") or "").strip()
    }
    if not requested_tools or not requested_tools.issubset(global_tools):
        missing = sorted(requested_tools - global_tools)
        return {
            "status": "blocked",
            "state": "tool_not_allowed",
            "reason": (
                "o procedure determinístico exige ferramenta fora da allowlist NOC"
                + (f": {', '.join(missing)}" if missing else "")
            ),
            "deterministic_skill": skill_id,
            "results": [],
        }

    analysis = dict(result.get("analysis") or {})
    scope = dict(analysis.get("site_scope") or {})
    if not scope.get("isolated") or not scope.get("same_site_only") or not scope.get("site_id") or not scope.get("entry_address"):
        return {
            "status": "blocked",
            "state": "site_scope_missing",
            "reason": "a investigação não preservou uma rota isolada cliente/site para a correção",
            "deterministic_skill": skill_id,
            "results": [],
        }

    try:
        route = build_approved_execution_route(
            {"target": result.get("target") or scope.get("internal_address") or incident.get("host")},
            analysis,
            environment=environment,
            approved_ssh_port=22,
            settings=settings,
        )
    except Exception as exc:
        return {
            "status": "failed",
            "state": "route_error",
            "reason": f"não foi possível reconstruir a rota segura do procedure: {type(exc).__name__}: {exc}",
            "deterministic_skill": skill_id,
            "results": [],
        }

    if not route.site_scoped:
        try:
            route.executor.close()
        except Exception:
            pass
        return {
            "status": "blocked",
            "state": "route_not_site_scoped",
            "reason": "procedure determinístico exige rota site-scoped; acesso direto foi recusado",
            "deterministic_skill": skill_id,
            "results": [],
        }

    executor = route.executor
    results: list[dict[str, Any]] = []
    local_already_correct = False
    try:
        executor.connect()

        diagnosis = execute_tool(
            executor,
            environment,
            "checkmk.inspect_agent_socket",
            {},
            approved=False,
        )
        results.append(diagnosis)

        if specification.get("idempotent_local_state"):
            desired_state = _read_check(
                executor,
                environment,
                _ALREADY_CORRECT_SOCKET_CHECK,
                purpose=(
                    "detectar estado local já corrigido: xinetd active/enabled, check_mk.socket "
                    "inactive/disabled, 6556 no xinetd, agente válido e sem socket legado FAILED"
                ),
                timeout=45,
            )
            desired_state["stage"] = "local_idempotency_check"
            results.append(desired_state)
            local_already_correct = (
                desired_state.get("status") == "validated"
                and "LOCAL_STATE=ALREADY_CORRECT" in str(desired_state.get("stdout") or "")
            )

        if not local_already_correct:
            for action in actions:
                correction = execute_tool(
                    executor,
                    environment,
                    str(action.get("tool") or ""),
                    dict(action.get("arguments") or {}),
                    approved=True,
                )
                results.append(correction)
                if str(correction.get("status") or "") != "validated":
                    return {
                        "status": "failed" if correction.get("status") == "failed" else "blocked",
                        "state": "deterministic_preconditions_not_met",
                        "reason": str(
                            correction.get("reason")
                            or f"a etapa {action.get('tool')} não foi validada; o procedure foi interrompido"
                        ),
                        "deterministic_skill": skill_id,
                        "results": results,
                        "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
                    }

            after_snapshot = execute_tool(
                executor,
                environment,
                "checkmk.inspect_agent_socket",
                {},
                approved=False,
            )
            results.append(after_snapshot)
            if str(after_snapshot.get("status") or "") not in {"executed", "validated"} or int(after_snapshot.get("exit_code") or 0) != 0:
                return {
                    "status": "failed",
                    "state": "agent_post_validation_failed",
                    "reason": "não foi possível obter a validação pós-correção do socket/agent",
                    "deterministic_skill": skill_id,
                    "results": results,
                    "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
                }

        if specification.get("require_no_failed_legacy_socket"):
            failed_check = _read_check(
                executor,
                environment,
                _FAILED_LEGACY_SOCKET_CHECK,
                purpose="confirmar que check_mk.socket não permanece em systemctl --failed --type=socket",
            )
            results.append(failed_check)
            if failed_check.get("status") != "validated":
                return {
                    "status": "failed",
                    "state": "legacy_socket_still_failed",
                    "reason": "check_mk.socket ainda aparece entre os sockets em falha após a correção",
                    "deterministic_skill": skill_id,
                    "results": results,
                    "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
                }

        _persist_known_cause(
            incident,
            skill_id=skill_id,
            conclusion=(
                "O TARGET_HOST já estava no estado local correto; nenhuma alteração redundante foi executada. "
                "A validação seguiu diretamente para o MONITORING_HOST/Checkmk."
                if local_already_correct
                else
                "O procedure confirmou o conflito, desabilitou check_mk.socket, limpou o FAILED, garantiu "
                "xinetd habilitado, reiniciou xinetd e validou novamente a TCP/6556. A validação continua no Checkmk."
            ),
            settings=settings,
        )

        if specification.get("checkmk_post_collection"):
            target_host = str(scope.get("host_name") or incident.get("host") or "").strip()
            target_address = str(
                scope.get("internal_address")
                or incident.get("host_address")
                or ""
            ).strip() or None
            post_collection = collect_target_from_monitor(
                executor,
                target_host,
                target_address,
            )
            results.append(post_collection)
            if str(post_collection.get("status") or "") != "validated":
                _persist_known_cause(
                    incident,
                    skill_id=skill_id,
                    conclusion=(
                        "O estado local do agente foi validado, porém o MONITORING_HOST não confirmou o acesso "
                        "funcional ao agente e/ou a nova coleta no Checkmk. O incidente permanece aberto."
                    ),
                    settings=settings,
                )
                return {
                    "status": "failed",
                    "state": "checkmk_post_collection_failed",
                    "reason": "estado local validado, mas a validação do agente/coleta a partir do monitor falhou",
                    "deterministic_skill": skill_id,
                    "local_already_correct": local_already_correct,
                    "results": results,
                    "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
                }

        _persist_known_cause(
            incident,
            skill_id=skill_id,
            conclusion=(
                "TARGET_HOST validado; MONITORING_HOST alcançou a TCP/6556 e executou a nova coleta no CHECKMK_SITE "
                "do mesmo cliente. O incidente só será marcado como resolvido quando o Livestatus confirmar OK."
            ),
            settings=settings,
        )
        return {
            "status": "validated",
            "state": "deterministic_skill_validated",
            "summary": (
                "Estado local já estava correto; revalidação Checkmk executada."
                if local_already_correct
                else "Sequência conhecida executada e validada até a nova coleta Checkmk."
            ),
            "deterministic_skill": skill_id,
            "master_skill": "noc-master",
            "procedure_id": skill_id,
            "local_already_correct": local_already_correct,
            "results": results,
            "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
            "new_approval_required": False,
            "pending_actions": [],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "state": "deterministic_execution_error",
            "reason": f"{type(exc).__name__}: {exc}",
            "deterministic_skill": skill_id,
            "local_already_correct": local_already_correct,
            "results": results,
            "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
        }
    finally:
        executor.close()
