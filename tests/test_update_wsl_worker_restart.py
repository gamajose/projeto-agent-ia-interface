from __future__ import annotations

from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPDATE_SCRIPT = PROJECT_ROOT / "scripts" / "update_wsl.sh"


def test_update_wsl_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(UPDATE_SCRIPT)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_update_wsl_restarts_operational_worker_after_setup() -> None:
    content = UPDATE_SCRIPT.read_text(encoding="utf-8")

    setup_call = 'bash "$PROJECT_DIR/scripts/setup_ai_stack.sh" "${SETUP_ARGS[@]}"'
    restart_call = "restart_operational_worker"

    assert "agent-ia-worker.service" in content
    assert "run_systemctl restart agent-ia-worker.service" in content
    assert "run_systemctl is-active --quiet agent-ia-worker.service" in content
    assert content.index(setup_call) < content.rindex(restart_call)
