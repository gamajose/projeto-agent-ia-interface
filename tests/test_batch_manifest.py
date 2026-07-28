from __future__ import annotations

import pytest

from app.services.batch_manifest import BatchManifestError, parse_batch_manifest


def test_plain_txt_accepts_lines_semicolon_and_per_target_port() -> None:
    result = parse_batch_manifest(
        "alvos.txt",
        "172.27.232.10; 172.27.232.11:2222\nmonitor-cliente\n172.27.232.10\n",
    )

    assert result["format"] == "txt"
    assert result["total"] == 3
    assert result["items"] == [
        {"target": "172.27.232.10", "display_name": "172.27.232.10"},
        {"target": "172.27.232.11", "display_name": "172.27.232.11", "ssh_port": 2222},
        {"target": "monitor-cliente", "display_name": "monitor-cliente"},
    ]
    assert result["warnings"] == ["alvo duplicado ignorado: 172.27.232.10"]


def test_csv_accepts_portuguese_headers_and_per_target_playbook() -> None:
    result = parse_batch_manifest(
        "servidores.csv",
        "ip;hostname;porta;ambiente;objetivo;playbook\n"
        "172.27.229.10;srv-prod-01;22;produção;Validar agente Checkmk;checkmk-agent-port\n"
        "172.27.229.11;srv-std-01;2222;standby;Validar filesystem;linux-filesystem\n",
    )

    assert result["format"] == "csv"
    assert result["total"] == 2
    assert result["items"][0] == {
        "target": "172.27.229.10",
        "display_name": "srv-prod-01",
        "ssh_port": 22,
        "environment": "production",
        "objective": "Validar agente Checkmk",
        "playbook_id": "checkmk-agent-port",
        "playbook_mode": "manual",
    }
    assert result["items"][1]["ssh_port"] == 2222
    assert result["items"][1]["environment"] == "standby"


def test_yaml_applies_defaults_and_allows_per_target_override() -> None:
    result = parse_batch_manifest(
        "lote.yaml",
        """
defaults:
  environment: monitoring
  objective: Validar saúde do agente de monitoramento
  provider: auto
  playbook_id: checkmk-agent-port
targets:
  - target: 172.27.232.20
    hostname: monitor-a
    ssh_port: 22
  - ip: 172.27.232.21
    hostname: monitor-b
    porta: 2222
    objetivo: Validar comunicação na porta 6556
""",
    )

    assert result["format"] == "yaml"
    assert result["defaults"]["environment"] == "monitoring"
    assert result["defaults"]["playbook_mode"] == "manual"
    assert result["items"][0]["provider"] == "auto"
    assert result["items"][0]["playbook_id"] == "checkmk-agent-port"
    assert result["items"][1]["objective"] == "Validar comunicação na porta 6556"
    assert result["items"][1]["ssh_port"] == 2222


def test_json_accepts_targets_string() -> None:
    result = parse_batch_manifest(
        "lote.json",
        '{"defaults":{"environment":"training"},"targets":"192.0.2.10;192.0.2.11"}',
    )

    assert result["total"] == 2
    assert all(item["environment"] == "training" for item in result["items"])


def test_diagnostic_playbook_without_targets_is_rejected() -> None:
    with pytest.raises(BatchManifestError, match="playbook de diagnóstico"):
        parse_batch_manifest(
            "playbook.yaml",
            "id: checkmk-agent\nsteps:\n  - tool: service.search\n",
        )


def test_batch_limit_is_enforced_after_deduplication() -> None:
    with pytest.raises(BatchManifestError, match="excede o limite de 2"):
        parse_batch_manifest("alvos.txt", "192.0.2.1;192.0.2.2;192.0.2.3", max_targets=2)


def test_invalid_port_is_rejected() -> None:
    with pytest.raises(BatchManifestError, match="porta SSH fora"):
        parse_batch_manifest(
            "alvos.csv",
            "target;porta\n192.0.2.10;70000\n",
        )


def test_secret_columns_are_ignored_and_never_returned() -> None:
    result = parse_batch_manifest(
        "alvos.csv",
        "target;password;token;ssh_port\n192.0.2.10;segredo;abc;22\n",
    )

    item = result["items"][0]
    assert item == {
        "target": "192.0.2.10",
        "display_name": "192.0.2.10",
        "ssh_port": 22,
    }
    assert "segredo" not in str(result)
