from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from app.web_ui_cache import _inject_topology_assets


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_topology_assets_are_injected_into_versioned_interface() -> None:
    html = "<html><head></head><body></body></html>"

    rendered = _inject_topology_assets(html)

    assert "/ui/assets/topology.css?v=" in rendered
    assert "/ui/assets/topology.js?v=" in rendered
    assert rendered.index("topology.css") < rendered.index("</head>")
    assert rendered.index("topology.js") < rendered.index("</body>")


def test_topology_interface_exposes_customer_hosts_and_safe_route() -> None:
    script = (PROJECT_ROOT / "app" / "ui" / "topology.js").read_text(encoding="utf-8")
    stylesheet = (PROJECT_ROOT / "app" / "ui" / "topology.css").read_text(encoding="utf-8")

    required_script = (
        "Investigar vários hosts da mesma empresa",
        "Empresa / cliente",
        "IP ou hostname interno",
        "SSH pelo servidor de entrada",
        "auto_expand_scope",
        "related_targets",
        "customer_name",
        "/ui/api/topology/resolve",
        "INVESTIGAÇÃO MULTI-HOST",
        "Trocas de host decididas pela IA",
        "Nenhuma sessão de banco de dados foi aberta",
    )
    for item in required_script:
        assert item in script

    assert "MAX_RELATED_HOSTS = 3" in script
    assert "credential_ref: \"SSH_DEFAULT_PASSWORD\"" in script
    assert "route_type: \"ssh\"" in script
    assert ".multi-host-target-grid" in stylesheet
    assert ".multi-host-result-grid" in stylesheet


def test_topology_javascript_has_valid_syntax() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js não está instalado no ambiente de testes")
    path = PROJECT_ROOT / "app" / "ui" / "topology.js"

    result = subprocess.run(
        [node, "--check", str(path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_multi_host_runner_keeps_scope_read_only_and_same_session() -> None:
    source = (PROJECT_ROOT / "app" / "services" / "multi_host_runner.py").read_text(encoding="utf-8")
    nested = (PROJECT_ROOT / "app" / "services" / "nested_ssh.py").read_text(encoding="utf-8")

    assert "isinstance(executor, VPNMenuSSHExecutor)" in source
    assert "NestedSSHExecutor" in source
    assert 'mode="investigate"' in source
    assert '"corrections": "blocked_until_single_target_review"' in source
    assert '"customer_databases": "blocked"' in source
    assert '"max_internal_hops": 1' in source
    assert '"max_related_hosts": 3' in source
    assert "sshpass -p" not in nested
    assert "subprocess.run" not in nested
    assert "self.parent._channel()" in nested
    assert "PreferredAuthentications=password,keyboard-interactive" in nested
