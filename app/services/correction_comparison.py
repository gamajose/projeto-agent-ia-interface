from __future__ import annotations

from typing import Any, Iterable


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _summaries(rows: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result.append(
            {
                "command": row.get("command"),
                "exit_code": row.get("exit_code"),
                "stdout": _clean(row.get("stdout"))[-1600:],
                "stderr": _clean(row.get("stderr"))[-800:],
            }
        )
    return result


def _fingerprint(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{item.get('exit_code')}|{_clean(item.get('stdout'))}|{_clean(item.get('stderr'))}"
        for item in rows
    )


def build_before_after_comparison(results: list[dict[str, Any]]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    validated = 0
    changed = 0
    for index, item in enumerate(results, start=1):
        before = _summaries(item.get("preconditions") or [])
        after = _summaries(item.get("validations") or [])
        action_validated = item.get("status") == "validated"
        state_changed = bool(before or after) and _fingerprint(before) != _fingerprint(after)
        if action_validated:
            validated += 1
        if state_changed:
            changed += 1
        actions.append(
            {
                "index": index,
                "tool": item.get("tool"),
                "description": item.get("description") or item.get("purpose") or item.get("command"),
                "status": item.get("status"),
                "before": before,
                "execution": {
                    "exit_code": item.get("exit_code"),
                    "stdout": _clean(item.get("stdout"))[-1600:],
                    "stderr": _clean(item.get("stderr"))[-800:],
                },
                "after": after,
                "changed": state_changed,
                "validated": action_validated,
                "rollback": item.get("rollback"),
            }
        )

    total = len(actions)
    overall = "validated" if total and validated == total else "partial" if validated else "failed"
    return {
        "status": overall,
        "total_actions": total,
        "validated_actions": validated,
        "changed_actions": changed,
        "actions": actions,
        "summary": (
            f"{validated} de {total} ação(ões) passaram pela validação posterior; "
            f"{changed} apresentaram diferença observável entre antes e depois."
        ),
    }
