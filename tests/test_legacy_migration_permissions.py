from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_migration_does_not_require_generic_passwordless_sudo() -> None:
    source = (ROOT / "deploy" / "scripts" / "migrate_legacy_services.sh").read_text(encoding="utf-8")
    assert "sudo -n true" not in source
    assert 'sudo -n "$PRIVILEGED_WRAPPER" "$@"' in source
    assert 'sudo -n systemctl "$@"' in source
    assert "agent-ia-web.service" in source
    assert "agent-ia-worker.service" in source
