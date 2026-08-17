from __future__ import annotations

from pathlib import Path

from app.db.checkmk_dedup import _deduplicate_checkmk_new_rows
from app.db.checkmk_master_models import CheckmkProblemORM
from app.services.noc_skills import reload_noc_skills, select_noc_skill


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _FakeSession:
    def __init__(self, rows, existing=None) -> None:
        self.new = list(rows)
        self.existing = existing
        self.locked = False

    def execute(self, *_args, **_kwargs):
        self.locked = True
        return None

    def scalar(self, _statement):
        return self.existing

    def expunge(self, obj) -> None:
        if obj in self.new:
            self.new.remove(obj)


def _problem(key: str, *, output: str = "erro") -> CheckmkProblemORM:
    return CheckmkProblemORM(
        problem_key=key,
        site_id="site-a",
        client_alias="EMPRESA A",
        kind="service",
        host_name="srv01",
        service="Systemd Socket Summary",
        output=output,
        state=2,
        state_name="CRIT",
        active=True,
    )


def test_same_flush_duplicate_problem_is_consolidated_before_insert() -> None:
    first = _problem("site-a|service|srv01|Systemd Socket Summary", output="primeiro")
    second = _problem("site-a|service|srv01|Systemd Socket Summary", output="mais recente")
    session = _FakeSession([first, second])

    _deduplicate_checkmk_new_rows(session, object(), object())

    assert session.locked is True
    assert session.new == [first]
    assert first.output == "mais recente"


def test_cross_process_duplicate_becomes_update_after_advisory_lock() -> None:
    existing = _problem("site-a|service|srv01|Systemd Socket Summary", output="gravado pelo worker")
    existing.occurrence_count = 3
    incoming = _problem("site-a|service|srv01|Systemd Socket Summary", output="fotografia web")
    session = _FakeSession([incoming], existing=existing)

    _deduplicate_checkmk_new_rows(session, object(), object())

    assert session.locked is True
    assert incoming not in session.new
    assert existing.output == "fotografia web"
    assert existing.occurrence_count >= 4


def test_rpcbind_socket_does_not_route_to_checkmk_agent_procedure() -> None:
    reload_noc_skills()
    selected = select_noc_skill(
        {
            "site_id": "dra",
            "host": "dra-dbprimario-oracle",
            "host_address": "10.0.0.10",
            "service": "Systemd Socket Summary",
            "state_name": "CRIT",
            "output": "Total: 20, Disabled: 3, Failed: 1, 1 socket failed (rpcbind) CRIT",
        }
    )

    assert selected["procedure_id"] == "linux-systemd-socket-summary"
    assert selected["procedure_id"] != "checkmk-systemd-socket-summary"


def test_check_mk_socket_still_routes_to_checkmk_agent_procedure() -> None:
    reload_noc_skills()
    selected = select_noc_skill(
        {
            "site_id": "sma",
            "host": "sma-dbstandby",
            "host_address": "10.1.1.223",
            "service": "Systemd Socket Summary",
            "state_name": "CRIT",
            "output": "Total: 13, Disabled: 1, Failed: 1, 1 socket failed (check_mk.socket) CRIT",
        }
    )

    assert selected["procedure_id"] == "checkmk-systemd-socket-summary"


def test_batch_runner_reuses_snapshot_handoff_instead_of_immediate_second_global_scan() -> None:
    runner = (PROJECT_ROOT / "app" / "services" / "noc_selected_runner.py").read_text(encoding="utf-8")
    batch = (PROJECT_ROOT / "app" / "services" / "noc_problem_batch.py").read_text(encoding="utf-8")

    assert "batch_snapshot_completed_at" in batch
    assert "batch_source" in batch
    assert "_active_batch_run" in batch
    assert "batch_handoff" in runner
    assert "_batch_handoff_snapshot" in runner
    assert "_BATCH_HANDOFF_MAX_AGE_SECONDS" in runner
    assert "snapshot = _snapshot_for_selected_run(run, settings=settings)" in runner


def test_database_guard_is_installed_during_schema_initialization() -> None:
    base = (PROJECT_ROOT / "app" / "db" / "base.py").read_text(encoding="utf-8")
    guard = (PROJECT_ROOT / "app" / "db" / "checkmk_dedup.py").read_text(encoding="utf-8")

    assert "install_checkmk_session_guards" in base
    assert "pg_advisory_xact_lock" in guard
    assert "CheckmkProblemORM.problem_key == key" in guard
