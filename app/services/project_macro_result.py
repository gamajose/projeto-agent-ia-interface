from __future__ import annotations

import re
from typing import Any

from app.services.project_validation import (
    INTERFACE_LABELS,
    _detect_hardware,
    _detect_internal_ip,
    _detect_management_ip,
    _detect_management_type,
    _detect_os,
)


def project_blueprint(plan: dict[str, Any], *, reference: str | None = None, include_manual: bool = True) -> list[dict[str, Any]]:
    """Converte o plano em checklist operacional interno.

    O comando permanece apenas no backend para casar a evidência com a etapa. A
    interface recebe uma versão sanitizada sem depender de uma lista de comandos.
    Quando o resultado é do alvo principal, etapas não automatizadas de outros
    contextos (ex.: Monitor 1) continuam visíveis como parte da macro.
    """
    rows: list[dict[str, Any]] = []
    for group in plan.get("groups") or []:
        group_reference = str(group.get("target") or "").strip()
        group_kind = str(group.get("kind") or "remote")
        for item in group.get("items") or []:
            kind = str(item.get("kind") or "manual")
            automated = bool(item.get("automated")) and kind == "command"
            if reference:
                if not group_reference and not include_manual:
                    continue
                if group_reference and group_reference != reference:
                    if not include_manual or automated:
                        continue
                if group_kind == "manual" and not include_manual:
                    continue
            rows.append(
                {
                    "id": str(item.get("id") or ""),
                    "title": str(item.get("title") or "Validação"),
                    "context": str(group.get("label") or item.get("context") or "Ambiente"),
                    "reference": group_reference,
                    "kind": kind,
                    "automated": automated,
                    "command": str(item.get("command") or ""),
                    "purpose": str(item.get("purpose") or ""),
                    "evidence_hint": str(item.get("evidence") or ""),
                    "notes": list(item.get("notes") or []),
                }
            )
    return rows


def public_checklist(blueprint: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "context": item.get("context"),
            "reference": item.get("reference"),
            "kind": item.get("kind"),
            "automated": bool(item.get("automated")),
            "status": "pending" if item.get("automated") else "manual",
            "summary": "Aguardando execução automática." if item.get("automated") else "Validação manual prevista na macro.",
            "evidence_hint": item.get("evidence_hint"),
            "notes": list(item.get("notes") or []),
        }
        for item in blueprint
    ]


def _evidence_by_step(evidence: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_command: dict[tuple[str, str], dict[str, Any]] = {}
    for item in evidence:
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("step_id") or "")
        if step_id:
            by_id[step_id] = item
        key = (str(item.get("reference") or ""), str(item.get("command") or ""))
        if key[1]:
            by_command[key] = item
    return by_id, by_command


def _stdout_for(step_id: str, by_id: dict[str, dict[str, Any]]) -> str:
    return str((by_id.get(step_id) or {}).get("stdout") or "")


def _checkmk_agent_installed(output: str) -> bool:
    """Confirma presença do agente sem confundir instalação com listener ativo.

    A coleta agent-local-validation começa pela consulta de pacote (rpm/dpkg) e
    também tenta executar check_mk_agent. Portanto, uma assinatura do pacote ou
    da saída real do agente é suficiente para impedir uma reinstalação desnecessária.
    """
    return bool(
        re.search(
            r"(?im)(\bcheck[-_]mk[-_]agent(?:[-_.][A-Za-z0-9]+)*\b|\bcheck_mk_agent\b|<<<check_mk>>>)",
            output or "",
        )
    )


