#!/usr/bin/env bash

set -Eeuo pipefail

log() { printf '[runner-privilege] %s\n' "$*"; }
fail() { printf '[runner-privilege] ERRO: %s\n' "$*" >&2; exit 1; }

[[ "$EUID" -eq 0 ]] || fail "execute uma única vez com sudo/root"

RUNNER_USER="${1:-${SUDO_USER:-}}"
[[ -n "$RUNNER_USER" && "$RUNNER_USER" != "root" ]] || fail "informe o usuário do runner, por exemplo: sudo bash $0 jose"
id "$RUNNER_USER" >/dev/null 2>&1 || fail "usuário inexistente: $RUNNER_USER"

WRAPPER="/usr/local/sbin/agent-ia-legacy-systemctl"
SUDOERS_FILE="/etc/sudoers.d/agent-ia-actions-runner"
SYSTEMCTL="$(command -v systemctl)"
[[ -x "$SYSTEMCTL" ]] || fail "systemctl não encontrado"
command -v visudo >/dev/null 2>&1 || fail "visudo não encontrado"

log "instalando wrapper root-owned com allowlist estrita"
cat >"$WRAPPER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail

SYSTEMCTL=${SYSTEMCTL@Q}
readonly WEB="agent-ia-web.service"
readonly WORKER="agent-ia-worker.service"

die() {
  printf 'agent-ia-legacy-systemctl: operação recusada: %s\\n' "\$*" >&2
  exit 64
}

[[ "\$EUID" -eq 0 ]] || die "wrapper precisa executar como root via sudo"
action="\${1:-}"
shift || true

if [[ "\$action" == "probe" ]]; then
  [[ "\$#" -eq 0 ]] || die "probe não recebe argumentos"
  exit 0
fi

case "\$action" in
  stop|start|enable|disable) ;;
  *) die "ação \$action não autorizada" ;;
esac

[[ "\$#" -ge 1 && "\$#" -le 2 ]] || die "quantidade de units inválida"
for unit in "\$@"; do
  [[ "\$unit" == "\$WEB" || "\$unit" == "\$WORKER" ]] || die "unit \$unit não autorizada"
done

exec "\$SYSTEMCTL" "\$action" "\$@"
EOF
chown root:root "$WRAPPER"
chmod 0755 "$WRAPPER"

log "instalando regra sudoers limitada exclusivamente ao wrapper"
cat >"$SUDOERS_FILE" <<EOF
# Agent IA - permite ao runner migrar somente os dois serviços legados.
Cmnd_Alias AGENT_IA_LEGACY_SYSTEMCTL = $WRAPPER
$RUNNER_USER ALL=(root) NOPASSWD: AGENT_IA_LEGACY_SYSTEMCTL
EOF
chown root:root "$SUDOERS_FILE"
chmod 0440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE" >/dev/null || {
  rm -f "$SUDOERS_FILE"
  fail "sudoers inválido; regra removida"
}

log "validando acesso não interativo do usuário $RUNNER_USER"
if ! sudo -u "$RUNNER_USER" sudo -n "$WRAPPER" probe; then
  fail "a regra foi gravada, mas o teste sudo -n falhou"
fi

log "pronto. O runner pode migrar somente agent-ia-web.service e agent-ia-worker.service sem senha."
