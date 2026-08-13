from __future__ import annotations

from app.core.settings import Settings, get_settings
from app.services.noc_autonomy_control import get_noc_autonomy_control, update_noc_autonomy_control


def pause_noc_autonomy_on_startup(*, settings: Settings | None = None) -> dict:
    """Inicia cada processo worker em observação e preserva o escopo salvo."""
    settings = settings or get_settings()
    current = get_noc_autonomy_control(settings=settings)
    return update_noc_autonomy_control(
        enabled=False,
        mode=str(current.get("mode") or "automatic"),
        sites=list(current.get("sites") or []),
        hosts=list(current.get("hosts") or []),
        problem_keys=list(current.get("problem_keys") or []),
        operator="worker-startup-safety",
        settings=settings,
    )
