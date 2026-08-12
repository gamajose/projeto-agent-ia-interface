from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel

from app.core.settings import get_settings
from app.db.base import ensure_database_schema
from app.services.checkmk_job_instrumentation import install_checkmk_site_job_routing
from app.services.checkmk_master_patrol import (
    checkmk_master_patrol_status,
    start_checkmk_master_patrol_background,
)
from app.services.codex_provider_instrumentation import install_codex_provider_preflight
from app.services.ensemble_instrumentation import install_ensemble_reasoning
from app.services.fleet_control import fleet_control_status
from app.services.fleet_scope_control import resume_active_fleet_discovery
from app.services.fleet_patrol import fleet_patrol_status, start_fleet_patrol_background
from app.services.operational_tool_instrumentation import install_operational_tools
from app.services.project_playbook_instrumentation import install_project_playbook_instrumentation
from app.services.jobs import get_job, run_worker_once
from app.services.noc_supervisor import supervisor_tick
from app.services.noc_worker_hooks import handle_worker_result, reconcile_noc_jobs
from app.services.secrets import secret_backend_status


install_operational_tools()
install_ensemble_reasoning()
install_project_playbook_instrumentation()
install_codex_provider_preflight()
install_checkmk_site_job_routing()
app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command("run")
def run(
    once: bool = typer.Option(False, "--once", help="Processa no máximo um job e encerra."),
    block_seconds: int | None = typer.Option(None, "--bloqueio", help="Tempo de espera por job."),
) -> None:
    """Executa jobs e mantém o ciclo autônomo de incidentes NOC."""
    settings = get_settings()
    ensure_database_schema()

    # A varredura por faixa virou contingência/manual. Se já existia uma
    # descoberta ativa, ela continua exatamente do cursor persistido.
    fleet_resumed = False if once else resume_active_fleet_discovery(settings=settings)

    # Fonte primária do NOC: CMK05/master -> sites remotos -> estados Checkmk.
    master_started = False if once else start_checkmk_master_patrol_background(settings=settings)

    # Compatibilidade/contingência: a antiga ronda por hosts só sobe quando
    # FLEET_PATROL_ENABLED=true for explicitamente configurado.
    fallback_patrol_started = False if once else start_fleet_patrol_background(settings=settings)

    console.print(Panel(
        f"Worker: {settings.agent_worker_name}\n"
        f"Fila: {settings.agent_queue_name}\n"
        f"Redis: configurado\n"
        f"Segredos: {secret_backend_status(settings).get('backend')}\n"
        f"StrictHostKeyChecking: {settings.ssh_strict_host_key_checking}\n"
        f"NOC autônomo: {'ativo' if settings.noc_incident_enabled else 'desativado'} · L{settings.noc_autonomy_level}\n"
        f"Checkmk Master: {'ativo' if master_started else 'desativado'}\n"
        f"Fleet Discovery: {'retomada em segundo plano' if fleet_resumed else 'contingência/manual'}\n"
        f"Fleet Patrol legado: {'ativo' if fallback_patrol_started else 'desativado'}",
        title="Agent IA Worker",
    ))

    reconcile_noc_jobs(settings=settings)

    if once:
        result = run_worker_once(settings=settings, block_seconds=block_seconds)
        handle_worker_result(result, settings=settings)
        reconcile_noc_jobs(settings=settings)
        supervisor_tick(settings=settings)
        console.print(json.dumps({
            "job": result or {"status": "empty"},
            "checkmk_master": checkmk_master_patrol_status(settings=settings),
            "fleet": fleet_control_status(settings=settings),
            "fallback_patrol": fleet_patrol_status(),
        }, ensure_ascii=False, indent=2, default=str))
        return

    while True:
        try:
            result = run_worker_once(settings=settings, block_seconds=block_seconds)
            handle_worker_result(result, settings=settings)
            reconcile_noc_jobs(settings=settings)
            supervisor_tick(settings=settings)
        except KeyboardInterrupt:
            return
        except Exception as exc:
            console.print(f"[yellow]Supervisor/worker encontrou erro transitório: {type(exc).__name__}: {exc}[/yellow]")


@app.command("fleet-status")
def fleet_status() -> None:
    """Mostra o CMK05 primário e a descoberta de rede de contingência."""
    settings = get_settings()
    ensure_database_schema()
    console.print(json.dumps({
        "checkmk_master": checkmk_master_patrol_status(settings=settings),
        "discovery": fleet_control_status(settings=settings),
        "fallback_patrol": fleet_patrol_status(),
    }, ensure_ascii=False, indent=2, default=str))


@app.command("job")
def job(job_id: str = typer.Argument(..., help="UUID do job.")) -> None:
    """Consulta o estado de um job distribuído."""
    result = get_job(job_id)
    if not result:
        console.print(Panel("Job não encontrado ou expirado.", title="Job", border_style="yellow"))
        raise typer.Exit(2)
    console.print(Panel(json.dumps(result, ensure_ascii=False, indent=2, default=str), title="Job distribuído"))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
