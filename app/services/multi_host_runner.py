from __future__ import annotations

import json
import time
import uuid
from typing import Any, Iterable

from sqlalchemy import select

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.db.base import SessionLocal, ensure_database_schema
from app.db.models import InvestigationORM
from app.services.ai_providers import get_provider, use_provider
from app.services.customer_topology import (
    get_customer_topology,
    mark_route_verified,
    reachable_nodes,
    save_customer_scope,
    select_automatic_related_nodes,
)
from app.services.intelligent_agent import run_dynamic_investigation
from app.services.inventory_learning import learn_result_inventory
from app.services.investigation_insights import enrich_investigation_result
from app.services.incident_orchestration import enrich_incident_intelligence
from app.services.nested_ssh import NestedSSHExecutor
from app.services.playbooks import selected_playbook_ssh_port, use_playbook
from app.services.progress import report_progress
from app.services.redaction import redact_object
from app.services.result_presentation import finalize_result_presentation
from app.services.runner import (
    _automation_summary,
    _explicit_provider_resolution,
    build_executor,
    resolve_target,
)
from app.services.provider_router import resolve_automatic_provider
from app.services.vpn_menu_ssh import VPNMenuSSHExecutor


_STATUS_WEIGHT = {"critical": 40, "attention": 30, "inconclusive": 20, "healthy": 10}


def _environment(value: Any) -> EnvironmentType:
    try:
        return EnvironmentType(str(value or EnvironmentType.UNKNOWN.value))
    except ValueError:
        return EnvironmentType.UNKNOWN


def _clean_targets(values: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for raw in values or []:
        item = dict(raw or {})
        reference = str(item.get("reference") or item.get("address") or "").strip()
        if not reference:
            continue
        port = int(item.get("ssh_port") or 22)
        key = (reference.casefold(), port)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                **item,
                "reference": reference,
                "address": reference,
                "ssh_port": port,
                "role": str(item.get("role") or "other").strip().casefold(),
                "environment": str(item.get("environment") or "unknown").strip().casefold(),
                "via": str(item.get("via") or "").strip() or None,
                "label": str(item.get("label") or "").strip() or None,
            }
        )
    return rows[:8]


def _compact_host_result(result: dict[str, Any], node: dict[str, Any], *, primary: bool) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    connection = dict(result.get("connection") or {})
    return {
        "investigation_id": result.get("investigation_id"),
        "primary": primary,
        "address": node.get("address") or result.get("target"),
        "hostname": result.get("hostname") or node.get("hostname"),
        "label": node.get("label"),
        "role": node.get("role") or "other",
        "environment": (result.get("environment_classification") or {}).get("environment")
        or node.get("environment")
        or "unknown",
        "status": analysis.get("status") or "inconclusive",
        "confidence": int(analysis.get("confidence") or 0),
        "summary": analysis.get("summary"),
        "probable_cause": analysis.get("probable_cause"),
        "conclusion": analysis.get("conclusion"),
        "facts": list(analysis.get("facts") or [])[:12],
        "recommendations": list(analysis.get("recommendations") or [])[:8],
        "evidence_count": len(result.get("evidence") or []),
        "connection": connection,
    }


def _fallback_synthesis(
    objective: str,
    host_results: list[dict[str, Any]],
    handoffs: list[dict[str, Any]],
) -> dict[str, Any]:
    ranked = sorted(
        host_results,
        key=lambda item: (
            _STATUS_WEIGHT.get(str(item.get("status") or "inconclusive"), 0)
            + int(item.get("confidence") or 0)
        ),
        reverse=True,
    )
    root = ranked[0] if ranked else {}
    facts: list[str] = []
    recommendations: list[str] = []
    for item in host_results:
        prefix = item.get("label") or item.get("hostname") or item.get("address") or "host"
        facts.extend(f"[{prefix}] {fact}" for fact in item.get("facts") or [])
        recommendations.extend(
            f"[{prefix}] {recommendation}" for recommendation in item.get("recommendations") or []
        )
    summary = (
        f"A investigação percorreu {len(host_results)} host(s) relacionados. "
        f"O achado com maior sustentação ocorreu em {root.get('hostname') or root.get('address') or 'host não identificado'}."
    )
    return {
        "status": root.get("status") or "inconclusive",
        "confidence": int(root.get("confidence") or 0),
        "summary": summary,
        "facts": facts[:24],
        "probable_cause": root.get("probable_cause") or "Nenhuma causa consolidada foi confirmada.",
        "conclusion": root.get("conclusion") or "A investigação multi-host terminou sem conclusão consolidada.",
        "recommendations": list(dict.fromkeys(recommendations))[:16],
        "root_host": root.get("address"),
        "handoffs": handoffs,
        "synthesis_source": "deterministic_fallback",
    }


