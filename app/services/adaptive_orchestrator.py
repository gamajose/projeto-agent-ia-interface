from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any, Iterable

from app.core.policies import EnvironmentType
from app.services.adaptive_tools import describe_adaptive_tools, execute_adaptive_tool
from app.services.operational_tools import describe_operational_tools
from app.services.redaction import redact_object
from app.services.ssh import SSHExecutor
from app.services.tool_registry import describe_tools


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:-]{1,}")


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return text.casefold()


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(_normalize(value))
        if len(token) >= 3 and token not in {"para", "com", "sem", "uma", "que", "the", "and"}
    }


def parse_runtime_snapshot(output: str) -> dict[str, Any]:
    context: dict[str, Any] = {
        "snapshot_version": "1",
        "os_id": "unknown",
        "os_name": "unknown",
        "kernel": "unknown",
        "init": "unknown",
        "binaries": [],
        "services": [],
        "listeners": [],
        "container_runtimes": [],
        "containers": [],
        "filesystems": [],
    }
    binaries: set[str] = set()
    runtimes: set[str] = set()

    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if key == "SNAPSHOT_VERSION":
            context["snapshot_version"] = value or "1"
        elif key == "OS_ID":
            context["os_id"] = value or "unknown"
        elif key == "OS_NAME":
            context["os_name"] = value or "unknown"
        elif key == "KERNEL":
            context["kernel"] = value or "unknown"
        elif key == "INIT":
            context["init"] = value or "unknown"
        elif key == "BIN" and value:
            binaries.add(value)
        elif key == "SERVICE" and value:
            parts = value.split("|", 2)
            context["services"].append(
                {
                    "name": parts[0],
                    "load": parts[1] if len(parts) > 1 else "",
                    "state": parts[2] if len(parts) > 2 else "",
                }
            )
        elif key == "LISTENER" and value:
            context["listeners"].append(value)
        elif key == "CONTAINER_RUNTIME" and value:
            runtimes.add(value)
        elif key == "CONTAINER" and value:
            parts = value.split("|", 3)
            context["containers"].append(
                {
                    "runtime": parts[0],
                    "name": parts[1] if len(parts) > 1 else "",
                    "image": parts[2] if len(parts) > 2 else "",
                    "status": parts[3] if len(parts) > 3 else "",
                }
            )
        elif key == "FILESYSTEM" and value:
            context["filesystems"].append(value)

    context["binaries"] = sorted(binaries)
    context["container_runtimes"] = sorted(runtimes)
    context["capability_terms"] = sorted(
        _tokens(
            " ".join(
                [
                    context["os_id"],
                    context["os_name"],
                    context["kernel"],
                    context["init"],
                    *context["binaries"],
                    *(item.get("name", "") for item in context["services"]),
                    *(item.get("image", "") for item in context["containers"]),
                ]
            )
        )
    )
    context["summary"] = {
        "binaries": len(context["binaries"]),
        "services": len(context["services"]),
        "listeners": len(context["listeners"]),
        "containers": len(context["containers"]),
        "filesystems": len(context["filesystems"]),
    }
    return context


def discover_runtime_context(
    executor: SSHExecutor,
    environment: EnvironmentType,
) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = execute_adaptive_tool(executor, environment, "runtime.snapshot", {})
    context = parse_runtime_snapshot(str(evidence.get("stdout") or ""))
    context["discovery_status"] = evidence.get("status")
    if evidence.get("status") != "executed":
        context["discovery_error"] = evidence.get("reason") or evidence.get("stderr") or "falha não detalhada"
    return redact_object(context), redact_object(evidence)


def runtime_availability(runtime_context: dict[str, Any]) -> dict[str, bool]:
    return {str(binary): True for binary in runtime_context.get("binaries") or []}


