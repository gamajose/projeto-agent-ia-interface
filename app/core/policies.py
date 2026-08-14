from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class EnvironmentType(StrEnum):
    PRODUCTION = "production"
    STANDBY = "standby"
    MONITORING = "monitoring"
    TRAINING = "training"
    UNKNOWN = "unknown"


class ActionType(StrEnum):
    READ_ONLY = "read_only"
    SERVICE_ADJUSTMENT = "service_adjustment"
    OMD_ADJUSTMENT = "omd_adjustment"
    CONTAINER_ADJUSTMENT = "container_adjustment"
    DESTRUCTIVE = "destructive"
    HOST_REBOOT = "host_reboot"
    DATABASE_ACCESS = "database_access"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    reason: str
    policy_code: str


# Bloqueio absoluto de lifecycle do SERVIDOR. Reiniciar serviços autorizados é
# outra categoria; reboot/poweroff/halt do host nunca pode ser executado.
#
# Cobrir também wrappers/caminhos comuns é intencional: ``sudo reboot``,
# ``/sbin/reboot`` ou ``systemctl --no-wall reboot`` precisam cair na mesma
# trava imutável antes de qualquer aprovação humana ou IA revisora.
REBOOT_RE = re.compile(
    r"(^|[;&|]\s*)"
    r"(?:(?:sudo|doas|nohup|command|exec)\s+)*"
    r"(?:/(?:usr/)?(?:sbin|bin)/)?"
    r"(?:"
    r"reboot\b|poweroff\b|halt\b|shutdown\b|"
    r"init\s+[06]\b|telinit\s+[06]\b|"
    r"systemctl(?:\s+--[A-Za-z0-9_-]+(?:=\S+)?)*\s+(?:reboot|poweroff|halt|kexec|soft-reboot)\b|"
    r"systemctl(?:\s+--[A-Za-z0-9_-]+(?:=\S+)?)*\s+(?:start|isolate)\s+(?:reboot|poweroff|halt)\.target\b"
    r")",
    re.I,
)
DB_CLIENT_RE = re.compile(r"(^|[;&|]\s*)(sqlplus|rman|psql|mysql|mariadb|sqlcmd|mongosh?|redis-cli)\b", re.I)

PAIRED_SERVICE_STOP_START_RE = re.compile(
    r"^(?:sudo\s+)?systemctl\s+stop\s+([A-Za-z0-9_.@:-]+)\s*&&\s*(?:sudo\s+)?systemctl\s+start\s+\1$",
    re.I,
)
PAIRED_LEGACY_STOP_START_RE = re.compile(
    r"^(?:sudo\s+)?service\s+([A-Za-z0-9_.@:-]+)\s+stop\s*&&\s*(?:sudo\s+)?service\s+\1\s+start$",
    re.I,
)
PAIRED_OMD_STOP_START_RE = re.compile(
    r"^(?:sudo\s+)?docker\s+exec\s+([A-Za-z0-9_.-]+)\s+omd\s+stop\s+([A-Za-z0-9_-]+)\s*&&\s*"
    r"(?:sudo\s+)?docker\s+exec\s+\1\s+omd\s+start\s+\2$",
    re.I,
)
# Exceção extremamente estreita para um problema já validado em campo: o
# xinetd está saudável e dono da 6556, mas uma unit legada check_mk.socket
# permanece FAILED e polui o Systemd Socket Summary. As pré-condições funcionais
# são verificadas pela ferramenta estruturada antes deste comando ser executado.
LEGACY_CHECKMK_SOCKET_CLEANUP_RE = re.compile(
    r"^systemctl\s+disable\s+--now\s+check_mk\.socket\s*&&\s*"
    r"systemctl\s+reset-failed\s+check_mk\.socket\s*&&\s*"
    r"systemctl\s+daemon-reload$",
    re.I,
)

