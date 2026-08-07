from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from app.core.settings import PROJECT_ROOT, Settings, get_settings
from app.services.redaction import redact_object


class AnsibleProjectError(RuntimeError):
    pass


def ansible_status(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    binary = shutil.which(settings.agent_ansible_binary)
    if not settings.agent_ansible_enabled:
        return {"enabled": False, "available": False, "binary": None, "detail": "Ansible desabilitado por configuração."}
    if not binary:
        return {
            "enabled": True,
            "available": False,
            "binary": None,
            "detail": f"{settings.agent_ansible_binary} não encontrado no PATH do Agent.",
        }
    return {"enabled": True, "available": True, "binary": binary, "detail": "Ansible disponível para orquestração dos playbooks de projeto."}


def _encoded(command: str) -> str:
    return base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")


def execute_project_steps(
    steps: list[dict[str, Any]],
    *,
    access_monitor_id: str = "monitor1",
    settings: Settings | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = settings or get_settings()
    status = ansible_status(settings)
    if not status["available"]:
        return [], status
    safe_steps = [
        item
        for item in steps
        if str(item.get("command") or "").strip()
        and str(item.get("reference") or "").strip()
        and bool(item.get("automated", True))
    ]
    if not safe_steps:
        return [], {**status, "executed": 0, "detail": "Nenhuma coleta Ansible aplicável ao alvo."}

    with tempfile.TemporaryDirectory(prefix="agent-ansible-project-") as temporary:
        root = Path(temporary)
        tasks: list[dict[str, Any]] = []
        outputs: list[Path] = []
        for index, item in enumerate(safe_steps):
            output = root / f"step-{index:03d}.json"
            outputs.append(output)
            argv = [
                sys.executable,
                "-m",
                "app.services.ansible_remote_exec",
                "--reference",
                str(item["reference"]),
                "--environment",
                str(item.get("environment") or "unknown"),
                "--command-b64",
                _encoded(str(item["command"])),
                "--purpose",
                str(item.get("purpose") or item.get("title") or "validação de projeto"),
                "--monitor-id",
                access_monitor_id,
            ]
            if item.get("sudo"):
                argv.append("--sudo")
            argv.extend(["--output", str(output)])
            tasks.append(
                {
                    "name": str(item.get("title") or item.get("purpose") or f"Validação {index + 1}"),
                    "ansible.builtin.command": {"argv": argv},
                    "changed_when": False,
                    "failed_when": False,
                }
            )

        playbook = [{"name": "Agent IA - validação automatizada de projeto", "hosts": "localhost", "gather_facts": False, "tasks": tasks}]
        playbook_path = root / "project-validation.yml"
        playbook_path.write_text(yaml.safe_dump(playbook, allow_unicode=True, sort_keys=False), encoding="utf-8")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join([str(PROJECT_ROOT), environment.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        completed = subprocess.run(
            [str(status["binary"]), "-i", "localhost,", "-c", "local", str(playbook_path)],
            cwd=str(PROJECT_ROOT),
            env=environment,
            capture_output=True,
            text=True,
            timeout=int(settings.agent_ansible_timeout_seconds),
            check=False,
        )
        evidence: list[dict[str, Any]] = []
        for output in outputs:
            if not output.is_file():
                continue
            try:
                row = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                evidence.append(row)
        diagnostics = {
            **status,
            "executed": len(evidence),
            "requested": len(safe_steps),
            "return_code": int(completed.returncode),
            "stdout_excerpt": (completed.stdout or "")[-2500:],
            "stderr_excerpt": (completed.stderr or "")[-1200:],
            "detail": (
                f"Ansible executou {len(evidence)}/{len(safe_steps)} coleta(s) automaticamente."
                if completed.returncode == 0
                else f"Ansible terminou com código {completed.returncode}; as evidências produzidas foram preservadas."
            ),
        }
        return redact_object(evidence), redact_object(diagnostics)


def evidence_context(evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return ""
    compact = []
    for item in evidence[-20:]:
        compact.append(
            {
                "reference": item.get("reference"),
                "purpose": item.get("purpose"),
                "command": item.get("command"),
                "exit_code": item.get("exit_code"),
                "stdout": str(item.get("stdout") or "")[-2500:],
                "stderr": str(item.get("stderr") or "")[-800:],
            }
        )
    return (
        "\n\nEVIDÊNCIAS JÁ EXECUTADAS AUTOMATICAMENTE PELO ANSIBLE DO AGENT IA. "
        "Use estas saídas como fatos observados e aprofunde somente onde necessário:\n"
        + json.dumps(redact_object(compact), ensure_ascii=False, default=str)
    )
