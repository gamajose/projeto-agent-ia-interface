from __future__ import annotations

from pathlib import Path

from app.services import noc_job_guard
from app.services.noc_autonomy_control import scope_matches_problem


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _problem(*, site: str = "abc", host: str = "srv01", key: str = "abc|srv01|Filesystem /var") -> dict:
    return {"site_id": site, "host": host, "problem_key": key}


def test_runtime_autonomy_is_disabled_by_default_in_source() -> None:
    source = (PROJECT_ROOT / "app" / "services" / "noc_autonomy_control.py").read_text(encoding="utf-8")
    assert '"enabled": False' in source
    assert '"default_off": True' in source


def test_disabled_scope_never_authorizes_problem() -> None:
    scope = {"enabled": False, "mode": "automatic", "sites": [], "hosts": [], "problem_keys": []}
    assert scope_matches_problem(_problem(), scope) is False


def test_automatic_mode_authorizes_every_problem_only_when_enabled() -> None:
    scope = {"enabled": True, "mode": "automatic", "sites": [], "hosts": [], "problem_keys": []}
    assert scope_matches_problem(_problem(site="abc"), scope) is True
    assert scope_matches_problem(_problem(site="xyz", host="db99"), scope) is True


def test_selected_client_without_host_or_sensor_means_all_problems_in_client() -> None:
    scope = {"enabled": True, "mode": "selected", "sites": ["abc"], "hosts": [], "problem_keys": []}
    assert scope_matches_problem(_problem(site="abc", host="srv01", key="p1"), scope) is True
    assert scope_matches_problem(_problem(site="abc", host="srv02", key="p2"), scope) is True
    assert scope_matches_problem(_problem(site="outro", host="srv01", key="p1"), scope) is False


def test_selected_host_without_sensor_means_all_errors_on_that_host() -> None:
    scope = {"enabled": True, "mode": "selected", "sites": ["abc"], "hosts": ["srv01"], "problem_keys": []}
    assert scope_matches_problem(_problem(site="abc", host="srv01", key="memory"), scope) is True
    assert scope_matches_problem(_problem(site="abc", host="srv01", key="filesystem"), scope) is True
    assert scope_matches_problem(_problem(site="abc", host="srv02", key="filesystem"), scope) is False


def test_selected_sensor_restricts_exact_problem_inside_client_and_host() -> None:
    scope = {
        "enabled": True,
        "mode": "selected",
        "sites": ["abc"],
        "hosts": ["srv01"],
        "problem_keys": ["filesystem"],
    }
    assert scope_matches_problem(_problem(site="abc", host="srv01", key="filesystem"), scope) is True
    assert scope_matches_problem(_problem(site="abc", host="srv01", key="memory"), scope) is False


def test_stale_queued_job_is_revalidated_when_current_mode_is_automatic(monkeypatch) -> None:
    metadata = {
        "source": "checkmk_master",
        "site_id": "pdl",
        "checkmk_host": "pdl-monitor-matriz",
        "checkmk_problem_key": "pdl|pdl-monitor-matriz|OMD pdl status",
        "noc_control_revision": "old-revision",
    }
    monkeypatch.setattr(
        noc_job_guard,
        "authorize_noc_job",
        lambda _metadata, *, settings: (False, "escopo autônomo mudou depois que o job entrou na fila"),
    )
    monkeypatch.setattr(
        noc_job_guard,
        "get_noc_autonomy_control",
        lambda *, settings: {
            "enabled": True,
            "mode": "automatic",
            "sites": [],
            "hosts": [],
            "problem_keys": [],
            "revision": "new-revision",
        },
    )

    allowed, reason = noc_job_guard.job_runtime_authorization(metadata, settings=object())

    assert allowed is True
    assert "revalidado" in reason
    assert "automático" in reason