def _facts(target: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    vpn_ip = str(target.get("vpn_ip") or "")
    release = _stdout_for("os-version", by_id)
    hardware = _stdout_for("hardware", by_id)
    network = _stdout_for("local-ip", by_id)
    virtualization = _stdout_for("virtualization", by_id)
    bmc = _stdout_for("management-detect", by_id)
    timedate = _stdout_for("time-sync", by_id)
    agent = _stdout_for("agent-local-validation", by_id)

    family, os_name = _detect_os(release, hardware)
    manufacturer, model = _detect_hardware(hardware)
    virt = virtualization.strip().splitlines()[0].strip() if virtualization.strip() else ""
    management_type = _detect_management_type(manufacturer, model, bmc)
    management_ip = _detect_management_ip(bmc)
    return {
        "vpn_ip": vpn_ip,
        "os_family": family,
        "os_name": os_name,
        "internal_ip": _detect_internal_ip(network, vpn_ip) if network else "",
        "virtualization": virt or "unknown",
        "machine_type": "física" if virt == "none" else ("virtual" if virt and virt != "unknown" else "desconhecida"),
        "manufacturer": manufacturer,
        "model": model,
        "management_type": management_type,
        "management_label": INTERFACE_LABELS.get(management_type, management_type),
        "management_ip": management_ip,
        "time_sync": "synchronized" if re.search(r"(?i)(System clock synchronized:\s*yes|synchronized:\s*yes)", timedate) else "not_confirmed",
        "agent_installed": _checkmk_agent_installed(agent),
        "agent_6556": "listening" if re.search(r"6556", agent) else "not_confirmed",
    }


def _friendly_summary(step: dict[str, Any], evidence: dict[str, Any] | None, facts: dict[str, Any]) -> str:
    step_id = str(step.get("id") or "")
    if evidence is None:
        return "A automação não retornou evidência para esta etapa."
    if int(evidence.get("exit_code") or 0) != 0:
        detail = str(evidence.get("stderr") or evidence.get("stdout") or "falha sem saída")
        return detail.strip().splitlines()[-1][:500]
    if step_id == "root-access":
        return "Acesso com elevação para root validado."
    if step_id == "virtualization":
        return "Máquina física." if facts.get("machine_type") == "física" else f"Máquina {facts.get('machine_type') or 'identificada'} ({facts.get('virtualization') or 'sem detalhe'})."
    if step_id == "hardware":
        return " ".join(value for value in (str(facts.get("manufacturer") or ""), str(facts.get("model") or "")) if value).strip() or "Informações de hardware coletadas."
    if step_id == "os-version":
        return str(facts.get("os_name") or "Sistema operacional identificado.")
    if step_id == "local-ip":
        return f"IP interno: {facts.get('internal_ip')}" if facts.get("internal_ip") else "Interfaces de rede coletadas; IP interno não identificado automaticamente."
    if step_id == "time-sync":
        return "Data e hora sincronizadas." if facts.get("time_sync") == "synchronized" else "Estado de sincronização coletado; sincronismo não confirmado."
    if step_id == "management-detect":
        label = str(facts.get("management_label") or "Interface de gerenciamento")
        return f"{label}: {facts.get('management_ip')}" if facts.get("management_ip") else f"{label}; IP não identificado."
    if step_id == "agent-local-validation":
        if facts.get("agent_installed") and facts.get("agent_6556") == "listening":
            return "Agente Checkmk já instalado e listener 6556 identificado no host."
        if facts.get("agent_installed"):
            return "Agente Checkmk já instalado; listener 6556 não confirmado."
        return "Validação local executada; agente Checkmk instalado não foi confirmado."
    if "6556" in str(step.get("title") or "") or "6557" in str(step.get("title") or ""):
        return "Comunicação validada com sucesso."
    if "WhatsApp" in str(step.get("title") or ""):
        return "Comunicação com a API do WhatsApp validada."
    return str(step.get("purpose") or "Validação concluída.")


def build_project_macro_result(
    *,
    blueprint: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    diagnostics: dict[str, Any] | None,
    target: dict[str, Any],
    scenario: str,
    scenario_label: str,
    ticket_macro: str = "",
) -> dict[str, Any]:
    by_id, by_command = _evidence_by_step(evidence)
    facts = _facts(target, by_id)
    checklist: list[dict[str, Any]] = []

    for step in blueprint:
        if step.get("automated"):
            found = by_id.get(str(step.get("id") or "")) or by_command.get(
                (str(step.get("reference") or ""), str(step.get("command") or ""))
            )
            status = "completed" if found is not None and int(found.get("exit_code") or 0) == 0 else "failed"
            checklist.append(
                {
                    "id": step.get("id"),
                    "title": step.get("title"),
                    "context": step.get("context"),
                    "reference": step.get("reference"),
                    "kind": step.get("kind"),
                    "automated": True,
                    "status": status,
                    "summary": _friendly_summary(step, found, facts),
                    "evidence": {
                        "exit_code": found.get("exit_code") if found else None,
                        "stdout": str(found.get("stdout") or "") if found else "",
                        "stderr": str(found.get("stderr") or "") if found else "",
                    },
                    "evidence_hint": step.get("evidence_hint"),
                    "notes": list(step.get("notes") or []),
                }
            )
            continue

        if step.get("id") in {"agent-copy", "agent-install"} and facts.get("agent_installed"):
            validation = by_id.get("agent-local-validation") or {}
            checklist.append(
                {
                    "id": step.get("id"),
                    "title": step.get("title"),
                    "context": step.get("context"),
                    "reference": step.get("reference"),
                    "kind": step.get("kind"),
                    "automated": False,
                    "status": "completed",
                    "summary": "Agente Checkmk já está instalado no servidor. Esta etapa foi ignorada e nenhuma reinstalação foi executada.",
                    "evidence": {
                        "exit_code": validation.get("exit_code"),
                        "stdout": str(validation.get("stdout") or ""),
                        "stderr": str(validation.get("stderr") or ""),
                    },
                    "evidence_hint": "A própria validação local do agente comprovou que o Checkmk já está instalado.",
                    "notes": list(step.get("notes") or []),
                }
            )
            continue

        if step.get("id") == "management-classification" and facts.get("management_type") not in {"unknown", "none", ""}:
            checklist.append(
                {
                    "id": step.get("id"),
                    "title": step.get("title"),
                    "context": step.get("context"),
                    "reference": step.get("reference"),
                    "kind": step.get("kind"),
                    "automated": True,
                    "status": "completed",
                    "summary": _friendly_summary({**step, "id": "management-detect"}, by_id.get("management-detect"), facts),
                    "evidence": {
                        "exit_code": (by_id.get("management-detect") or {}).get("exit_code"),
                        "stdout": _stdout_for("management-detect", by_id),
                        "stderr": str((by_id.get("management-detect") or {}).get("stderr") or ""),
                    },
                    "evidence_hint": step.get("evidence_hint"),
                    "notes": list(step.get("notes") or []),
                }
            )
            continue

        checklist.append(
            {
                "id": step.get("id"),
                "title": step.get("title"),
                "context": step.get("context"),
                "reference": step.get("reference"),
                "kind": step.get("kind"),
                "automated": False,
                "status": "manual",
                "summary": "Etapa manual prevista na macro.",
                "evidence": None,
                "evidence_hint": step.get("evidence_hint"),
                "notes": list(step.get("notes") or []),
            }
        )

    completed = sum(item["status"] == "completed" for item in checklist)
    failed = sum(item["status"] == "failed" for item in checklist)
    manual = sum(item["status"] == "manual" for item in checklist)
    pending = sum(item["status"] == "pending" for item in checklist)
    return {
        "kind": "project_validation",
        "scenario": scenario,
        "scenario_label": scenario_label,
        "target": target,
        "facts": facts,
        "checklist": checklist,
        "summary": {
            "total": len(checklist),
            "completed": completed,
            "failed": failed,
            "manual": manual,
            "pending": pending,
            "automatic": completed + failed + pending,
        },
        "validation_status": "attention" if failed else "completed",
        "ansible": diagnostics or {},
        "evidence": evidence,
        "ticket_macro": ticket_macro,
    }