def combined_tool_catalog(runtime_context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    runtime_context = runtime_context or {}
    binaries = set(runtime_context.get("binaries") or [])
    rows: list[dict[str, Any]] = []
    for item in [*describe_tools(), *describe_adaptive_tools(), *describe_operational_tools()]:
        row = dict(item)
        requirements = tuple(row.get("requires_any") or ())
        if row.get("transport") == "http":
            row["available"] = True
        else:
            row["available"] = not requirements or any(binary in binaries for binary in requirements)
        if requirements and not row["available"]:
            row["unavailable_reason"] = f"requer uma destas ferramentas: {', '.join(requirements)}"
        rows.append(row)
    return rows


def _extract_tool_names(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        tool = value.get("tool")
        if isinstance(tool, str) and tool:
            names.append(tool)
        for nested in value.values():
            names.extend(_extract_tool_names(nested))
    elif isinstance(value, list):
        for nested in value:
            names.extend(_extract_tool_names(nested))
    return names


def tool_feedback(evidence: Iterable[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, list[str]] = {
        "successful": [],
        "failed": [],
        "unavailable": [],
        "blocked": [],
    }
    reasons: list[dict[str, str]] = []
    for item in evidence:
        tool = str(item.get("tool") or item.get("command") or "").strip()
        if not tool:
            continue
        status = str(item.get("status") or "").casefold()
        if status in {"executed", "validated"} and int(item.get("exit_code") or 0) == 0:
            by_status["successful"].append(tool)
        elif status == "unavailable" or int(item.get("exit_code") or 0) == 127:
            by_status["unavailable"].append(tool)
        elif status == "blocked":
            by_status["blocked"].append(tool)
        elif status == "failed" or int(item.get("exit_code") or 0) != 0:
            by_status["failed"].append(tool)
        reason = str(item.get("reason") or item.get("stderr") or "").strip()
        if reason and status not in {"executed", "validated"}:
            reasons.append({"tool": tool, "reason": reason[:300]})

    return {
        key: list(dict.fromkeys(values))
        for key, values in by_status.items()
    } | {"reasons": reasons[-20:]}


def _diversify_recommendations(
    rows: list[dict[str, Any]],
    *,
    objective_tokens: set[str],
    limit: int,
) -> list[dict[str, Any]]:
    """Evita que muitas ferramentas de um único domínio ocultem alternativas úteis."""
    limit = max(1, int(limit))
    selected = list(rows[:limit])
    selected_names = {str(item.get("tool") or "") for item in selected}

    priority_groups: list[tuple[set[str], tuple[str, ...]]] = []
    if objective_tokens & {"servico", "service", "unidade", "unit", "systemd"}:
        priority_groups.append(({"service"}, ("service.search", "systemd.inspect_unit", "systemd.list_failed")))
    if objective_tokens & {"erro", "erros", "error", "errors", "log", "logs", "journal"}:
        priority_groups.append(({"logs"}, ("logs.search", "journal.read_unit")))
    if objective_tokens & {"porta", "port", "socket", "listener", "listeners"}:
        priority_groups.append(({"network"}, ("network.listeners", "network.test_port")))

    for categories, preferred_names in priority_groups:
        if any(str(item.get("category") or "") in categories for item in selected):
            continue
        candidate = next(
            (
                item
                for preferred in preferred_names
                for item in rows
                if item.get("tool") == preferred and item.get("tool") not in selected_names
            ),
            None,
        )
        if candidate is None:
            candidate = next(
                (
                    item
                    for item in rows
                    if str(item.get("category") or "") in categories
                    and item.get("tool") not in selected_names
                ),
                None,
            )
        if candidate is None:
            continue
        if len(selected) >= limit:
            replace_index = next(
                (
                    index
                    for index in range(len(selected) - 1, -1, -1)
                    if str(selected[index].get("category") or "") not in categories
                    and sum(
                        1
                        for existing in selected
                        if existing.get("category") == selected[index].get("category")
                    ) > 1
                ),
                len(selected) - 1,
            )
            selected_names.discard(str(selected[replace_index].get("tool") or ""))
            selected[replace_index] = candidate
        else:
            selected.append(candidate)
        selected_names.add(str(candidate.get("tool") or ""))

    order = {str(item.get("tool") or ""): index for index, item in enumerate(rows)}
    selected.sort(key=lambda item: order.get(str(item.get("tool") or ""), len(rows)))
    return selected[:limit]


def recommend_tools(
    *,
    objective: str,
    runtime_context: dict[str, Any],
    catalog: list[dict[str, Any]],
    history: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    executed: set[str] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    history = history or []
    evidence = evidence or []
    executed = executed or set()
    objective_tokens = _tokens(objective)
    runtime_tokens = set(runtime_context.get("capability_terms") or [])
    successful_history = Counter(_extract_tool_names(history))
    feedback = tool_feedback(evidence)
    failed = set(feedback["failed"] + feedback["unavailable"] + feedback["blocked"])

    ranked: list[tuple[float, dict[str, Any]]] = []
    for item in catalog:
        name = str(item.get("name") or "")
        if not name or item.get("correction") or name in executed or not item.get("available", True):
            continue
        corpus = " ".join(
            [
                name,
                str(item.get("category") or ""),
                str(item.get("description") or ""),
                " ".join((item.get("arguments") or {}).keys()),
                " ".join((item.get("arguments") or {}).values()),
            ]
        )
        descriptor_tokens = _tokens(corpus)
        score = float(len(objective_tokens & descriptor_tokens) * 5)
        score += float(len(runtime_tokens & descriptor_tokens) * 1.5)
        score += min(8.0, float(successful_history.get(name, 0) * 2))
        if item.get("adaptive"):
            score += 0.5
        if item.get("operational"):
            score += 1.0
        if name in failed:
            score -= 12
        if name == "runtime.snapshot":
            score -= 20
        ranked.append(
            (
                score,
                {
                    "tool": name,
                    "category": item.get("category"),
                    "description": item.get("description"),
                    "arguments": item.get("arguments") or {},
                    "score": round(score, 2),
                    "reason": _recommendation_reason(
                        objective_tokens,
                        runtime_tokens,
                        descriptor_tokens,
                        successful_history.get(name, 0),
                    ),
                },
            )
        )

    ranked.sort(key=lambda pair: (-pair[0], pair[1]["tool"]))
    positive = [row for score, row in ranked if score > 0]
    candidates = positive if positive else [row for _, row in ranked[: max(1, min(limit, 12))]]
    return _diversify_recommendations(
        candidates,
        objective_tokens=objective_tokens,
        limit=limit,
    )


def _recommendation_reason(
    objective_tokens: set[str],
    runtime_tokens: set[str],
    descriptor_tokens: set[str],
    history_hits: int,
) -> str:
    objective_matches = sorted(objective_tokens & descriptor_tokens)
    runtime_matches = sorted(runtime_tokens & descriptor_tokens)
    parts: list[str] = []
    if objective_matches:
        parts.append(f"relacionada ao objetivo: {', '.join(objective_matches[:6])}")
    if runtime_matches:
        parts.append(f"compatível com o alvo: {', '.join(runtime_matches[:6])}")
    if history_hits:
        parts.append(f"usada com sucesso/recorrência em {history_hits} registro(s) semelhante(s)")
    return "; ".join(parts) or "alternativa genérica disponível no alvo"


def enrich_tool_result(
    result: dict[str, Any],
    *,
    catalog: list[dict[str, Any]],
    executed_tools: set[str] | None = None,
    limit: int = 4,
) -> dict[str, Any]:
    status = str(result.get("status") or "")
    if status not in {"failed", "unavailable", "blocked"} and int(result.get("exit_code") or 0) == 0:
        return result
    category = str(result.get("category") or "")
    current = str(result.get("tool") or "")
    executed_tools = executed_tools or set()
    alternatives = [
        {
            "tool": item.get("name"),
            "description": item.get("description"),
            "arguments": item.get("arguments") or {},
        }
        for item in catalog
        if item.get("available", True)
        and not item.get("correction")
        and str(item.get("category") or "") == category
        and str(item.get("name") or "") not in {current, *executed_tools}
    ][:limit]
    return {**result, "alternative_tools": alternatives}
