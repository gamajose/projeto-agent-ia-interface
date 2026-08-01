from __future__ import annotations

from copy import deepcopy
from typing import Any


_SCENARIOS: dict[str, dict[str, Any]] = {
    "checkmk-automation-helper": {
        "id": "checkmk-automation-helper",
        "title": "Checkmk parcialmente indisponível",
        "description": "Container ativo, site OMD parcialmente iniciado e automation-helper parado.",
        "category": "Checkmk",
        "customer": "Empresa Demonstração",
        "target": "172.27.250.10",
        "objective": "Investigar Docker Container Health e OMD Status do site demo.",
        "environment": "monitoring",
        "duration_seconds": 14,
        "events": [
            {"delay": 0.3, "stage": "provider_validation", "percent": 12, "detail": "IA de demonstração selecionada sem consumo externo."},
            {"delay": 0.5, "stage": "target_resolution", "percent": 25, "detail": "Cliente e servidor de monitoramento identificados."},
            {"delay": 0.7, "stage": "ssh_connection", "percent": 38, "detail": "Replay da conexão Monitor 1 → DEMO MONITOR."},
            {"delay": 0.8, "stage": "command_started", "percent": 50, "detail": "Executando docker ps e omd status.", "command_id": "demo-cmk-1", "command": "docker ps; omd status", "host": "demo-monitor"},
            {"delay": 0.8, "stage": "command_output", "percent": 60, "detail": "Recebendo estado dos componentes.", "command_id": "demo-cmk-1", "command": "docker ps; omd status", "host": "demo-monitor", "stdout_tail": "checkmk-demo Up 2 days (unhealthy)\nautomation-helper stopped"},
            {"delay": 0.5, "stage": "command_completed", "percent": 72, "detail": "Coleta concluída.", "command_id": "demo-cmk-1", "command": "docker ps; omd status", "host": "demo-monitor", "exit_code": 0, "stdout_tail": "checkmk-demo Up 2 days (unhealthy)\nautomation-helper stopped"},
            {"delay": 0.8, "stage": "evidence_analysis", "percent": 88, "detail": "Container ativo; falha localizada no processo interno."},
            {"delay": 0.4, "stage": "result_persistence", "percent": 96, "detail": "Resultado sanitizado preparado para visualização."},
        ],
        "result": {
            "target": "172.27.250.10",
            "hostname": "demo-monitor",
            "display_target": "EMPRESA DEMONSTRAÇÃO MONITOR",
            "environment": "monitoring",
            "status": "attention",
            "confidence": 94,
            "analysis": {
                "status": "attention",
                "confidence": 94,
                "summary": "O container permanece ativo, mas o site OMD está parcialmente funcional porque o automation-helper está parado.",
                "facts": [
                    "O container checkmk-demo está ativo há dois dias.",
                    "O healthcheck do container está unhealthy.",
                    "O processo automation-helper do site demo está parado.",
                    "Os demais processos essenciais do site permanecem ativos.",
                ],
                "hypotheses": [],
                "discarded_hypotheses": ["O container está completamente parado."],
                "probable_cause": "Interrupção do automation-helper dentro do site OMD demo.",
                "conclusion": "A indisponibilidade é parcial e está localizada no processo interno do site, não no ciclo de vida do container.",
                "recommendations": ["Revisar logs do automation-helper antes de qualquer reinício.", "Após aprovação, recuperar somente o serviço afetado e validar o healthcheck."],
                "next_safe_step": "Consultar os logs recentes do automation-helper e confirmar a causa da interrupção.",
                "target_context": {"client_name": "EMPRESA DEMONSTRAÇÃO MONITOR", "hostname": "demo-monitor", "vpn_ip": "172.27.250.10", "environment": "monitoring"},
                "quality": {"overall": 93, "target_identification": 100, "connectivity": 100, "evidence_coverage": 90, "diagnosis": 95, "final_validation": 80},
            },
            "evidence": [
                {"tool": "container.inspect", "status": "executed", "source_host": "demo-monitor", "stdout": "checkmk-demo Up 2 days (unhealthy)", "exit_code": 0},
                {"tool": "omd.status", "status": "executed", "source_host": "demo-monitor", "stdout": "automation-helper stopped", "exit_code": 0},
            ],
        },
    },
    "vpn-flapping": {
        "id": "vpn-flapping",
        "title": "Flapping de VPN",
        "description": "Linha do tempo de dpinger com alternância entre alarm e clear.",
        "category": "Rede e VPN",
        "customer": "Cliente Cacique Demo",
        "target": "172.27.250.20",
        "objective": "Investigar flapping do gateway CACIQUE nas últimas 60 minutos.",
        "environment": "monitoring",
        "duration_seconds": 12,
        "events": [
            {"delay": 0.3, "stage": "provider_validation", "percent": 12, "detail": "IA de demonstração selecionada."},
            {"delay": 0.5, "stage": "target_resolution", "percent": 26, "detail": "Gateway CACIQUE localizado no firewall de demonstração."},
            {"delay": 0.7, "stage": "ssh_connection", "percent": 40, "detail": "Replay da conexão com o pfSense pela opção 8."},
            {"delay": 0.7, "stage": "command_started", "percent": 52, "detail": "Coletando gateways.log e estado do dpinger.", "command_id": "demo-vpn-1", "command": "vpn.flapping.timeline CACIQUE", "host": "demo-pfsense"},
            {"delay": 0.8, "stage": "command_output", "percent": 65, "detail": "Eventos de alarm e clear identificados.", "command_id": "demo-vpn-1", "command": "vpn.flapping.timeline CACIQUE", "host": "demo-pfsense", "stdout_tail": "alarm latency 510ms loss 22%\nclear latency 72ms loss 0%"},
            {"delay": 0.5, "stage": "command_completed", "percent": 76, "detail": "Linha do tempo consolidada.", "command_id": "demo-vpn-1", "command": "vpn.flapping.timeline CACIQUE", "host": "demo-pfsense", "exit_code": 0, "stdout_tail": "up=4 down=5 loss_events=5"},
            {"delay": 0.8, "stage": "evidence_analysis", "percent": 90, "detail": "Flapping confirmado e correlacionado à perda intermitente."},
        ],
        "result": {
            "target": "172.27.250.20",
            "hostname": "demo-pfsense",
            "display_target": "CACIQUE FIREWALL DEMO",
            "environment": "monitoring",
            "status": "critical",
            "confidence": 91,
            "analysis": {
                "status": "critical",
                "confidence": 91,
                "summary": "O gateway alternou repetidamente entre alarm e clear, com picos de latência e perda de pacotes.",
                "facts": ["Foram encontrados cinco eventos de queda e quatro recuperações em 60 minutos.", "A maior perda registrada foi 22%.", "A interface permaneceu administrativamente ativa."],
                "probable_cause": "Instabilidade intermitente no caminho WAN ou no peer remoto, não queda administrativa da interface.",
                "conclusion": "O flapping está confirmado; ainda é necessário comparar o caminho e o peer para separar operadora de destino remoto.",
                "recommendations": ["Executar MTR em janela de instabilidade.", "Comparar erros da interface e eventos do peer."],
                "next_safe_step": "Coletar MTR e erros de interface durante o próximo evento de perda.",
                "target_context": {"client_name": "CACIQUE FIREWALL DEMO", "hostname": "demo-pfsense", "vpn_ip": "172.27.250.20", "environment": "monitoring"},
                "quality": {"overall": 88, "target_identification": 100, "connectivity": 100, "evidence_coverage": 86, "diagnosis": 88, "final_validation": 65},
            },
            "evidence": [
                {"tool": "vpn.flapping.timeline", "status": "executed", "source_host": "demo-pfsense", "normalized": {"summary": {"up": 4, "down": 5, "loss_events": 5}, "event_count": 9}, "exit_code": 0},
                {"tool": "pfsense.interfaces", "status": "executed", "source_host": "demo-pfsense", "stdout": "status: active", "exit_code": 0},
            ],
        },
    },
    "multi-host-standby-monitor": {
        "id": "multi-host-standby-monitor",
        "title": "Alerta no standby, causa no monitoramento",
        "description": "Demonstra a troca inteligente de host na mesma empresa.",
        "category": "Multi-host",
        "customer": "Empresa José Demo",
        "target": "172.27.250.30",
        "objective": "Investigar sensor do standby e consultar o monitoramento quando necessário.",
        "environment": "standby",
        "duration_seconds": 18,
        "events": [
            {"delay": 0.3, "stage": "provider_validation", "percent": 10, "detail": "IA de demonstração selecionada."},
            {"delay": 0.5, "stage": "target_resolution", "percent": 22, "detail": "Topologia Empresa José Demo carregada."},
            {"delay": 0.7, "stage": "ssh_connection", "percent": 34, "detail": "Monitor 1 → servidor de monitoramento."},
            {"delay": 0.8, "stage": "multi_host_triage", "percent": 46, "detail": "Triagem do standby sem falha local evidente.", "host": "demo-standby"},
            {"delay": 0.8, "stage": "multi_host_handoff", "percent": 58, "detail": "A causa pode estar no site Checkmk; mudando para o monitoramento.", "from_host": "demo-standby", "to_host": "demo-monitor"},
            {"delay": 0.7, "stage": "command_started", "percent": 68, "detail": "Consultando site e serviço no monitoramento.", "command_id": "demo-mh-1", "command": "checkmk.status.service", "host": "demo-monitor"},
            {"delay": 0.8, "stage": "command_completed", "percent": 78, "detail": "Serviço localizado com stale data.", "command_id": "demo-mh-1", "command": "checkmk.status.service", "host": "demo-monitor", "exit_code": 0, "stdout_tail": "standby-demo|Agent Receiver|2|stale data"},
            {"delay": 0.8, "stage": "evidence_analysis", "percent": 90, "detail": "Causa sustentada no monitoramento, não no standby."},
        ],
        "result": {
            "target": "172.27.250.30",
            "hostname": "demo-standby",
            "display_target": "EMPRESA JOSÉ DEMO",
            "environment": "standby",
            "status": "attention",
            "confidence": 92,
            "analysis": {
                "status": "attention",
                "confidence": 92,
                "summary": "O standby está saudável. O alerta é derivado de dados obsoletos no site Checkmk do servidor de monitoramento.",
                "facts": ["O serviço local do standby responde normalmente.", "A porta 6556 está acessível no standby.", "O Checkmk apresenta stale data para o host standby-demo."],
                "discarded_hypotheses": ["Falha do serviço local no standby."],
                "probable_cause": "Coleta desatualizada no site Checkmk hospedado no monitoramento.",
                "conclusion": "O host do alerta e o host da causa são diferentes. Nenhuma ação deve ser aplicada no standby.",
                "recommendations": ["Revisar o processo de coleta no monitoramento.", "Manter o standby somente leitura."],
                "next_safe_step": "Validar no monitoramento o processo responsável pela coleta do standby-demo.",
                "target_context": {"client_name": "EMPRESA JOSÉ DEMO", "hostname": "demo-standby", "vpn_ip": "172.27.250.30", "environment": "standby"},
                "quality": {"overall": 94, "target_identification": 100, "connectivity": 100, "evidence_coverage": 92, "diagnosis": 96, "final_validation": 85},
            },
            "multi_host": {
                "enabled": True,
                "read_only": True,
                "customer": {"name": "Empresa José Demo"},
                "entry_host": {"address": "172.27.250.31", "hostname": "demo-monitor", "label": "Monitoramento José Demo", "role": "monitoring", "environment": "monitoring"},
                "root_host": "172.27.250.31",
                "hosts": [
                    {"address": "172.27.250.30", "hostname": "demo-standby", "label": "Standby José Demo", "role": "standby", "environment": "standby", "status": "healthy", "confidence": 90, "summary": "Serviços locais normais.", "probable_cause": "Sem causa local."},
                    {"address": "172.27.250.31", "hostname": "demo-monitor", "label": "Monitoramento José Demo", "role": "monitoring", "environment": "monitoring", "status": "attention", "confidence": 94, "summary": "Coleta stale no Checkmk.", "probable_cause": "Processo de coleta desatualizado."},
                ],
                "handoffs": [{"from": "172.27.250.30", "to": "172.27.250.31", "role": "monitoring", "reason": "O sensor depende do site Checkmk hospedado no monitoramento.", "status": "completed"}],
                "safety": {"corrections": "blocked_until_single_target_review", "production": "read_only", "standby": "read_only", "customer_databases": "blocked"},
            },
            "evidence": [
                {"tool": "checkmk.agent.output", "status": "executed", "source_host": "demo-standby", "stdout": "<<<check_mk>>>\nVersion: demo", "exit_code": 0},
                {"tool": "checkmk.status.service", "status": "executed", "source_host": "demo-monitor", "stdout": "standby-demo|Agent Receiver|2|stale data", "exit_code": 0},
            ],
        },
    },
    "snmp-timeout": {
        "id": "snmp-timeout",
        "title": "SNMP timeout com validação por camadas",
        "description": "Diferencia rota, porta UDP, chegada de pacotes e resposta do equipamento.",
        "category": "SNMP",
        "customer": "Hardware Demo",
        "target": "172.27.250.40",
        "objective": "Investigar timeout SNMP sem alterar o equipamento.",
        "environment": "monitoring",
        "duration_seconds": 13,
        "events": [
            {"delay": 0.3, "stage": "provider_validation", "percent": 12, "detail": "IA de demonstração selecionada."},
            {"delay": 0.5, "stage": "target_resolution", "percent": 25, "detail": "IP SNMP associado ao host hardware-demo."},
            {"delay": 0.6, "stage": "ssh_connection", "percent": 38, "detail": "Conexão com o monitoramento de demonstração."},
            {"delay": 0.7, "stage": "command_started", "percent": 52, "detail": "Executando cmk -vvn e captura limitada UDP 161.", "command_id": "demo-snmp-1", "command": "checkmk.diagnose_snmp_address; network.packet_capture", "host": "demo-monitor"},
            {"delay": 0.8, "stage": "command_output", "percent": 65, "detail": "Solicitações saem, mas nenhuma resposta retorna.", "command_id": "demo-snmp-1", "command": "checkmk.diagnose_snmp_address; network.packet_capture", "host": "demo-monitor", "stdout_tail": "UDP request 172.27.250.40.161\n0 packets received"},
            {"delay": 0.5, "stage": "command_completed", "percent": 76, "detail": "Coleta concluída sem resposta SNMP.", "command_id": "demo-snmp-1", "command": "checkmk.diagnose_snmp_address; network.packet_capture", "host": "demo-monitor", "exit_code": 0, "stdout_tail": "request sent; no response"},
            {"delay": 0.8, "stage": "evidence_analysis", "percent": 90, "detail": "Rota existe; falha permanece entre ACL/comunidade/serviço SNMP."},
        ],
        "result": {
            "target": "172.27.250.40",
            "hostname": "hardware-demo",
            "display_target": "HARDWARE DEMO",
            "environment": "monitoring",
            "status": "inconclusive",
            "confidence": 78,
            "analysis": {
                "status": "inconclusive",
                "confidence": 78,
                "summary": "O monitoramento envia solicitações UDP 161, mas não recebe resposta do equipamento.",
                "facts": ["A rota até o IP está presente.", "Solicitações SNMP saem do monitoramento.", "Nenhum pacote de resposta foi observado na janela de captura."],
                "hypotheses": ["ACL do equipamento bloqueando a origem.", "Community ou credencial divergente.", "Serviço SNMP desabilitado."],
                "probable_cause": "Bloqueio ou configuração no lado do equipamento; evidência insuficiente para separar ACL, credencial e serviço.",
                "conclusion": "A falha não foi atribuída à rota VPN. É necessária validação do gerenciamento do equipamento.",
                "recommendations": ["Validar origem permitida e community no equipamento.", "Confirmar serviço SNMP ativo sem alterar a produção."],
                "next_safe_step": "Solicitar validação da configuração SNMP no equipamento ou interface de gerenciamento.",
                "target_context": {"client_name": "HARDWARE DEMO", "hostname": "hardware-demo", "vpn_ip": "172.27.250.40", "environment": "monitoring"},
                "quality": {"overall": 84, "target_identification": 100, "connectivity": 85, "evidence_coverage": 85, "diagnosis": 78, "final_validation": 70},
            },
            "evidence": [
                {"tool": "network.inspect_route", "status": "executed", "source_host": "demo-monitor", "stdout": "via 172.27.250.1 dev tun0", "exit_code": 0},
                {"tool": "network.packet_capture", "status": "executed", "source_host": "demo-monitor", "stdout": "request UDP 161; no response", "exit_code": 0},
            ],
        },
    },
}


def list_replay_scenarios() -> list[dict[str, Any]]:
    return [
        {
            key: value.get(key)
            for key in ("id", "title", "description", "category", "customer", "target", "objective", "environment", "duration_seconds")
        }
        for value in _SCENARIOS.values()
    ]


def get_replay_scenario(scenario_id: str) -> dict[str, Any] | None:
    scenario = _SCENARIOS.get(str(scenario_id or "").strip())
    return deepcopy(scenario) if scenario else None
