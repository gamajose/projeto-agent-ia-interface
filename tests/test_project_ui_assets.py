from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_projects_view_and_assets_are_registered():
    index = (PROJECT_ROOT / "app/ui/index.html").read_text(encoding="utf-8")
    web_main = (PROJECT_ROOT / "app/web_main.py").read_text(encoding="utf-8")

    assert 'data-view="projects"' in index
    assert 'id="view-projects"' in index
    assert '/ui/assets/projects.css' in index
    assert '/ui/assets/projects.js' in index
    assert "projects_router" in web_main
    assert "agent_ui_projects_registered" in web_main


def test_project_form_only_asks_for_information_the_agent_cannot_discover():
    index = (PROJECT_ROOT / "app/ui/index.html").read_text(encoding="utf-8")

    assert 'id="project-target-vpn"' in index
    assert 'id="project-scenario"' in index
    assert 'id="project-role"' in index
    assert 'id="project-monitor-vpn"' in index

    for removed_id in (
        "project-name",
        "project-ticket",
        "project-os-family",
        "project-target-name",
        "project-target-internal",
        "project-monitor-name",
        "project-monitor-internal",
        "project-management-type",
        "project-management-ip",
        "project-monitor1-ip",
        "project-monitor1-user",
        "project-cmk05-ip",
        "project-whatsapp-host",
        "project-firewall-type",
    ):
        assert f'id="{removed_id}"' not in index


def test_project_javascript_has_valid_syntax():
    import subprocess

    result = subprocess.run(
        ["node", "--check", str(PROJECT_ROOT / "app/ui/projects.js")],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
