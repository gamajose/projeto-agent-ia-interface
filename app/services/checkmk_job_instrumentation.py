from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from app.services import jobs
from app.services.site_scoped_runner import run_site_scoped_target


_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("checkmk_site_job_context", default=None)
_INSTALLED = False


def install_checkmk_site_job_routing() -> None:
    """Acopla jobs do CMK05 ao runner site-scoped sem alterar jobs comuns."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_execute = jobs._execute_job
    original_runner = jobs.run_target_tracked

    def execute_with_site_context(job: dict[str, Any], *, settings):
        metadata = dict(job.get("metadata") or {})
        context = metadata if metadata.get("source") == "checkmk_master" and metadata.get("site_scope") else None
        token = _CONTEXT.set(context)
        try:
            return original_execute(job, settings=settings)
        finally:
            _CONTEXT.reset(token)

    def site_aware_runner(
        reference: str,
        objective: str,
        *,
        environment,
        mode="propose",
        approve=False,
        ssh_port=None,
        provider_name=None,
        model_name=None,
        playbook_mode="auto",
        playbook_id=None,
        settings=None,
    ):
        context = _CONTEXT.get()
        if not context:
            return original_runner(
                reference,
                objective,
                environment=environment,
                mode=mode,
                approve=approve,
                ssh_port=ssh_port,
                provider_name=provider_name,
                model_name=model_name,
                playbook_mode=playbook_mode,
                playbook_id=playbook_id,
                settings=settings,
            )
        return run_site_scoped_target(
            reference,
            objective,
            site_id=str(context.get("site_id") or ""),
            client_alias=str(context.get("client_alias") or context.get("site_id") or ""),
            host_name=str(context.get("checkmk_host") or ""),
            internal_target=str(context.get("internal_target") or "").strip() or None,
            target_strategy=str(context.get("target_strategy") or "internal_ssh"),
            environment=environment,
            provider_name=provider_name,
            model_name=model_name,
            playbook_mode=playbook_mode,
            playbook_id=playbook_id,
            settings=settings,
        )

    jobs._execute_job = execute_with_site_context
    jobs.run_target_tracked = site_aware_runner
    _INSTALLED = True
