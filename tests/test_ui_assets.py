from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_interface_references_provider_health_assets() -> None:
    html = (PROJECT_ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")

    assert "/ui/assets/enhancements.css" in html
    assert 'id="provider"' in html
    assert 'id="playbook-mode"' in html
    assert 'id="view-health"' in html
    assert "/ui/api/ai/providers" in script
    assert "/ui/api/health" in script
    assert "API_KEY" not in html


def test_interface_javascript_has_valid_syntax() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js não está instalado no ambiente de testes")

    result = subprocess.run(
        [node, "--check", str(PROJECT_ROOT / "app" / "ui" / "app.js")],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
