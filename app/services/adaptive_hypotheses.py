from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.services.symptom_intake import current_reported_symptom, parse_reported_symptom


@dataclass(frozen=True)
class HypothesisTemplate:
    identifier: str
    title: str
    mechanism: str
    domains: tuple[str, ...]
    support_terms: tuple[str, ...]
    contradiction_terms: tuple[str, ...]
    tests: tuple[tuple[str, str], ...]
    base_score: int = 20


def _normalize(value: Any) -> str:
    return (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore")
        .decode()
        .casefold()
    )


def _unique(values: list[Any], *, limit: int = 30) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        key = _normalize(text)
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text[:2000])
        if len(result) >= limit:
            break
    return result


def _templates(profile: str | None, objective: str) -> list[HypothesisTemplate]:
    text = _normalize(f"{profile or ''} {objective}")
    common = [
        HypothesisTemplate(
            "resource_exhaustion",
            "Esgotamento de recurso",
            "Filesystem, inode, memória ou outro recurso impediu a inicialização ou a continuidade do componente.",
            ("filesystem", "memory", "system"),
            (
                "no space left on device", "filesystem 100", "use% 100", "100%", "out of memory",
                "oom-killer", "killed process", "cannot allocate memory", "inode 100", "swap 100",
            ),
            ("available 80", "use% 1", "memory available", "active (running)"),
            (
                ("filesystem.usage", "Medir filesystem e inodes relacionados ao componente."),
                ("memory.summary", "Validar pressão de memória, swap e eventos OOM."),
                ("journal.errors", "Buscar falhas de alocação ou falta de espaço no mesmo período."),
            ),
            22,
        ),
        HypothesisTemplate(
            "permission_or_ownership",
            "Permissão ou propriedade incompatível",
            "O componente não consegue ler, criar ou alterar um recurso necessário por causa de permissão, proprietário ou contexto de segurança.",
            ("service", "filesystem", "security"),
            (
                "permission denied", "operation not permitted", "access denied", "avc denied",
                "read-only file system", "wrong owner", "cannot create", "failed to open",
            ),
            ("permission ok", "writable", "access granted"),
            (
                ("service.logs", "Localizar o caminho e a operação que recebeu permission denied."),
                ("filesystem.path_metadata", "Validar proprietário, grupo e permissões dos diretórios envolvidos."),
                ("security.selinux_status", "Verificar bloqueios de SELinux sem alterar a política."),
            ),
            18,
        ),
        HypothesisTemplate(
            "invalid_configuration",
            "Configuração inválida ou incompatível",
            "Uma configuração inválida, ausente ou incompatível impede o componente de iniciar ou permanecer estável.",
            ("service", "configuration", "system"),
            (
                "invalid configuration", "syntax error", "unknown directive", "failed to parse",
                "configuration error", "bad option", "unit not found", "not found", "no such file",
            ),
            ("configuration valid", "syntax ok", "configtest successful"),
            (
                ("service.logs", "Identificar arquivo, diretiva ou unidade mencionada no erro."),
                ("service.unit", "Comparar a unidade instalada com a unidade esperada para a versão detectada."),
                ("system.recent_changes", "Correlacionar a falha com alterações recentes de configuração ou pacote."),
            ),
            18,
        ),
        HypothesisTemplate(
            "dependency_failure",
            "Dependência indisponível",
            "O componente depende de outro processo, socket, porta, mount ou endpoint que não está disponível.",
            ("service", "network", "monitoring"),
            (
                "connection refused", "dependency failed", "failed to connect", "cannot connect",
                "no route to host", "name or service not known", "socket unavailable", "timed out",
            ),
            ("connection established", "connected", "dependency healthy", "listening"),
            (
                ("service.dependencies", "Mapear dependências e unidades relacionadas."),
                ("network.listeners", "Confirmar listener, endereço e porta da dependência."),
                ("network.reachability", "Testar o caminho até a dependência sem alterar o ambiente."),
            ),
            24,
        ),
        HypothesisTemplate(
            "process_crash_loop",
            "Falha de processo ou ciclo de reinício",
            "O processo inicia e encerra por erro interno, sinal, watchdog ou limite operacional.",
            ("service", "system", "container"),
            (
                "exited with status", "main process exited", "core dumped", "segmentation fault",
                "restart counter", "start request repeated too quickly", "watchdog", "terminated",
            ),
            ("active (running)", "uptime", "ready", "started successfully"),
            (
                ("service.status", "Obter código de saída, contador de reinícios e estado atual."),
                ("service.logs", "Ler o erro imediatamente anterior ao encerramento."),
                ("system.kernel_events", "Verificar OOM, segfault ou encerramento pelo kernel."),
            ),
            24,
        ),
        HypothesisTemplate(
            "connectivity_path_failure",
            "Falha no caminho de comunicação",
            "Rota, interface, firewall intermediário, VPN ou resolução impede o tráfego entre os componentes.",
            ("network", "vpn", "monitoring"),
            (
                "destination host unreachable", "network is unreachable", "no route to host",
                "100% packet loss", "timed out", "timeout", "gateway alarm", "flapping",
            ),
            ("0% packet loss", "reachable", "connection established", "reply from"),
            (
                ("network.route", "Validar a rota efetiva até o destino."),
                ("network.reachability", "Comparar ICMP e conexão na porta esperada."),
                ("network.mtr", "Localizar perda ou oscilação no caminho quando necessário."),
            ),
            20,
        ),
        HypothesisTemplate(
            "recent_change_regression",
            "Regressão após mudança recente",
            "Uma atualização, reinício, alteração de configuração ou recriação antecedeu o início do incidente.",
            ("change", "system", "container"),
            (
                "updated", "upgraded", "changed", "recreated", "new image", "package installed",
                "configuration changed", "rebooted", "since boot", "deployment",
            ),
            (),
            (
                ("system.recent_changes", "Construir a linha do tempo de mudanças antes do alerta."),
                ("container.events", "Verificar recriação, restart ou troca de imagem."),
                ("service.logs", "Correlacionar o primeiro erro com o horário da mudança."),
            ),
            14,
        ),
    ]

    if any(token in text for token in ("checkmk", "check mk", "omd", "automation-helper", "monitoramento")):
        common.extend(
            (
                HypothesisTemplate(
                    "checkmk_internal_component_failure",
                    "Falha em componente interno do site Checkmk",
                    "Um processo interno do site OMD falhou e produziu estado parcial ou healthcheck degradado.",
                    ("monitoring", "service", "container"),
                    (
                        "automation-helper stopped", "automation-helper: stopped", "partially running",
                        "overall state partially", "omd status", "ui-job-scheduler stopped", "mkeventd stopped",
                    ),
                    ("overall state running", "automation-helper running", "all processes running"),
                    (
                        ("checkmk.site.status", "Identificar exatamente qual processo interno está fora do estado esperado."),
                        ("checkmk.site.logs", "Localizar o primeiro erro do processo interno afetado."),
                        ("container.health_history", "Confirmar se o alerta do container é consequência do processo interno."),
                    ),
                    34,
                ),
                HypothesisTemplate(
                    "checkmk_agent_transport_failure",
                    "Falha no transporte do agente Checkmk",
                    "Socket, xinetd, agent receiver ou listener da porta do agente não está disponível ou não entrega dados.",
                    ("monitoring", "network", "service"),
                    (
                        "6556", "xinetd inactive", "check-mk-agent.socket inactive", "connection refused",
                        "agent output", "no listener", "socket failed", "agent receiver stopped",
                    ),
                    ("6556 listening", "agent output ok", "check-mk-agent.socket active"),
                    (
                        ("checkmk.agent.output", "Testar a saída local do agente e identificar onde a cadeia quebra."),
                        ("network.listeners", "Confirmar qual processo está associado à porta do agente."),
                        ("service.status", "Validar xinetd, socket ou agent receiver conforme a versão descoberta."),
                    ),
                    30,
                ),
            )
        )

    if any(token in text for token in ("snmp", "oid", "porta 161", "sysdescr")):
        common.extend(
            (
                HypothesisTemplate(
                    "snmp_identity_or_access_failure",
                    "Credencial, versão ou política SNMP incompatível",
                    "A requisição chega ao equipamento, mas comunidade, usuário, versão ou política de acesso impede a resposta válida.",
                    ("monitoring", "network", "security"),
                    (
                        "authorizationerror", "unknown user", "authentication failure", "wrong digest",
                        "unknown community", "no access", "snmpv3", "usmstats",
                    ),
                    ("sysdescr.0", "response received", "no error"),
                    (
                        ("snmp.probe", "Comparar versão e resposta com uma consulta mínima permitida."),
                        ("network.packet_capture", "Confirmar chegada e saída dos pacotes UDP 161 com filtro restrito."),
                        ("service.logs", "Buscar rejeição de comunidade, usuário ou origem."),
                    ),
                    28,
                ),
                HypothesisTemplate(
                    "snmp_daemon_binding_failure",
                    "Daemon SNMP sem bind ou sem resposta",
                    "O daemon está inativo, vinculado à interface errada ou incapaz de responder na porta UDP 161.",
                    ("monitoring", "service", "network"),
                    ("snmpd inactive", "bsnmpd stopped", "udp 161 absent", "no listener", "bind failed"),
                    ("udp 161 listening", "snmpd active", "bsnmpd running"),
                    (
                        ("service.status", "Validar o daemon SNMP compatível com o sistema detectado."),
                        ("network.listeners", "Confirmar bind, endereço e processo na UDP 161."),
                        ("service.logs", "Buscar erro de bind, configuração ou acesso."),
                    ),
                    28,
                ),
            )
        )
    return common


