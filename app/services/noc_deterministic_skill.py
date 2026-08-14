from __future__ import annotations

from typing import Any

from app.core.policies import EnvironmentType, environment_allows_correction
from app.core.settings import Settings
from app.services.checkmk_post_correction import collect_target_from_monitor
from app.services import noc_incidents as incident_store
from app.services.noc_skills import select_noc_skill
from app.services.site_scoped_execution import build_approved_execution_route
from app.services.tool_registry import execute_tool


# Skills desta tabela representam correções operacionais já validadas em campo.
# A IA continua útil para enriquecer diagnóstico e causa, porém indisponibilidade
# do provedor não pode impedir uma correção cuja ferramenta já possui
# pré-condições funcionais duras e política restrita no próprio código.
_DETERMINISTIC_SKILLS: dict[str, dict[str, Any]] = {
    "checkmk-systemd-socket-summary": {
        "tools": [
            {"tool": "checkmk.resolve_legacy_socket_conflict", "arguments": {}},
        ],
        "checkmk_post_collection": True,
    },
}


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
                    "Conflito legado do agente Checkmk confirmado: check_mk.socket em falha enquanto "
                    "xinetd permanece funcional e atende a porta TCP/6556."
                ),
                "conclusion": conclusion,
                "deterministic_skill": skill_id,
            }
        )
        incident_store._store(client, settings, current)
    except Exception:
        # Falha ao enriquecer o histórico nunca deve transformar uma correção
        # tecnicamente validada em falha operacional.
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


def run_deterministic_skill_correction(
    incident: dict[str, Any],
    result: dict[str, Any],
    *,
    settings: Settings,
) -> dict[str, Any] | None:
    """Executa uma Skill comprovada quando a camada de IA está indisponível.

    A função nunca transforma o problema em um comando shell livre. Ela só usa
    ferramentas estruturadas já cadastradas, exige rota site-scoped persistida,
    respeita a allowlist NOC e deixa as pré-condições da própria ferramenta
    decidirem se a alteração é realmente aplicável.
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
        return {
            "status": "blocked",
            "state": "tool_not_allowed",
            "reason": "a Skill determinística exige ferramenta fora da allowlist NOC",
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
            "reason": f"não foi possível reconstruir a rota segura da Skill: {type(exc).__name__}: {exc}",
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
            "reason": "Skill determinística exige rota site-scoped; acesso direto foi recusado",
            "deterministic_skill": skill_id,
            "results": [],
        }

    executor = route.executor
    results: list[dict[str, Any]] = []
    try:
        executor.connect()
        # Snapshot funcional antes da alteração. Não depende de IA.
        diagnosis = execute_tool(
            executor,
            environment,
            "checkmk.inspect_agent_socket",
            {},
            approved=False,
        )
        results.append(diagnosis)

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
                    "reason": str(correction.get("reason") or "pré-condições da Skill não confirmaram que a correção é aplicável"),
                    "deterministic_skill": skill_id,
                    "results": results,
                    "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
                }

        _persist_known_cause(
            incident,
            skill_id=skill_id,
            conclusion=(
                "A Skill determinística confirmou as pré-condições e aplicou a limpeza segura de check_mk.socket, "
                "preservando o xinetd funcional. Nova coleta no servidor de monitoramento em andamento."
            ),
            settings=settings,
        )

        if specification.get("checkmk_post_collection"):
            target_host = str(scope.get("host_name") or incident.get("host") or "").strip()
            post_collection = collect_target_from_monitor(executor, target_host)
            results.append(post_collection)
            if str(post_collection.get("status") or "") != "validated":
                _persist_known_cause(
                    incident,
                    skill_id=skill_id,
                    conclusion=(
                        "O conflito local foi corrigido pela Skill determinística, porém a nova coleta no Checkmk "
                        "não foi validada; o incidente permanece aberto para continuidade."
                    ),
                    settings=settings,
                )
                return {
                    "status": "failed",
                    "state": "checkmk_post_collection_failed",
                    "reason": "correção local aplicada, mas a nova coleta no Checkmk não foi validada",
                    "deterministic_skill": skill_id,
                    "results": results,
                    "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
                }

        _persist_known_cause(
            incident,
            skill_id=skill_id,
            conclusion=(
                "Conflito legado corrigido no TARGET_HOST e nova coleta validada no MONITORING_HOST/CHECKMK_SITE "
                "do mesmo cliente. Aguardando apenas a confirmação final do estado do sensor no Livestatus."
            ),
            settings=settings,
        )
        return {
            "status": "validated",
            "state": "deterministic_skill_validated",
            "summary": "Skill conhecida executada e validada sem depender do provedor de IA.",
            "deterministic_skill": skill_id,
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
            "results": results,
            "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
        }
    finally:
        executor.close()