def _synthesize(
    objective: str,
    host_results: list[dict[str, Any]],
    handoffs: list[dict[str, Any]],
    *,
    provider_name: str,
    model_name: str | None,
    settings: Settings,
) -> dict[str, Any]:
    fallback = _fallback_synthesis(objective, host_results, handoffs)
    payload = {
        "objective": objective,
        "hosts": host_results,
        "handoffs": handoffs,
        "rules": {
            "do_not_invent": True,
            "same_customer_only": True,
            "production_and_standby_read_only": True,
            "customer_databases_forbidden": True,
        },
    }
    prompt = (
        "Você é o sintetizador final de uma investigação AIOps multi-host. Responda somente JSON válido.\n"
        "Compare as análises por host e determine em qual host está a causa mais sustentada.\n"
        "Não invente fatos, não trate recomendação como evidência e não confunda alerta derivado com causa.\n"
        "Explique as trocas de host. Produção e standby são somente leitura e bancos de clientes não podem ser acessados.\n"
        "Formato: {\"status\":\"healthy|attention|critical|inconclusive\",\"confidence\":0,"
        "\"summary\":\"...\",\"facts\":[\"...\"],\"probable_cause\":\"...\","
        "\"conclusion\":\"...\",\"recommendations\":[\"...\"],\"root_host\":\"...\"}.\n\n"
        "DADOS:\n" + json.dumps(redact_object(payload), ensure_ascii=False, default=str)
    )
    try:
        provider = get_provider(provider_name, settings, model_name)
        result, _metadata = provider.generate_json(prompt)
        if not isinstance(result, dict):
            return fallback
        status = str(result.get("status") or "")
        confidence = int(result.get("confidence") or 0)
        if status not in _STATUS_WEIGHT or not 0 <= confidence <= 100:
            return fallback
        return {
            "status": status,
            "confidence": confidence,
            "summary": str(result.get("summary") or fallback["summary"]),
            "facts": [str(item) for item in result.get("facts") or fallback["facts"]][:24],
            "probable_cause": str(result.get("probable_cause") or fallback["probable_cause"]),
            "conclusion": str(result.get("conclusion") or fallback["conclusion"]),
            "recommendations": [str(item) for item in result.get("recommendations") or fallback["recommendations"]][:16],
            "root_host": str(result.get("root_host") or fallback.get("root_host") or ""),
            "handoffs": handoffs,
            "synthesis_source": "ai",
        }
    except Exception:
        return fallback


def _tag_evidence(result: dict[str, Any], node: dict[str, Any]) -> list[dict[str, Any]]:
    tagged: list[dict[str, Any]] = []
    for raw in result.get("evidence") or []:
        if not isinstance(raw, dict):
            continue
        tagged.append(
            {
                **raw,
                "source_host": node.get("address") or result.get("target"),
                "source_hostname": result.get("hostname") or node.get("hostname"),
                "source_role": node.get("role") or "other",
                "source_environment": node.get("environment") or "unknown",
                "access_path": (result.get("connection") or {}).get("mode") or "unknown",
            }
        )
    return tagged