def _evidence_records(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or item.get("tool") or "evidência")
        output = "\n".join(
            (
                str(item.get("stdout") or "")[-8000:],
                str(item.get("stderr") or "")[-3000:],
                json.dumps(item.get("normalized") or {}, ensure_ascii=False, default=str)[:4000],
            )
        )
        records.append(
            {
                "id": str(item.get("evidence_id") or f"evidence-{index + 1}"),
                "command": command[:500],
                "status": str(item.get("status") or "unknown"),
                "exit_code": item.get("exit_code"),
                "text": _normalize(f"{command}\n{output}"),
                "excerpt": " ".join(output.split())[:700],
            }
        )
    return records


def _hard_signals(records: list[dict[str, Any]]) -> dict[str, list[str]]:
    blob = "\n".join(item["text"] for item in records)
    signals: dict[str, list[str]] = {}

    def add(identifier: str, *patterns: str) -> None:
        matched = [pattern for pattern in patterns if pattern in blob]
        if matched:
            signals[identifier] = matched

    add("resource_exhaustion", "no space left on device", "out of memory", "oom-killer", "inode 100%")
    add("permission_or_ownership", "permission denied", "operation not permitted", "read-only file system")
    add("invalid_configuration", "syntax error", "failed to parse", "unknown directive", "unit not found")
    add("dependency_failure", "dependency failed", "connection refused", "failed to connect", "no route to host")
    add("process_crash_loop", "core dumped", "segmentation fault", "start request repeated too quickly")
    add("checkmk_internal_component_failure", "overall state partially", "partially running")
    add("checkmk_agent_transport_failure", "connection refused", "6556")
    add("snmp_identity_or_access_failure", "authorizationerror", "authentication failure", "unknown community")
    add("snmp_daemon_binding_failure", "bind failed", "udp 161")
    return signals


