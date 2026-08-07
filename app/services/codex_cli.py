from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.settings import PROJECT_ROOT, Settings, get_settings


class CodexCLIError(RuntimeError):
    """Erro esperado ao localizar ou iniciar o Codex CLI."""


@dataclass(frozen=True)
class CodexCLIStatus:
    available: bool
    command: str | None
    version: str
    workdir: str


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _directory_candidates(directory: Path) -> tuple[Path, ...]:
    return (
        directory / "codex",
        directory / "bin" / "codex",
        directory / "node_modules" / ".bin" / "codex",
        directory / "target" / "release" / "codex",
        directory / "codex-rs" / "target" / "release" / "codex",
    )


def resolve_codex_command(configured_path: str | None = None) -> str | None:
    """Localiza o executável sem shell e aceita a pasta informada pelo operador."""
    candidates: list[Path] = []
    if configured_path:
        configured = Path(configured_path).expanduser()
        if configured.is_dir():
            candidates.extend(_directory_candidates(configured))
        elif _is_executable(configured):
            return str(configured.resolve())
        elif os.sep not in configured_path:
            found = shutil.which(configured_path)
            if found:
                return found

    # Compatibilidade com as instalações já usadas no WSL do projeto.
    for directory in (Path("~/ia/codex").expanduser(), Path("~/.local").expanduser()):
        if directory.is_dir():
            candidates.extend(_directory_candidates(directory))
    candidates.append(Path("~/.local/bin/codex").expanduser())

    for candidate in candidates:
        if _is_executable(candidate):
            return str(candidate.resolve())
    return shutil.which("codex")


def _resolve_workdir(configured_workdir: str | None) -> Path:
    return Path(configured_workdir).expanduser() if configured_workdir else PROJECT_ROOT


def _environment(settings: Settings) -> dict[str, str]:
    environment = os.environ.copy()
    if settings.codex_home:
        environment["CODEX_HOME"] = str(Path(settings.codex_home).expanduser())
    return environment


def codex_cli_status(settings: Settings | None = None) -> CodexCLIStatus:
    settings = settings or get_settings()
    command = resolve_codex_command(settings.codex_cli_path)
    workdir = _resolve_workdir(settings.codex_workdir)
    version = "não identificado"

    if command:
        try:
            completed = subprocess.run(
                [command, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env=_environment(settings),
            )
            output = (completed.stdout or completed.stderr or "").strip()
            if output:
                version = output.splitlines()[0]
        except (OSError, subprocess.SubprocessError):
            version = "instalado, versão indisponível"

    return CodexCLIStatus(
        available=bool(command),
        command=command,
        version=version,
        workdir=str(workdir),
    )


def codex_generate_json(
    prompt: str,
    *,
    settings: Settings | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Executa `codex exec` usando a sessão já autenticada do CLI.

    O Codex roda em sandbox somente leitura. A saída final é obrigada por schema
    a ser um objeto JSON, permitindo usá-lo como mais um provedor cognitivo sem
    conceder ao Codex acesso direto às credenciais SSH do Agent.
    """
    settings = settings or get_settings()
    status = codex_cli_status(settings)
    if not status.command:
        raise CodexCLIError("Codex CLI não encontrado; configure CODEX_CLI_PATH ou instale/login no Codex CLI.")
    workdir = Path(status.workdir)
    if not workdir.is_dir():
        raise CodexCLIError(f"Diretório de trabalho do Codex não existe: {workdir}")

    schema = {
        "type": "object",
        "additionalProperties": True,
    }
    with tempfile.TemporaryDirectory(prefix="agent-codex-") as temp_dir:
        temp = Path(temp_dir)
        schema_path = temp / "schema.json"
        output_path = temp / "answer.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        command = [
            status.command,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        selected_model = str(model or settings.codex_model or "").strip()
        if selected_model:
            command.extend(["--model", selected_model])
        command.append("-")
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                cwd=str(workdir),
                env=_environment(settings),
                capture_output=True,
                text=True,
                timeout=int(settings.codex_exec_timeout_seconds),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexCLIError(f"Codex excedeu {settings.codex_exec_timeout_seconds}s") from exc
        except OSError as exc:
            raise CodexCLIError(f"Não foi possível executar o Codex CLI: {exc}") from exc

        if completed.returncode != 0:
            detail = " ".join((completed.stderr or completed.stdout or "").split())[-800:]
            raise CodexCLIError(f"codex exec retornou código {completed.returncode}: {detail or 'sem detalhe'}")
        text = output_path.read_text(encoding="utf-8").strip() if output_path.is_file() else (completed.stdout or "").strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CodexCLIError("Codex respondeu, mas a saída final não é JSON válido") from exc
        if not isinstance(payload, dict):
            raise CodexCLIError("Codex respondeu JSON, mas não retornou um objeto")
        return payload, {
            "response_chars": len(text),
            "cli_version": status.version,
            "model": selected_model or "sessão padrão",
            "backend": "codex-cli",
        }


def launch_codex(settings: Settings | None = None) -> int:
    """Abre o Codex CLI interativo herdando o terminal atual."""
    settings = settings or get_settings()
    status = codex_cli_status(settings)
    if not status.command:
        raise CodexCLIError(
            "Codex CLI não encontrado. Configure CODEX_CLI_PATH com o executável "
            "ou com a pasta onde ele foi instalado."
        )
    workdir = Path(status.workdir)
    if not workdir.is_dir():
        raise CodexCLIError(f"Diretório do Codex não existe: {workdir}")
    try:
        completed = subprocess.run([status.command], cwd=str(workdir), env=_environment(settings), check=False)
    except OSError as exc:
        raise CodexCLIError(f"Não foi possível iniciar o Codex CLI: {exc}") from exc
    return int(completed.returncode)