def _persist_logical_investigation(
    root_id: str,
    root_result: dict[str, Any],
    children: list[dict[str, Any]],
    synthesis: dict[str, Any],
    multi_host: dict[str, Any],
) -> None:
    try:
        root_uuid = uuid.UUID(str(root_id))
    except ValueError:
        return
    ensure_database_schema()
    with SessionLocal() as session:
        root = session.get(InvestigationORM, root_uuid)
        if not root:
            return
        analysis = dict(root.analysis or {})
        analysis.update(
            {
                "status": synthesis["status"],
                "confidence": synthesis["confidence"],
                "summary": synthesis["summary"],
                "facts": synthesis["facts"],
                "probable_cause": synthesis["probable_cause"],
                "conclusion": synthesis["conclusion"],
                "recommendations": synthesis["recommendations"],
                "multi_host": multi_host,
                "next_safe_step": (
                    synthesis["recommendations"][0]
                    if synthesis.get("recommendations")
                    else "Revisar as evidências por host antes de qualquer correção."
                ),
            }
        )
        analysis.pop("approval", None)
        for item in analysis.get("proposed_actions") or []:
            if isinstance(item, dict) and item.get("status") == "proposed":
                item["status"] = "multi_host_read_only"
                item["reason"] = "a investigação percorreu múltiplos hosts e exige definição posterior do alvo corretivo"
        root.analysis = redact_object(analysis)
        root.status = synthesis["status"]
        root.confidence = synthesis["confidence"]
        root.duration_ms = int(root_result.get("duration_ms") or 0) + sum(
            int(item.get("duration_ms") or 0) for item in children
        )
        root.evidence = redact_object(root_result.get("evidence") or [])
        root.plans = redact_object(root_result.get("plans") or [])
        root.assessments = redact_object(root_result.get("round_assessments") or [])
        root.diagnostics = redact_object(root_result.get("ai_diagnostics") or [])

        for child in children:
            try:
                child_uuid = uuid.UUID(str(child.get("investigation_id")))
            except (ValueError, TypeError):
                continue
            row = session.get(InvestigationORM, child_uuid)
            if not row:
                continue
            child_analysis = dict(row.analysis or {})
            child_analysis["multi_host_parent_id"] = str(root_uuid)
            child_analysis["multi_host_child"] = True
            row.analysis = redact_object(child_analysis)
        session.commit()


