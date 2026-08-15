from __future__ import annotations

import re
import shlex
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings
from app.services.noc_skills import select_noc_skill
from app.services.redaction import redact_text
from app.services.site_scoped_execution import build_approved_execution_route


_SAFE_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
_SYSTEMCTL_INSTRUCTION_RE = re.compile(
    r"\bsystemctl\s+(?P<action>start|stop|restart|reload|enable\s+--now|disable\s+--now)\s+(?P<unit>[A-Za-z0-9_.@:-]+)",
    re.IGNORECASE,
)
_REBOOT_RE = re.compile(
    r"\b(?:reboot|reinici(?:ar|e|a)|restart)\b.{0,18}\b(?:servidor|server|host|maquina|máquina)\b"
    r"|\bsystemctl\s+(?:--no-block\s+)?reboot\b",
    re.IGNORECASE,
)
_STOP_START_RE = re.compile(
    r"(?:"
    r"\b(?:stop|parar|pare)\b(?:\s+(?:o|a|no|na))?(?:\s+servi[cç]o)?\s+(?P<unit1>[A-Za-z0-9_.@:-]+)"
    r".{0,80}?\b(?:start|iniciar|inicie)\b(?:\s+(?:o|a|no|na))?(?:\s+servi[cç]o)?(?:\s+(?P=unit1))?"
    r"|"
    r"\bstop\s*(?:/|\+|e|and|\s)\s*start\b(?:\s+(?:o|a|no|na|em))?(?:\s+servi[cç]o)?\s+(?P<unit2>[A-Za-z0-9_.@:-]+)"
    r")",
    re.IGNORECASE,
)
_RESTART_SERVICE_RE = re.compile(
    r"\b(?:restart|reiniciar|reinicie|reset|resetar)\b"
    r"(?:\s+(?:o|a|no|na|em))?(?:\s+servi[cç]o)?\s+(?P<unit>[A-Za-z0-9_.@:-]+)",
    re.IGNORECASE,
)
_STOP_SERVICE_RE = re.compile(
    r"\b(?:stop|parar|pare)\b(?:\s+(?:o|a|no|na))?(?:\s+servi[cç]o)?\s+(?P<unit>[A-Za-z0-9_.@:-]+)",
    re.IGNORECASE,
)
_START_SERVICE_RE = re.compile(
    r"\b(?:start|iniciar|inicie)\b(?:\s+(?:o|a|no|na))?(?:\s+servi[cç]o)?\s+(?P<unit>[A-Za-z0-9_.@:-]+)",
    re.IGNORECASE,
)

_ALLOWED_SERVICE_ACTIONS = {
    "start",
    "stop",
    "restart",
    "reload",
    "enable --now",
    "disable --now",
    "stop_start",
}
_RESERVED_NON_UNITS = {
    "servidor",
    "server",
    "host",
    "maquina",
    "máquina",
    "servico",
    "serviço",
    "banco",
    "database",
    "db",
}


def _safe_unit(value: Any) -> str:
    unit = str(value or "").strip()
    if not _SAFE_UNIT_RE.fullmatch(unit) or unit.startswith("-"):
        raise ValueError(f"unit systemd inválida para ação prescrita: {unit or '-'}")
    if unit.casefold() in _RESERVED_NON_UNITS:
        raise ValueError(f"nome de unit genérico não é suficiente para ação prescrita: {unit}")
    return unit


def _normalize_action(value: Any) -> str:
    action = (
        str(value or "restart")
        .strip()
        .casefold()
        .replace("stop/start", "stop_start")
        .replace("stop+start", "stop_start")
    )
    action = re.sub(r"\s+", " ", action)
    if action not in _ALLOWED_SERVICE_ACTIONS:
        raise ValueError(f"ação systemd prescrita não suportada: {action}")
    return action


def normalize_prescription(item: dict[str, Any], *, source: str) -> dict[str, Any]:
    raw_type = str(item.get("type") or item.get("kind") or item.get("tool") or "").strip().casefold()
    if raw_type in {"systemd", "service", "systemd.service", "systemd.prescribed_unit"}:
        arguments = dict(item.get("arguments") or {})
        unit = _safe_unit(item.get("unit") or arguments.get("unit"))
        action = _normalize_action(item.get("action") or arguments.get("action"))
        return {
            "type": "systemd",
            "unit": unit,
            "action": action,
            "authorization_source": source,
            "reason": str(item.get("reason") or "ação prescrita explicitamente")[:500],
        }
    if raw_type in {"reboot", "host_reboot", "system.reboot", "system.prescribed_reboot"}:
        return {
            "type": "reboot",
            "authorization_source": source,
            "reason": str(item.get("reason") or "reboot prescrito explicitamente")[:500],
        }
    raise ValueError(f"tipo de ação prescrita não suportado: {raw_type or '-'}")


