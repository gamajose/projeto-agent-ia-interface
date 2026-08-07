from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from app.core.policies import EnvironmentType
from app.core.settings import get_settings
from app.services.access_monitors import settings_for_access_monitor
from app.services.redaction import redact_object
from app.services.runner import build_executor, resolve_target


def _decode_command(value: str) -> str:
    return base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True)
    parser.add_argument("--environment", default="unknown")
    parser.add_argument("--command-b64", required=True)
    parser.add_argument("--purpose", default="validação de projeto")
    parser.add_argument("--monitor-id", default="monitor1")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    base_settings = get_settings()
    settings = settings_for_access_monitor(args.monitor_id, base_settings)
    try:
        environment = EnvironmentType(args.environment)
    except ValueError:
        environment = EnvironmentType.UNKNOWN
    command = _decode_command(args.command_b64)
    output_path = Path(args.output)
    executor = None
    result: dict[str, object]
    try:
        target = resolve_target(args.reference, environment, None, settings=settings)
        executor = build_executor(target, settings=settings)
        executor.connect()
        completed = executor.run(command, target.environment, timeout=settings.ssh_command_timeout)
        result = {
            "orchestrator": "ansible",
            "reference": args.reference,
            "purpose": args.purpose,
            "command": command,
            "exit_code": int(completed.exit_code),
            "stdout": str(completed.stdout or "")[-12000:],
            "stderr": str(completed.stderr or "")[-4000:],
            "status": "success" if int(completed.exit_code) == 0 else "error",
        }
    except Exception as exc:
        result = {
            "orchestrator": "ansible",
            "reference": args.reference,
            "purpose": args.purpose,
            "command": command,
            "exit_code": 255,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "status": "error",
        }
    finally:
        if executor is not None:
            try:
                executor.close()
            except Exception:
                pass
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(redact_object(result), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