def run_multi_host_tracked(
    reference: str,
    objective: str,
    *,
    environment: EnvironmentType = EnvironmentType.UNKNOWN,
    mode: str = "propose",
    approve: bool = False,
    ssh_port: int | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
    playbook_mode: str = "auto",
    playbook_id: str | None = None,
    customer_name: str | None = None,
    related_targets: Iterable[dict[str, Any]] | None = None,
    auto_expand_scope: bool = True,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Investiga vários hosts da mesma empresa reutilizando a sessão de entrada."""
    del approve  # multi-host permanece somente leitura
    settings = settings or get_settings()
    started = time.monotonic()
    explicit_targets = _clean_targets(related_targets)
    requested_provider = str(provider_name or settings.ai_provider or "gemini").strip().lower()
    selection = (
        resolve_automatic_provider(settings)
        if requested_provider == "auto"
        else _explicit_provider_resolution(provider_name, model_name, settings)
    )

    with use_provider(selection.provider, selection.model), use_playbook(
        "auto" if requested_provider == "auto" else playbook_mode,
        None if requested_provider == "auto" else playbook_id,
    ):
        playbook_ssh_port, selected_playbook_id = selected_playbook_ssh_port(
            objective.strip() or "validar a saúde geral do ambiente"
        )
        target = resolve_target(
            reference,
            environment,
            ssh_port,
            playbook_ssh_port=playbook_ssh_port,
            settings=settings,
        )
        executor = build_executor(target, settings=settings)
        if not isinstance(executor, VPNMenuSSHExecutor):
            raise RuntimeError(
                "a investigação multi-host requer SSH_ACCESS_MODE=vpn_menu para reutilizar o servidor de entrada"
            )

        report_progress(
            "multi_host_scope",
            detail="Abrindo uma única sessão VPN para o escopo multi-host.",
            percent=20,
            requested_hosts=1 + len(explicit_targets),
        )
        children: list[dict[str, Any]] = []
        handoffs: list[dict[str, Any]] = []
        try:
            executor.connect()
            connection = dict(getattr(executor, "connection_metadata", {}) or {})
            report_progress(
                "multi_host_primary",
                detail=f"Investigando o host de entrada {target.host}:{target.port}.",
                percent=34,
            )
            primary_result = run_dynamic_investigation(
                executor=executor,
                target=reference,
                context=objective,
                environment=target.environment,
                mode="investigate",
                approve=False,
            )
            primary_result["connection"] = connection
            primary_result["provider_selection"] = selection.as_dict()
            primary_result["selected_provider"] = selection.provider
            primary_result["selected_model"] = selection.model
            primary_result["approval_token"] = None
            primary_result["automation"] = _automation_summary(
                selection=selection,
                target=target,
                result=primary_result,
            )
            inventory = learn_result_inventory(
                primary_result,
                resolved_host=target.host,
                ssh_port=int(connection.get("ssh_port") or target.port),
                settings=settings,
            )
            primary_result["inventory"] = inventory

            resolved_customer = (
                str(customer_name or "").strip()
                or str(connection.get("client_name") or "").strip()
                or str(primary_result.get("hostname") or reference).strip()
            )
            primary_payload = {
                "address": target.host,
                "ssh_port": int(connection.get("ssh_port") or target.port),
                "hostname": primary_result.get("hostname"),
                "label": connection.get("client_name") or primary_result.get("hostname"),
                "role": (
                    "monitoring"
                    if target.environment == EnvironmentType.MONITORING
                    else target.environment.value
                    if target.environment.value in {"production", "standby"}
                    else "other"
                ),
                "environment": target.environment.value,
                "direct_vpn": True,
                "metadata": {"source": "vpn_menu", "reference": reference},
            }
            topology = save_customer_scope(
                resolved_customer,
                primary=primary_payload,
                related_targets=explicit_targets,
            )
            reachable = reachable_nodes(topology, target.host, max_hops=1)
            explicit_keys = {
                (str(item.get("reference") or "").casefold(), int(item.get("ssh_port") or 22))
                for item in explicit_targets
            }
            candidates = [
                item
                for item in reachable
                if (str(item.get("address") or "").casefold(), int(item.get("ssh_port") or 22))
                in explicit_keys
            ]
            if auto_expand_scope:
                automatic = select_automatic_related_nodes(
                    primary_result,
                    topology,
                    target.host,
                    max_hosts=3,
                )
                candidates.extend(item for item in automatic if int(item.get("hops") or 1) == 1)

            deduplicated: list[dict[str, Any]] = []
            seen_nodes: set[str] = set()
            for item in candidates:
                node_id = str(item.get("id") or "")
                if not node_id or node_id in seen_nodes:
                    continue
                seen_nodes.add(node_id)
                deduplicated.append(item)
                if len(deduplicated) >= 3:
                    break

            primary_node = {
                "address": target.host,
                "hostname": primary_result.get("hostname"),
                "label": connection.get("client_name"),
                "role": primary_payload["role"],
                "environment": target.environment.value,
            }
            host_results = [_compact_host_result(primary_result, primary_node, primary=True)]
            combined_evidence = _tag_evidence(primary_result, primary_node)
            combined_plans = list(primary_result.get("plans") or [])
            combined_assessments = list(primary_result.get("round_assessments") or [])
            combined_diagnostics = list(primary_result.get("ai_diagnostics") or [])

            for index, node in enumerate(deduplicated, start=1):
                reason = str(
                    node.get("selection_reason")
                    or "Host incluído manualmente no escopo da investigação."
                )
                handoff = {
                    "from": target.host,
                    "to": node.get("address"),
                    "role": node.get("role"),
                    "reason": reason,
                    "status": "running",
                }
                handoffs.append(handoff)
                report_progress(
                    "multi_host_handoff",
                    detail=f"Mudando a coleta para {node.get('label') or node.get('address')}: {reason}",
                    percent=min(82, 48 + index * 12),
                    from_host=target.host,
                    to_host=node.get("address"),
                    host_role=node.get("role"),
                )
                route = dict(node.get("route") or {})
                nested = NestedSSHExecutor(
                    executor,
                    host=str(node.get("address")),
                    port=int(node.get("ssh_port") or 22),
                    username=str(route.get("username") or settings.ssh_default_user),
                    password=executor.password,
                    route={**route, "hops": node.get("hops"), "route_path": node.get("route_path")},
                    connect_timeout=settings.ssh_connect_timeout,
                    strict_host_key_checking=settings.ssh_strict_host_key_checking,
                )
                try:
                    nested.connect()
                    child_context = (
                        f"OBJETIVO GERAL: {objective}\n\n"
                        f"HOST ATUAL: função {node.get('role') or 'other'}, endereço {node.get('address')}.\n"
                        f"MOTIVO DA TROCA DE HOST: {reason}\n"
                        "Investigue somente este host e relacione os achados ao objetivo geral."
                    )
                    child_result = run_dynamic_investigation(
                        executor=nested,
                        target=str(node.get("address")),
                        context=child_context,
                        environment=_environment(node.get("environment")),
                        mode="investigate",
                        approve=False,
                    )
                    child_result["connection"] = dict(nested.connection_metadata)
                    child_result["selected_provider"] = selection.provider
                    child_result["selected_model"] = selection.model
                    child_result["approval_token"] = None
                    child_result["topology_node"] = node
                    child_result["handoff_reason"] = reason
                    children.append(child_result)
                    host_results.append(_compact_host_result(child_result, node, primary=False))
                    combined_evidence.extend(_tag_evidence(child_result, node))
                    combined_plans.extend(child_result.get("plans") or [])
                    combined_assessments.extend(child_result.get("round_assessments") or [])
                    combined_diagnostics.extend(child_result.get("ai_diagnostics") or [])
                    handoff["status"] = "completed"
                    handoff["investigation_id"] = child_result.get("investigation_id")
                    if route.get("id"):
                        mark_route_verified(str(route["id"]))
                except Exception as exc:
                    handoff["status"] = "failed"
                    handoff["error"] = f"{type(exc).__name__}: {exc}"
                finally:
                    nested.close()

            synthesis = _synthesize(
                objective,
                host_results,
                handoffs,
                provider_name=selection.provider,
                model_name=selection.model,
                settings=settings,
            )
            multi_host = {
                "enabled": True,
                "read_only": True,
                "customer": topology.get("customer"),
                "entry_host": primary_node,
                "hosts": host_results,
                "handoffs": handoffs,
                "root_host": synthesis.get("root_host"),
                "requested_mode": mode,
                "effective_mode": "investigate",
                "auto_expand_scope": bool(auto_expand_scope),
                "limits": {"max_internal_hops": 1, "max_related_hosts": 3},
                "safety": {
                    "corrections": "blocked_until_single_target_review",
                    "production": "read_only",
                    "standby": "read_only",
                    "customer_databases": "blocked",
                },
            }
            analysis = dict(primary_result.get("analysis") or {})
            analysis.update(
                {
                    "status": synthesis["status"],
                    "confidence": synthesis["confidence"],
                    "summary": synthesis["summary"],
                    "facts": synthesis["facts"],
                    "probable_cause": synthesis["probable_cause"],
                    "conclusion": synthesis["conclusion"],
                    "recommendations": synthesis["recommendations"],
                    "multi_host": multi_host,
                }
            )
            analysis.pop("approval", None)
            primary_result["analysis"] = analysis
            primary_result["status"] = synthesis["status"]
            primary_result["confidence"] = synthesis["confidence"]
            primary_result["evidence"] = combined_evidence
            primary_result["plans"] = combined_plans
            primary_result["round_assessments"] = combined_assessments
            primary_result["ai_diagnostics"] = combined_diagnostics
            primary_result["multi_host"] = multi_host
            primary_result["child_investigations"] = [
                item.get("investigation_id") for item in children if item.get("investigation_id")
            ]
            primary_result["duration_ms"] = int((time.monotonic() - started) * 1000)
            primary_result["approval_token"] = None

            _persist_logical_investigation(
                str(primary_result.get("investigation_id")),
                primary_result,
                children,
                synthesis,
                multi_host,
            )
            enrich_investigation_result(primary_result, settings=settings)
            enrich_incident_intelligence(primary_result)
            finalize_result_presentation(primary_result, settings=settings)
            report_progress(
                "completed",
                status="completed",
                detail=f"Investigação multi-host concluída em {len(host_results)} host(s) da mesma empresa.",
                percent=100,
                investigation_id=primary_result.get("investigation_id"),
                visited_hosts=len(host_results),
            )
            return redact_object(primary_result)
        finally:
            executor.close()