def _dedupe(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for action in actions:
        key = (
            str(action.get("type") or ""),
            str(action.get("unit") or ""),
            str(action.get("action") or ""),
            str(action.get("authorization_source") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    return result


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < other_end and end > other_start for other_start, other_end in occupied)


def parse_operator_instruction(text: str | None) -> list[dict[str, Any]]:
    """Extrai somente prescrições operacionais inequívocas do texto do operador.

    A ordem do texto é preservada. Texto não reconhecido continua disponível
    para a investigação, mas não ganha autorização elevada. Assim uma frase
    como "stop/start postgresql.service e depois reiniciar o servidor" executa
    primeiro o serviço e só então submete o reboot.
    """

    value = str(text or "").strip()
    if not value:
        return []

    candidates: list[tuple[int, int, dict[str, Any]]] = []
    occupied: list[tuple[int, int]] = []

    for match in _SYSTEMCTL_INSTRUCTION_RE.finditer(value):
        try:
            unit = _safe_unit(match.group("unit"))
            action = _normalize_action(match.group("action"))
        except ValueError:
            continue
        span = match.span()
        occupied.append(span)
        candidates.append(
            (
                span[0],
                span[1],
                normalize_prescription(
                    {
                        "type": "systemd",
                        "unit": unit,
                        "action": action,
                        "reason": "comando systemctl solicitado explicitamente pelo operador",
                    },
                    source="operator_prescription",
                ),
            )
        )

    for match in _STOP_START_RE.finditer(value):
        span = match.span()
        if _overlaps(span, occupied):
            continue
        raw_unit = match.group("unit1") or match.group("unit2")
        try:
            unit = _safe_unit(raw_unit)
        except ValueError:
            continue
        occupied.append(span)
        candidates.append(
            (
                span[0],
                span[1],
                normalize_prescription(
                    {
                        "type": "systemd",
                        "unit": unit,
                        "action": "stop_start",
                        "reason": "stop/start solicitado explicitamente pelo operador",
                    },
                    source="operator_prescription",
                ),
            )
        )

    for pattern, action_name in (
        (_RESTART_SERVICE_RE, "restart"),
        (_STOP_SERVICE_RE, "stop"),
        (_START_SERVICE_RE, "start"),
    ):
        for match in pattern.finditer(value):
            span = match.span()
            if _overlaps(span, occupied):
                continue
            try:
                unit = _safe_unit(match.group("unit"))
            except ValueError:
                continue
            occupied.append(span)
            candidates.append(
                (
                    span[0],
                    span[1],
                    normalize_prescription(
                        {
                            "type": "systemd",
                            "unit": unit,
                            "action": action_name,
                            "reason": f"{action_name} solicitado explicitamente pelo operador",
                        },
                        source="operator_prescription",
                    ),
                )
            )

    for match in _REBOOT_RE.finditer(value):
        span = match.span()
        if _overlaps(span, occupied):
            continue
        occupied.append(span)
        candidates.append(
            (
                span[0],
                span[1],
                normalize_prescription(
                    {"type": "reboot", "reason": "reboot solicitado explicitamente pelo operador"},
                    source="operator_prescription",
                ),
            )
        )

    candidates.sort(key=lambda item: (item[0], item[1]))
    return _dedupe([item[2] for item in candidates])


def skill_prescriptions(incident: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
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
    procedure_id = str(skill.get("procedure_id") or skill.get("id") or "").strip()
    if not procedure_id or procedure_id == "generic-checkmk-alert":
        return None, []

    actions: list[dict[str, Any]] = []
    for raw in skill.get("prescribed_actions") or []:
        if isinstance(raw, dict):
            actions.append(normalize_prescription(dict(raw), source="skill_prescription"))
    return procedure_id, _dedupe(actions)


def prescribed_actions_for_incident(incident: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    procedure_id, actions = skill_prescriptions(incident)
    actions.extend(parse_operator_instruction(str(incident.get("operator_instruction") or "")))
    for raw in incident.get("operator_prescribed_actions") or []:
        if isinstance(raw, dict):
            actions.append(normalize_prescription(raw, source="operator_prescription"))
    return procedure_id, _dedupe(actions)


def _underlying_executor(executor: Any, command: str) -> Any:
    selector = getattr(executor, "_executor", None)
    if callable(selector):
        return selector(command)
    return executor


def _prescribed_sudo(executor: Any, command: str, *, timeout: int) -> Any:
    """Executa comando estruturado e prescrito sem passar pelo veto genérico.

    O bypass é deliberadamente estreito: somente comandos construídos neste
    módulo chegam aqui. Não existe entrada de shell livre do usuário ou da IA.
    """

    target = _underlying_executor(executor, command)

    execute_nested = getattr(target, "_execute_nested", None)
    if callable(execute_nested):
        return execute_nested(
            command,
            timeout=timeout,
            sudo_password=getattr(target, "password", None),
        )

    execute_interactive = getattr(target, "_execute_interactive", None)
    if callable(execute_interactive):
        remote_username = str(getattr(target, "remote_username", "") or "")
        firewall_user = str(getattr(target, "firewall_user", "") or "")
        if remote_username == "root" or (firewall_user and remote_username == firewall_user):
            return execute_interactive(command=command, timeout=timeout)
        password = getattr(target, "password", None)
        if password:
            return execute_interactive(command=command, timeout=timeout, sudo_password=password)
        return execute_interactive(
            command=f"sudo -n sh -lc {shlex.quote(command)}",
            timeout=timeout,
        )

    execute_streaming = getattr(target, "_execute_streaming", None)
    if callable(execute_streaming):
        password = getattr(target, "password", None)
        if password:
            wrapped = f"sudo -S -p '' sh -lc {shlex.quote(command)}"
            sudo_password = password
        else:
            wrapped = f"sudo -n sh -lc {shlex.quote(command)}"
            sudo_password = None
        return execute_streaming(
            command=command,
            wrapped_command=wrapped,
            timeout=timeout,
            sudo_password=sudo_password,
        )

    raise RuntimeError("executor não suporta o canal prescrito de alteração")


def _read(executor: Any, environment: EnvironmentType, command: str, *, timeout: int = 30) -> dict[str, Any]:
    try:
        value = executor.run_sudo(command, environment, timeout=timeout)
        return {
            "command": command,
            "exit_code": int(value.exit_code or 0),
            "stdout": redact_text(str(value.stdout or "")),
            "stderr": redact_text(str(value.stderr or "")),
        }
    except Exception as exc:
        return {
            "command": command,
            "exit_code": 255,
            "stdout": "",
            "stderr": redact_text(f"{type(exc).__name__}: {exc}"),
        }


def _service_command(action: dict[str, Any]) -> tuple[str, list[str]]:
    unit = _safe_unit(action.get("unit"))
    verb = _normalize_action(action.get("action"))
    quoted = shlex.quote(unit)
    if verb == "stop_start":
        command = f"systemctl stop {quoted} && systemctl start {quoted}"
        validations = [f"systemctl is-active {quoted}"]
    elif verb == "stop":
        command = f"systemctl stop {quoted}"
        validations = [f"systemctl show {quoted} --property=ActiveState --value | grep -q '^inactive$'"]
    elif verb == "disable --now":
        command = f"systemctl disable --now {quoted}"
        validations = [
            f"systemctl show {quoted} --property=ActiveState --value | grep -q '^inactive$'",
            f"systemctl is-enabled {quoted} 2>/dev/null | grep -Eq '^(disabled|static|masked)$'",
        ]
    elif verb == "enable --now":
        command = f"systemctl enable --now {quoted}"
        validations = [f"systemctl is-active {quoted}", f"systemctl is-enabled {quoted}"]
    else:
        command = f"systemctl {verb} {quoted}"
        validations = [f"systemctl is-active {quoted}"]
    return command, validations


def execute_prescribed_action(
    executor: Any,
    environment: EnvironmentType,
    action: dict[str, Any],
) -> dict[str, Any]:
    source = str(action.get("authorization_source") or "").strip()
    if source not in {"skill_prescription", "operator_prescription"}:
        return {
            "status": "blocked",
            "reason": "ação não possui procedência prescrita confiável",
            "authorization_source": source or None,
        }

    action_type = str(action.get("type") or "").strip().casefold()
    if action_type == "systemd":
        command, validations = _service_command(action)
        preconditions = [
            _read(
                executor,
                environment,
                f"systemctl show {shlex.quote(_safe_unit(action.get('unit')))} --no-pager -p Id -p LoadState -p ActiveState -p SubState -p UnitFileState",
            )
        ]
        if preconditions[0]["exit_code"] != 0:
            return {
                **action,
                "status": "failed",
                "reason": "não foi possível confirmar a unit prescrita antes da alteração",
                "command": command,
                "preconditions": preconditions,
                "validations": [],
                "policy_path": "prescribed_action_bypass",
            }
        try:
            result = _prescribed_sudo(executor, command, timeout=120)
        except Exception as exc:
            return {
                **action,
                "status": "failed",
                "reason": f"{type(exc).__name__}: {exc}",
                "command": command,
                "preconditions": preconditions,
                "validations": [],
                "policy_path": "prescribed_action_bypass",
            }
        checks = [_read(executor, environment, check, timeout=45) for check in validations]
        valid = int(result.exit_code or 0) == 0 and all(item["exit_code"] == 0 for item in checks)
        return {
            **action,
            "status": "validated" if valid else "failed",
            "command": command,
            "exit_code": int(result.exit_code or 0),
            "stdout": redact_text(str(result.stdout or "")),
            "stderr": redact_text(str(result.stderr or "")),
            "preconditions": preconditions,
            "validations": checks,
            "policy_path": "prescribed_action_bypass",
        }

    if action_type == "reboot":
        command = "systemctl --no-block reboot"
        preconditions = [_read(executor, environment, "uptime; systemctl is-system-running 2>/dev/null || true")]
        try:
            result = _prescribed_sudo(executor, command, timeout=20)
        except Exception as exc:
            return {
                **action,
                "status": "failed",
                "reason": f"{type(exc).__name__}: {exc}",
                "command": command,
                "preconditions": preconditions,
                "validations": [],
                "policy_path": "prescribed_action_bypass",
            }
        accepted = int(result.exit_code or 0) == 0
        return {
            **action,
            "status": "validated" if accepted else "failed",
            "state": "reboot_submitted" if accepted else "reboot_failed",
            "command": command,
            "exit_code": int(result.exit_code or 0),
            "stdout": redact_text(str(result.stdout or "")),
            "stderr": redact_text(str(result.stderr or "")),
            "preconditions": preconditions,
            "validations": [],
            "policy_path": "prescribed_action_bypass",
            "reconnect_required": accepted,
        }

    return {
        **action,
        "status": "blocked",
        "reason": f"tipo prescrito não suportado: {action_type}",
        "policy_path": "prescribed_action_bypass",
    }


def _environment(result: dict[str, Any], incident: dict[str, Any]) -> EnvironmentType:
    raw = (result.get("environment_classification") or {}).get("environment") or incident.get("environment")
    if hasattr(raw, "value"):
        raw = raw.value
    try:
        return EnvironmentType(str(raw or EnvironmentType.UNKNOWN.value).strip().casefold())
    except ValueError:
        return EnvironmentType.UNKNOWN


def run_prescribed_correction(
    incident: dict[str, Any],
    result: dict[str, Any],
    *,
    settings: Settings,
) -> dict[str, Any] | None:
    procedure_id, actions = prescribed_actions_for_incident(incident)
    if not actions:
        return None

    analysis = dict(result.get("analysis") or {})
    scope = dict(analysis.get("site_scope") or {})
    if not scope.get("isolated") or not scope.get("same_site_only") or not scope.get("site_id") or not scope.get("entry_address"):
        return {
            "status": "failed",
            "state": "prescribed_route_scope_missing",
            "reason": "ação prescrita exige rota isolada do mesmo cliente/site",
            "procedure_id": procedure_id,
            "results": [],
        }

    environment = _environment(result, incident)
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
            "state": "prescribed_route_error",
            "reason": f"não foi possível reconstruir a rota prescrita: {type(exc).__name__}: {exc}",
            "procedure_id": procedure_id,
            "results": [],
        }

    if not route.site_scoped:
        try:
            route.executor.close()
        except Exception:
            pass
        return {
            "status": "failed",
            "state": "prescribed_route_not_site_scoped",
            "reason": "ação prescrita recusada porque a rota não ficou isolada no site do cliente",
            "procedure_id": procedure_id,
            "results": [],
        }

    results: list[dict[str, Any]] = []
    executor = route.executor
    try:
        executor.connect()
        for action in actions:
            item = execute_prescribed_action(executor, environment, action)
            results.append(item)
            if str(item.get("status") or "") != "validated":
                return {
                    "status": "failed",
                    "state": "prescribed_action_failed",
                    "reason": str(item.get("reason") or "ação prescrita não foi validada"),
                    "procedure_id": procedure_id,
                    "results": results,
                    "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
                }
            if item.get("reconnect_required") and action is not actions[-1]:
                return {
                    "status": "failed",
                    "state": "prescribed_reboot_not_last",
                    "reason": "reboot prescrito deve ser a última ação do procedure/instrução para permitir a revalidação posterior",
                    "procedure_id": procedure_id,
                    "results": results,
                    "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
                }
        return {
            "status": "validated",
            "state": "prescribed_actions_executed",
            "summary": f"{len(results)} ação(ões) prescrita(s) executada(s) sem bloqueio do Ansible/IA.",
            "procedure_id": procedure_id,
            "results": results,
            "prescription_sources": sorted({str(item.get("authorization_source") or "") for item in results}),
            "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
            "new_approval_required": False,
            "pending_actions": [],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "state": "prescribed_execution_error",
            "reason": f"{type(exc).__name__}: {exc}",
            "procedure_id": procedure_id,
            "results": results,
            "execution_route": {**dict(route.metadata), "site_scoped": True, "context": route.context},
        }
    finally:
        executor.close()
