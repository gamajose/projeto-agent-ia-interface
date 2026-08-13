from __future__ import annotations

from types import SimpleNamespace

from app.services import noc_startup_safety


def test_worker_startup_forces_off_and_preserves_saved_scope(monkeypatch) -> None:
    current = {
        "enabled": True,
        "mode": "selected",
        "sites": ["abc"],
        "hosts": ["srv01"],
        "problem_keys": ["abc|srv01|Memory"],
    }
    captured: dict = {}
    settings = SimpleNamespace()

    monkeypatch.setattr(noc_startup_safety, "get_noc_autonomy_control", lambda **kwargs: current)

    def fake_update(**kwargs):
        captured.update(kwargs)
        return {**current, **kwargs}

    monkeypatch.setattr(noc_startup_safety, "update_noc_autonomy_control", fake_update)

    result = noc_startup_safety.pause_noc_autonomy_on_startup(settings=settings)

    assert captured["enabled"] is False
    assert captured["mode"] == "selected"
    assert captured["sites"] == ["abc"]
    assert captured["hosts"] == ["srv01"]
    assert captured["problem_keys"] == ["abc|srv01|Memory"]
    assert captured["operator"] == "worker-startup-safety"
    assert result["enabled"] is False


def test_worker_calls_startup_pause_before_background_patrol() -> None:
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    worker = (project_root / "app" / "worker.py").read_text(encoding="utf-8")
    pause_at = worker.index("pause_noc_autonomy_on_startup(settings=settings)")
    patrol_at = worker.index("start_checkmk_master_patrol_background(settings=settings)")
    assert pause_at < patrol_at
