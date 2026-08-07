from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import get_settings
from app.services.access_monitors import settings_for_access_monitor
from app.services.redaction import redact_object
from app.services.runner import build_executor, resolve_target


def _read_steps(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("arquivo de passos deve conter uma lista JSON")
    return [dict(item) for item in raw if isinstance(item, dict)]


def _result_for_error(item: dict[str, Any], reference: str, exc: Exception) -> dict[str, Any]:
    return {
        "orchestrator": "ansible",
        "reference": reference,
        "purpose": str(item.get("purpose") or item.get("title") or "validação de projeto"),
        "command": str(item.get("command") or ""),
        "sudo": bool(item.get("sudo")),
        "exit_code": 255,
        "stdout": "",
        "stderr": f"{type(exc).__name__}: {exc}",
        "status": "error",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--environment", default="unknown")
    parser.add_argument("--steps-file", required=True)
    parser.add_argument("--monitor-id", default="monitor1")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    steps = _read_steps(Path(args.steps_file))
    output_path = Path(args.output)
    base_settings = get_settings()
    settings = settings_for_access_monitor(args.monitor_id, base_settings)
    try:
        environment = EnvironmentType(args.environment)
    except ValueError:
        environment = EnvironmentType.UNKNOWN

    executor = None
    evidence: list[dict[str, Any]] = []
    connection_error = ""
    try:
        target = resolve_target(args.reference, environment, None, settings=settings)
        executor = build_executor(target, settings=settings)
        executor.connect()
        for item in steps:
            command = str(item.get("command") or "").strip()
            if not command:
                continue
            try:
                completed = (
                    executor.run_sudo(command, target.environment, timeout=settings.ssh_command_timeout)
                    if item.get("sudo")
                    else executor.run(command, target.environment, timeout=settings.ssh_command_timeout)
                )
                evidence.append(
                    {
                        "orchestrator": "ansible",
                        "reference": args.reference,
                        "purpose": str(item.get("purpose") or item.get("title") or "validação de projeto"),
                        "command": command,
                        "sudo": bool(item.get("sudo")),
                        "exit_code": int(completed.exit_code),
                        "stdout": str(completed.stdout or "")[-12000:],
                        "stderr": str(completed.stderr or "")[-4000:],
                        "status": "success" if int(completed.exit_code) == 0 else "error",
                    }
                )
            except Exception as exc:
                evidence.append(_result_for_error(item, args.reference, exc))
    except Exception as exc:
        connection_error = f"{type(exc).__name__}: {exc}"
        evidence.extend(_result_for_error(item, args.reference, exc) for item in steps)
    finally:
        if executor is not None:
            try:
                executor.close()
            except Exception:
                pass

    output = {
        "reference": args.reference,
        "connection_error": connection_error,
        "evidence": evidence,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(redact_object(output), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