CONTAINER_LIFECYCLE_RE = re.compile(
    r"(^|[;&|]\s*)(?:sudo\s+)?docker\s+(start|stop|restart|kill|rm|rmi|prune)\b",
    re.I,
)
DESTRUCTIVE_RE = re.compile(
    r"(^|[;&|]\s*)(rm\s|rmdir\s|unlink\s|truncate\s|dd\s|mkfs\b|wipefs\b|"
    r"systemctl\s+(stop|disable|mask)\b|service\s+\S+\s+stop\b|"
    r"omd\s+(stop|rm|remove)\b|dnf\s+remove\b|yum\s+remove\b|rpm\s+-e\b)",
    re.I,
)
OMD_ADJUST_RE = re.compile(r"\bomd\s+(start|restart)\b", re.I)
SERVICE_ADJUST_RE = re.compile(r"\b(systemctl\s+(start|restart|reload|enable)|service\s+\S+\s+(start|restart|reload))\b", re.I)


def classify_command(command: str) -> ActionType:
    command = command.strip()
    if REBOOT_RE.search(command):
        return ActionType.HOST_REBOOT
    if DB_CLIENT_RE.search(command):
        return ActionType.DATABASE_ACCESS
    if CONTAINER_LIFECYCLE_RE.search(command):
        return ActionType.CONTAINER_ADJUSTMENT
    if PAIRED_SERVICE_STOP_START_RE.fullmatch(command) or PAIRED_LEGACY_STOP_START_RE.fullmatch(command):
        return ActionType.SERVICE_ADJUSTMENT
    if PAIRED_OMD_STOP_START_RE.fullmatch(command):
        return ActionType.OMD_ADJUSTMENT
    if LEGACY_CHECKMK_SOCKET_CLEANUP_RE.fullmatch(command):
        return ActionType.SERVICE_ADJUSTMENT
    if DESTRUCTIVE_RE.search(command):
        return ActionType.DESTRUCTIVE
    if OMD_ADJUST_RE.search(command):
        return ActionType.OMD_ADJUSTMENT
    if SERVICE_ADJUST_RE.search(command):
        return ActionType.SERVICE_ADJUSTMENT
    return ActionType.READ_ONLY


def environment_allows_correction(environment: EnvironmentType) -> bool:
    """Ambientes classificados podem executar correções seguras após aprovação.

    Isso NÃO significa correção autônoma. Produção e standby continuam exigindo
    revisão da segunda IA, token ligado às ações e clique explícito do analista.
    Ambiente desconhecido permanece somente leitura até ser classificado.
    """
    return environment in {
        EnvironmentType.PRODUCTION,
        EnvironmentType.STANDBY,
        EnvironmentType.MONITORING,
        EnvironmentType.TRAINING,
    }


def evaluate_action(action: ActionType, environment: EnvironmentType) -> PolicyDecision:
    if action == ActionType.DATABASE_ACCESS:
        return PolicyDecision(False, False, "Acesso a banco de dados do cliente é proibido.", "CUSTOMER_DATABASE_ACCESS_DENIED")
    if action == ActionType.HOST_REBOOT:
        return PolicyDecision(False, False, "Bloqueio absoluto: o Agent IA nunca reinicia, desliga ou liga o servidor.", "HOST_REBOOT_DENIED")
    if action == ActionType.CONTAINER_ADJUSTMENT:
        return PolicyDecision(False, False, "Stop, start, restart, kill ou remoção de container são proibidos.", "CONTAINER_LIFECYCLE_DENIED")
    if action == ActionType.DESTRUCTIVE:
        return PolicyDecision(False, True, "Remoção, exclusão, desinstalação ou parada isolada não é executada automaticamente.", "DESTRUCTIVE_ACTION_DENIED")
    if action in {ActionType.SERVICE_ADJUSTMENT, ActionType.OMD_ADJUSTMENT}:
        if environment == EnvironmentType.UNKNOWN:
            return PolicyDecision(False, True, "O ambiente precisa ser classificado antes de qualquer alteração.", "UNKNOWN_ENVIRONMENT_CHANGE_DENIED")
        if environment in {EnvironmentType.PRODUCTION, EnvironmentType.STANDBY}:
            return PolicyDecision(
                True,
                True,
                "Ajuste operacional restrito em produção/standby permitido somente após segunda IA e aprovação explícita do analista, com pós-validação obrigatória.",
                "PROTECTED_ENVIRONMENT_APPROVED_CHANGE",
            )
        return PolicyDecision(True, True, "Ajuste operacional restrito autorizado, com aprovação e validação funcional obrigatórias.", "SAFE_ADJUSTMENT_ALLOWED")
    return PolicyDecision(True, False, "Comando somente leitura permitido.", "READ_ONLY_ALLOWED")
