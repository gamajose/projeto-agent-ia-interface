from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_interface_references_compact_modal_batch_result_tool_and_settings_assets() -> None:
    html = (PROJECT_ROOT / "app" / "ui" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "app" / "ui" / "app.js").read_text(encoding="utf-8")
    api_script = (PROJECT_ROOT / "app" / "ui" / "api-resilience.js").read_text(encoding="utf-8")
    batch_script = (PROJECT_ROOT / "app" / "ui" / "batch.js").read_text(encoding="utf-8")
    workspace_script = (PROJECT_ROOT / "app" / "ui" / "workspace.js").read_text(encoding="utf-8")
    tools_script = (PROJECT_ROOT / "app" / "ui" / "tools.js").read_text(encoding="utf-8")
    settings_script = (PROJECT_ROOT / "app" / "ui" / "settings.js").read_text(encoding="utf-8")
    ux_script = (PROJECT_ROOT / "app" / "ui" / "ux-improvements.js").read_text(encoding="utf-8")
    tracker_script = (PROJECT_ROOT / "app" / "ui" / "execution-tracker.js").read_text(encoding="utf-8")
    polish_script = (PROJECT_ROOT / "app" / "ui" / "product-polish.js").read_text(encoding="utf-8")
    investigation_script = (PROJECT_ROOT / "app" / "ui" / "investigation-flow.js").read_text(encoding="utf-8")
    correction_script = (PROJECT_ROOT / "app" / "ui" / "correction-flow.js").read_text(encoding="utf-8")
    workspace_css = (PROJECT_ROOT / "app" / "ui" / "workspace.css").read_text(encoding="utf-8")
    tools_css = (PROJECT_ROOT / "app" / "ui" / "tools.css").read_text(encoding="utf-8")
    settings_css = (PROJECT_ROOT / "app" / "ui" / "settings.css").read_text(encoding="utf-8")
    ux_css = (PROJECT_ROOT / "app" / "ui" / "ux-improvements.css").read_text(encoding="utf-8")
    polish_css = (PROJECT_ROOT / "app" / "ui" / "product-polish.css").read_text(encoding="utf-8")
    investigation_css = (PROJECT_ROOT / "app" / "ui" / "investigation-flow.css").read_text(encoding="utf-8")
    live_css = (PROJECT_ROOT / "app" / "ui" / "execution-live.css").read_text(encoding="utf-8")

    for asset in (
        "enhancements.css", "batch.css", "workspace.css", "tools.css", "settings.css",
        "ux-improvements.css", "product-polish.css", "investigation-flow.css", "execution-live.css",
        "api-resilience.js", "batch.js", "workspace.js", "tools.js", "settings.js", "ux-improvements.js",
        "execution-tracker.js", "product-polish.js", "investigation-flow.js", "correction-flow.js",
    ):
        assert f"/ui/assets/{asset}" in html
    assert "v=1.21.2" in html
    assert "v=1.21.0" not in html

    assert 'id="analysis-modal"' in html
    assert 'id="attach-batch-file"' in html
    assert 'id="provider"' in html
    assert 'id="playbook-mode"' in html
    assert 'id="batch-file"' in html
    assert 'id="view-opencode"' in html
    assert 'data-view="opencode"' in html
    assert 'id="view-settings"' in html
    assert 'data-view="settings"' in html
    assert 'id="provider-new"' in html
    assert ">Adicionar IA<" in html
    assert 'id="provider-modal"' in html
    assert 'id="provider-api-key"' in html
    assert 'id="provider-order-status"' in html
    assert 'id="topbar-start-investigation"' in html
    assert "hero-panel" not in html
    assert "Investigue o ambiente sem perder o controle operacional" not in html

    assert 'id="add-playbook"' in html
    assert 'id="import-playbook"' in html
    assert 'id="import-playbook-file"' in html
    assert "seleção automática, manual ou nenhuma" not in html
    assert "/ui/api/playbooks/intelligent-import-preview" in ux_script
    assert "importPlaybookFile" in ux_script
    assert "playbook-editor-summary" in ux_script
    assert "playbook-editor-validations" in ux_script
    assert "playbook-import-log" in ux_script
    assert "import_warnings" in ux_script
    assert 'typeof value === "string"' in ux_script
    assert "await response.text()" in api_script
    assert "JSON.parse" in api_script

    assert 'id="refresh-opencode"' not in html
    assert 'id="refresh-health"' not in html
    assert "OPEN SOURCE CODING AGENT" not in tools_script
    assert "Enviar ao OpenCode" not in tools_script
    assert ">Enviar<" in tools_script
    assert "opencode-session-history" in tools_script
    assert "SESSION_STORAGE_KEY" in tools_script
    assert "startOpenCodeAutoRefresh" in tools_script
    assert "Acesso avançado à interface original" in tools_script
    assert "opencode-build-confirmation" in tools_css
    assert "white-space: pre-wrap" in tools_css

    assert "As alterações entram no próximo diagnóstico ou investigação" not in polish_script
    assert "cleanSettingsStatusHint" in polish_script
    assert "compactAutomaticHealth" in polish_script
    assert "/ui/api/executions" in tracker_script
    assert "/cancel" in tracker_script
    assert "agent-ui-active-execution" in tracker_script
    assert "startTrackedAnalysis" in tracker_script
    assert "execution-tray" in tracker_script
    assert "execution-tray-percent" in tracker_script
    assert "BASE_PIPELINE" in tracker_script
    assert "QUEUE_PIPELINE" in tracker_script
    assert "showResultWithoutProgressCollision" in tracker_script
    assert "command_started" in tracker_script
    assert "command_output" in tracker_script
    assert "data-cancel-execution" in tracker_script
    assert "worker_wait" in tracker_script
    assert "completed" in tracker_script
    assert "pending" in tracker_script
    assert "compact-health-grid" in polish_css
    assert "grid-template-columns: repeat(5" in polish_css
    assert ".timeline-item.completed" in live_css
    assert ".timeline-item.active" in live_css
    assert ".timeline-item.failed" in live_css
    assert ".timeline-item.cancelled" in live_css
    assert ".execution-live-panel" in live_css
    assert ".execution-command" in live_css
    assert ".danger-button" in live_css

    assert "/ui/api/inventory/backfill" in investigation_script
    assert "/ui/api/targets/suggestions" in investigation_script
    assert "target-autocomplete" in investigation_script
    assert "chooseSuggestion" in investigation_script
    assert "/prepare-correction" in correction_script
    assert "/normalize-presentation" in correction_script
    assert "Continuar para correção" in correction_script
    assert "looksEnglish" in correction_script
    assert ".target-autocomplete" in investigation_css
    assert ".execution-tray-progress" in investigation_css
    assert ".timeline-item.pending" in investigation_css

    assert "Provedores e chaves de IA" not in html
    assert "ai-settings-header" not in html
    assert 'id="provider-deepseek"' not in html
    assert 'id="refresh-ai-settings"' not in html
    assert 'id="ai-provider-order"' not in html
    assert 'id="provider-save-order"' not in html
    assert 'id="provider-priority"' not in html
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
    assert "openProviderModal" in settings_script
    assert "persistCardOrder" in settings_script
    assert "reorderCardAroundPointer" in settings_script
    assert 'addEventListener("dragstart"' in settings_script
    assert 'addEventListener("pointerdown"' in settings_script
    assert 'addEventListener("pointermove"' in settings_script
    assert "document.elementFromPoint" in settings_script
    assert "/ui/api/settings/ai/order" in settings_script
    assert "applyDeepSeekPreset" not in settings_script
    assert "terminal-card" in workspace_script
    assert "confidence-ring" in workspace_script
    assert "renderApprovedExecution" in workspace_script
    assert ".analysis-modal" in workspace_css
    assert ".terminal-screen" in workspace_css
    assert ".opencode-workspace" in tools_css
    assert ".opencode-chat-panel" in tools_css
    assert ".opencode-composer" in tools_css
    assert ".provider-config-grid" in settings_css
    assert ".provider-modal" in settings_css
    assert ".is-dragging" in settings_css
    assert "grid-template-columns:repeat(auto-fit" in settings_css
    assert "touch-action:none" in settings_css
    assert ".provider-enabled-field" in ux_css
    assert ".provider-toggle-track" in ux_css
    assert ".playbook-modal" in ux_css
    assert ".execution-tray" in ux_css
    assert "overflow-x:auto" not in settings_css
    assert "::-webkit-scrollbar" not in settings_css
    assert "DEEPSEEK_API_KEY" not in html
    assert "OMNIROUTE_API_KEY" not in html


def test_interface_javascript_has_valid_syntax() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js não está instalado no ambiente de testes")

    for path in (
        PROJECT_ROOT / "app" / "ui" / "app.js",
        PROJECT_ROOT / "app" / "ui" / "api-resilience.js",
        PROJECT_ROOT / "app" / "ui" / "batch.js",
        PROJECT_ROOT / "app" / "ui" / "workspace.js",
        PROJECT_ROOT / "app" / "ui" / "tools.js",
        PROJECT_ROOT / "app" / "ui" / "settings.js",
        PROJECT_ROOT / "app" / "ui" / "ux-improvements.js",
        PROJECT_ROOT / "app" / "ui" / "execution-tracker.js",
        PROJECT_ROOT / "app" / "ui" / "product-polish.js",
        PROJECT_ROOT / "app" / "ui" / "investigation-flow.js",
        PROJECT_ROOT / "app" / "ui" / "correction-flow.js",
    ):
        result = subprocess.run(
            [node, "--check", str(path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path.name}: {result.stderr}"