def test_stale_queued_job_remains_blocked_if_current_selected_scope_excludes_it(monkeypatch) -> None:
    metadata = {
        "source": "checkmk_master",
        "site_id": "pdl",
        "checkmk_host": "pdl-monitor-matriz",
        "checkmk_problem_key": "pdl|pdl-monitor-matriz|OMD pdl status",
        "noc_control_revision": "old-revision",
    }
    monkeypatch.setattr(
        noc_job_guard,
        "authorize_noc_job",
        lambda _metadata, *, settings: (False, "escopo autônomo mudou depois que o job entrou na fila"),
    )
    monkeypatch.setattr(
        noc_job_guard,
        "get_noc_autonomy_control",
        lambda *, settings: {
            "enabled": True,
            "mode": "selected",
            "sites": ["outro-site"],
            "hosts": [],
            "problem_keys": [],
            "revision": "new-revision",
        },
    )

    allowed, reason = noc_job_guard.job_runtime_authorization(metadata, settings=object())

    assert allowed is False
    assert "não pertence mais" in reason


def test_stale_queued_job_remains_blocked_when_agents_are_disabled(monkeypatch) -> None:
    metadata = {
        "source": "checkmk_master",
        "site_id": "pdl",
        "checkmk_host": "pdl-monitor-matriz",
        "checkmk_problem_key": "pdl|pdl-monitor-matriz|OMD pdl status",
        "noc_control_revision": "old-revision",
    }
    monkeypatch.setattr(
        noc_job_guard,
        "authorize_noc_job",
        lambda _metadata, *, settings: (False, "escopo autônomo mudou depois que o job entrou na fila"),
    )
    monkeypatch.setattr(
        noc_job_guard,
        "get_noc_autonomy_control",
        lambda *, settings: {
            "enabled": False,
            "mode": "automatic",
            "sites": [],
            "hosts": [],
            "problem_keys": [],
            "revision": "new-revision",
        },
    )

    allowed, reason = noc_job_guard.job_runtime_authorization(metadata, settings=object())

    assert allowed is False
    assert "desligada" in reason


def test_worker_has_second_runtime_guard_before_ssh() -> None:
    guard = (PROJECT_ROOT / "app" / "services" / "noc_job_guard.py").read_text(encoding="utf-8")
    worker = (PROJECT_ROOT / "app" / "worker.py").read_text(encoding="utf-8")
    assert "job_runtime_authorization" in guard
    assert "blocked_by_autonomy" in guard
    assert "Job NOC não abriu SSH" in guard
    assert "install_noc_job_guard()" in worker


def test_checkmk_background_patrol_observes_without_queuing_when_control_is_off() -> None:
    patrol = (PROJECT_ROOT / "app" / "services" / "checkmk_master_patrol.py").read_text(encoding="utf-8")
    web = (PROJECT_ROOT / "app" / "web_fleet.py").read_text(encoding="utf-8")
    assert "get_noc_autonomy_control" in patrol
    assert "problemas_paused" not in patrol  # protege contra nome incorreto fora do schema esperado
    assert '"problems_paused"' in patrol
    assert "passive=True" in web
    assert '@router.post("/ui/api/noc/autonomy")' in web
    assert '@router.post("/ui/api/noc/autonomy/run-selected")' in web


def test_noc_ui_exposes_switch_scope_and_skills() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "noc-agents-control.js").read_text(encoding="utf-8")
    css = (PROJECT_ROOT / "app" / "ui" / "noc-agents-control.css").read_text(encoding="utf-8")
    cache = (PROJECT_ROOT / "app" / "web_ui_cache.py").read_text(encoding="utf-8")
    required = (
        'id="noc-agent-toggle"',
        'data-noc-mode="automatic"',
        'data-noc-mode="selected"',
        'id="noc-scope-sites"',
        'id="noc-scope-hosts"',
        'id="noc-scope-problems"',
        "Sem sensor marcado = todos dos hosts.",
        "Arrumar selecionados",
        "/ui/api/noc/autonomy/run-selected",
        "/ui/api/noc/skills",
        "Sincronizar dados",
        "Atualizar problemas",
    )
    for item in required:
        assert item in source
    assert ".noc-power-switch" in css
    assert ".noc-scope-grid" in css
    assert ".noc-skill-card" in css
    assert "noc-agents-control.js" in cache
    assert "noc-agents-control.css" in cache


def test_inactive_agents_can_persist_selected_mode_and_search_host_by_ip_or_name() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "noc-memory-ui-v146.js").read_text(encoding="utf-8")
    assert "setupAgentSelectedModePersistence" in source
    assert "if (toggle?.checked) return;" in source
    assert "enabled: false" in source
    assert "mode," in source
    assert "Buscar por IP ou nome" in source
    assert "data-noc-host" in source
