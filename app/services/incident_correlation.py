from __future__ import annotations

import re
from typing import Any


_ALERT_RULES = (
    ("automation_helper", 100, re.compile(r"automation[- ]?helper|automation helpers?|process(?:o)?\s+[a-z0-9_-]+\s+automation", re.I)),
    ("omd_status", 80, re.compile(r"\bomd\b.{0,80}\bstatus\b|site\s+omd|partially running|parcialmente iniciado", re.I)),
    ("container_health", 60, re.compile(r"docker container health|container\s+unhealthy|healthcheck", re.I)),
    ("checkmk_agent", 55, re.compile(r"porta\s+6556|check[_ -]?mk agent|agente checkmk", re.I)),
    ("snmp", 50, re.compile(r"\bsnmp\b|udp\s*161|authorizationerror", re.I)),
    ("vpn", 45, re.compile(r"\bvpn\b|flapping|tap\d+|t[uú]nel", re.I)),
    ("filesystem", 40, re.compile(r"filesystem|disco|inode|no space left", re.I)),
    ("memory", 35, re.compile(r"mem[oó]ria|swap|oom", re.I)),
)
_ALERT_LABELS = {
    "automation_helper": "Processo automation-helper",
    "omd_status": "Estado do site OMD",
    "container_health": "Saúde do container",
    "checkmk_agent": "Agente Checkmk / porta 6556",
    "snmp": "Comunicação SNMP",
    "vpn": "Conectividade VPN",
    "filesystem": "Filesystem / inodes",
    "memory": "Memória / swap",
    "generic": "Alerta operacional",
}
_CHECKMK_CHAIN = {"automation_helper", "omd_status", "container_health"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _site_token(text: str) -> str | None:
    patterns = (
        r"\bomd\s+([a-z0-9_-]{2,30})\s+status\b",
        r"\bprocess(?:o)?\s+([a-z0-9_-]{2,30})\s+automation",
        r"\bsite\s+([a-z0-9_-]{2,30})\b",
        r"\bSITE=([a-z0-9_-]{2,30})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1).lower()
    return None


def _alert_kinds(text: str) -> list[tuple[str, int]]:
    matches = [(kind, priority) for kind, priority, pattern in _ALERT_RULES if pattern.search(text)]
    return matches or [("generic", 10)]


def _rows(
    text: str,
    *,
    source: str,
    site: str | None,
    investigation_id: Any = None,
    created_at: Any = None,
) -> list[dict[str, Any]]:
    return [
        {
            "source": source,
            "investigation_id": investigation_id,
            "kind": kind,
            "label": _ALERT_LABELS[kind],
            "priority": priority,
            "site": site,
            "objective": text,
            "created_at": created_at,
        }
        for kind, priority in _alert_kinds(text)
    ]


def correlate_alerts(result: dict[str, Any]) -> dict[str, Any]:
    current_text = _text(result.get("context") or result.get("objective"))
    current_site = _site_token(current_text)
    candidates = _rows(current_text, source="current", site=current_site)

    for item in [*(result.get("history") or []), *(result.get("similar_history") or [])]:
        if not isinstance(item, dict):
            continue
        objective = _text(item.get("objective") or item.get("symptom"))
        if not objective:
            continue
        site = _site_token(objective)
        if current_site and site and site != current_site:
            continue
        candidates.extend(
            _rows(
                objective,
                source="history",
                site=site or current_site,
                investigation_id=item.get("id"),
                created_at=item.get("created_at"),
            )
        )

    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for item in candidates:
        effective_site = item.get("site") or current_site
        item["site"] = effective_site
        key = (str(item["kind"]), effective_site)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(item)

    primary = max(deduplicated, key=lambda item: int(item.get("priority") or 0))
    related = [item for item in deduplicated if item is not primary]
    kinds = {str(item.get("kind")) for item in deduplicated}
    grouped = len(kinds.intersection(_CHECKMK_CHAIN)) >= 2
    reason = (
        "Os alertas descrevem camadas dependentes do mesmo site Checkmk; o processo interno é priorizado como possível causa primária."
        if grouped
        else "Não há evidência suficiente para consolidar alertas diferentes em um único incidente."
    )
    incident_kind = str(primary.get("kind") or "generic")
    return {
        "grouped": grouped,
        "incident_key": f"{result.get('target')}:{current_site or incident_kind}",
        "site": current_site,
        "primary_alert": primary,
        "related_alerts": related,
        "detected_kinds": sorted(kinds),
        "reason": reason,
    }