def _executed_tool_names(evidence: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            continue
        for key in ("tool", "command"):
            value = str(item.get(key) or "").strip().casefold()
            if value:
                values.add(value)
    return values


def _test_was_executed(tool: str, executed: set[str]) -> bool:
    wanted = tool.casefold()
    parts = [part for part in re.split(r"[._-]", wanted) if len(part) >= 3]
    return any(wanted in item or all(part in item for part in parts[:2]) for item in executed)


def _band(score: int, status: str) -> str:
    if status == "confirmed":
        return "confirmed"
    if status == "discarded":
        return "discarded"
    if score >= 70:
        return "strong"
    if score >= 45:
        return "moderate"
    return "weak"


def build_adaptive_hypothesis_state(
    *,
    objective: str,
    profile: str | None,
    evidence: list[dict[str, Any]] | None,
    assessments: list[dict[str, Any]] | None = None,
    previous_state: dict[str, Any] | None = None,
    runtime_context: dict[str, Any] | None = None,
    similar_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Atualiza hipóteses com evidências e escolhe os próximos testes úteis.

    O score é interno e serve para ordenar a investigação. A interface principal
    usa estados operacionais (confirmada, forte, em teste ou descartada), evitando
    criar dúvida artificial quando a causa já está comprovada.
    """
    evidence = [item for item in (evidence or []) if isinstance(item, dict)]
    assessments = [item for item in (assessments or []) if isinstance(item, dict)]
    runtime_context = dict(runtime_context or {})
    similar_history = [item for item in (similar_history or []) if isinstance(item, dict)]
    symptom = current_reported_symptom() or parse_reported_symptom(objective)
    templates = _templates(profile, objective)
    records = _evidence_records(evidence)
    hard = _hard_signals(records)
    executed = _executed_tool_names(evidence)
    blob = "\n".join(item["text"] for item in records)
    objective_text = _normalize(objective)
    previous_map = {
        str(item.get("id")): item
        for item in (previous_state or {}).get("hypotheses") or []
        if isinstance(item, dict) and item.get("id")
    }

    assessment_confirmed = _normalize(
        " ".join(
            str(value)
            for item in assessments
            for value in item.get("hypotheses_confirmed") or []
        )
    )
    assessment_discarded = _normalize(
        " ".join(
            str(value)
            for item in assessments
            for value in item.get("hypotheses_discarded") or []
        )
    )

    hypotheses: list[dict[str, Any]] = []
    for template in templates:
        previous = previous_map.get(template.identifier) or {}
        score = max(template.base_score, int(previous.get("score") or 0))
        support: list[dict[str, str]] = []
        contradictions: list[dict[str, str]] = []

        if any(term in objective_text for term in template.support_terms):
            score += 8
        for record in records:
            matched_support = [term for term in template.support_terms if term in record["text"]]
            matched_contradiction = [term for term in template.contradiction_terms if term in record["text"]]
            if matched_support:
                score += min(22, 7 + len(matched_support) * 3)
                support.append(
                    {
                        "evidence_id": record["id"],
                        "command": record["command"],
                        "excerpt": record["excerpt"],
                    }
                )
            if matched_contradiction:
                score -= min(28, 10 + len(matched_contradiction) * 4)
                contradictions.append(
                    {
                        "evidence_id": record["id"],
                        "command": record["command"],
                        "excerpt": record["excerpt"],
                    }
                )

        if template.identifier in hard:
            score += 32
        title_text = _normalize(f"{template.title} {template.mechanism}")
        if any(token and token in assessment_confirmed for token in title_text.split() if len(token) > 5):
            score += 18
        if any(token and token in assessment_discarded for token in title_text.split() if len(token) > 5):
            score -= 35

        for case in similar_history[:5]:
            cause = _normalize(case.get("probable_cause"))
            if cause and any(term in cause for term in template.support_terms):
                memory_state = str(case.get("validation_state") or "")
                score += 8 if memory_state == "verified" else 4

        score = max(0, min(100, score))
        hard_confirmed = template.identifier in hard and len(support) >= 1 and not contradictions
        independently_supported = len({item["command"] for item in support}) >= 2 and score >= 82 and not contradictions
        status = "confirmed" if hard_confirmed or independently_supported else "open"
        if contradictions and score <= 20:
            status = "discarded"
        elif score >= 70 and status != "confirmed":
            status = "probable"
        elif support:
            status = "testing"

        missing_tests = [
            {"tool": tool, "purpose": purpose}
            for tool, purpose in template.tests
            if not _test_was_executed(tool, executed)
        ]
        hypotheses.append(
            {
                "id": template.identifier,
                "title": template.title,
                "mechanism": template.mechanism,
                "domains": list(template.domains),
                "status": status,
                "band": _band(score, status),
                "score": score,
                "supporting_evidence": support[:8],
                "contradicting_evidence": contradictions[:6],
                "hard_signals": list(hard.get(template.identifier) or []),
                "missing_tests": missing_tests[:4],
            }
        )

    hypotheses.sort(
        key=lambda item: (
            {"confirmed": 4, "probable": 3, "testing": 2, "open": 1, "discarded": 0}.get(item["status"], 0),
            int(item["score"]),
        ),
        reverse=True,
    )
    confirmed = [item for item in hypotheses if item["status"] == "confirmed"]
    active = [item for item in hypotheses if item["status"] != "discarded"]
    leader = confirmed[0] if confirmed else active[0] if active else None

    next_tests: list[dict[str, Any]] = []
    for hypothesis in active[:4]:
        for test in hypothesis.get("missing_tests") or []:
            key = str(test.get("tool") or "").casefold()
            if not key or any(str(item.get("tool") or "").casefold() == key for item in next_tests):
                continue
            next_tests.append(
                {
                    **test,
                    "hypothesis_id": hypothesis["id"],
                    "hypothesis": hypothesis["title"],
                    "priority": "high" if hypothesis is leader else "normal",
                }
            )
            if len(next_tests) >= 6:
                break
        if len(next_tests) >= 6:
            break

    stop = bool(confirmed and confirmed[0].get("supporting_evidence") and not confirmed[0].get("contradicting_evidence"))
    symptom_statement = str(symptom.get("statement") or objective)[:2000]
    causal_chain: list[dict[str, str]] = []
    if leader:
        causal_chain.append({"type": "mechanism", "statement": str(leader["mechanism"])})
    causal_chain.append({"type": "reported_symptom", "statement": symptom_statement})

    known_runtime = sum(
        len(runtime_context.get(name) or [])
        for name in ("binaries", "services", "listeners", "containers")
    )
    novelty = "known"
    if not similar_history and known_runtime < 2:
        novelty = "unknown_environment"
    elif not similar_history:
        novelty = "new_incident_pattern"

    return {
        "version": 1,
        "mode": "adaptive_hypothesis_tree",
        "symptom": symptom,
        "hypotheses": hypotheses,
        "leader": leader,
        "confirmed_cause": confirmed[0] if confirmed else None,
        "next_best_tests": next_tests,
        "stop_decision": {
            "ready": stop,
            "reason": (
                "Uma causa possui evidência direta, não contraditória e suficiente para encerrar a busca de hipóteses concorrentes."
                if stop
                else "A investigação ainda precisa reduzir as lacunas da hipótese líder."
            ),
        },
        "causal_chain": causal_chain,
        "novelty": novelty,
        "discarded_count": len([item for item in hypotheses if item["status"] == "discarded"]),
        "evidence_count": len(records),
    }


def enrich_analysis_with_hypotheses(result: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    adaptive = build_adaptive_hypothesis_state(
        objective=str(result.get("context") or ""),
        profile=result.get("profile"),
        evidence=list(result.get("evidence") or []),
        assessments=list(result.get("round_assessments") or []),
        previous_state=analysis.get("adaptive_hypotheses"),
        runtime_context=dict(result.get("runtime_context") or {}),
        similar_history=list(result.get("similar_history") or []),
    )
    analysis["adaptive_hypotheses"] = adaptive

    confirmed = adaptive.get("confirmed_cause") or {}
    probable = adaptive.get("leader") or {}
    current_root = dict(analysis.get("root_cause") or {})
    current_statement = str(current_root.get("statement") or analysis.get("probable_cause") or "").strip()
    if confirmed and not current_statement:
        analysis["probable_cause"] = confirmed.get("mechanism")
    analysis["dynamic_investigation"] = {
        "state": "cause_confirmed" if confirmed else "hypothesis_testing",
        "leader": confirmed or probable or None,
        "next_best_tests": adaptive.get("next_best_tests") or [],
        "stop_decision": adaptive.get("stop_decision") or {},
        "novelty": adaptive.get("novelty"),
    }
    result["adaptive_hypotheses"] = adaptive
    result["analysis"] = analysis
    return result
