from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_interface_references_compact_modal_batch_result_tool_and_settings_assets() -> None:
    html = (PROJECT_ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
    batch_script = (PROJECT_ROOT / "app" / "ui" / "batch.js").read_text(encoding="utf-8")
    workspace_script = (PROJECT_ROOT / "app" / "ui" / "workspace.js").read_text(encoding="utf-8")
    tools_script = (PROJECT_ROOT / "app" / "ui" / "tools.js").read_text(encoding="utf-8")
    settings_script = (PROJECT_ROOT / "app" / "ui" / "settings.js").read_text(encoding="utf-8")
    workspace_css = (PROJECT_ROOT / "app" / "ui" / "workspace.css").read_text(encoding="utf-8")
    tools_css = (PROJECT_ROOT / "app" / "ui" / "tools.css").read_text(encoding="utf-8")
    settings_css = (PROJECT_ROOT / "app" / "ui" / "settings.css").read_text(encoding="utf-8")

    assert "/ui/assets/enhancements.css" in html
    assert "/ui/assets/batch.css" in html
    assert "/ui/assets/workspace.css" in html
    assert "/ui/assets/tools.css" in html
    assert "/ui/assets/settings.css" in html
    assert "/ui/assets/batch.js" in html
    assert "/ui/assets/workspace.js" in html
    assert "/ui/assets/tools.js" in html
    assert "/ui/assets/settings.js" in html
    assert "v=1.13.0" in html
    assert 'id="analysis-modal"' in html
    assert 'id="attach-batch-file"' in html
    assert 'id="provider"' in html
    assert 'id="playbook-mode"' in html
    assert 'id="batch-file"' in html
    assert 'id="view-opencode"' in html
    assert 'data-view="opencode"' in html
    assert "OpenCode integrado ao Agent IA" in html
    assert 'id="view-settings"' in html
    assert 'data-view="settings"' in html
    assert 'id="provider-deepseek"' in html
    assert 'id="provider-api-key"' in html
    assert 'id="view-health"' in html
    assert 'data-view="analysis"' not in html
    assert "/ui/api/ai/providers" in script
    assert "/ui/api/health" in script
    assert "/ui/api/batches/parse" in batch_script
    assert "/ui/api/tools/opencode/runs" in tools_script
    assert "submitOpenCodePrompt" in tools_script
    assert "pollOpenCodeRun" in tools_script
    assert "opencode-prompt" in tools_script
    assert "opencode-agent" in tools_script
    assert "/ui/api/settings/ai" in settings_script
    assert "saveProvider" in settings_script
    assert "applyDeepSeekPreset" in settings_script
    assert "terminal-card" in workspace_script
    assert "confidence-ring" in workspace_script
    assert "renderApprovedExecution" in workspace_script
    assert ".analysis-modal" in workspace_css
    assert ".terminal-screen" in workspace_css
    assert ".opencode-workspace" in tools_css
    assert ".opencode-chat-panel" in tools_css
    assert ".opencode-composer" in tools_css
    assert ".provider-config-grid" in settings_css
    assert ".provider-editor" in settings_css
    assert "DEEPSEEK_API_KEY" not in html
    assert "OMNIROUTE_API_KEY" not in html


def test_interface_javascript_has_valid_syntax() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js não está instalado no ambiente de testes")

    for path in (
        PROJECT_ROOT / "app" / "ui" / "app.js",
        PROJECT_ROOT / "app" / "ui" / "batch.js",
        PROJECT_ROOT / "app" / "ui" / "workspace.js",
        PROJECT_ROOT / "app" / "ui" / "tools.js",
        PROJECT_ROOT / "app" / "ui" / "settings.js",
    ):
        result = subprocess.run(
            [node, "--check", str(path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path.name}: {result.stderr}"
