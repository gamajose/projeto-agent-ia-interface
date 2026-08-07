from __future__ import annotations

import ipaddress
import json
import os
import shlex
import time
from typing import Any

from app.core.policies import EnvironmentType
from app.core.settings import Settings, get_settings
from app.services.cancellation import raise_if_cancelled
from app.services.intelligent_agent import resilient_model_call
from app.services.multi_host_triage import triage_host
from app.services.progress import report_progress, use_progress
from app.services.redaction import redact_object
from app.services.runner import build_executor, resolve_target
from app.services.secrets import get_secret
from app.services.ssh import SSHExecutor
from app.services.tracked_runner import run_target_tracked


_STATUS_WEIGHT = {"critical": 400, "attention": 300, "inconclusive": 200, "healthy": 100}
_VALID_STATUS = set(_STATUS_WEIGHT)


class RangeInvestigationError(RuntimeError):
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on", "sim", "s"}


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _scan_ports() -> list[int]:
    raw = os.getenv("AGENT_RANGE_SCAN_PORTS", "22,2224")
    ports: list[int] = []
    for item in raw.split(","):
        try:
            port = int(item.strip())
        except ValueError:
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
    return ports or [22]


def looks_like_ip_range(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if "/" in text:
        try:
            return isinstance(ipaddress.ip_network(text, strict=False), ipaddress.IPv4Network)
        except ValueError:
            return False
    if "-" not in text:
        return False
    try:
        _expand_range(text, max_addresses=2_000_000, private_only=False)
        return True
    except Exception:
        return False


def _expand_range(value: str, *, max_addresses: int, private_only: bool) -> list[str]:
    text = str(value or "").strip()
    addresses: list[ipaddress.IPv4Address]

    if "/" in text:
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise RangeInvestigationError(f"faixa CIDR inválida: {text}") from exc
        if not isinstance(network, ipaddress.IPv4Network):
            raise RangeInvestigationError("a varredura de faixa aceita IPv4 neste fluxo")
        addresses = list(network.hosts())
    elif "-" in text:
        left, right = [item.strip() for item in text.split("-", 1)]
        try:
            start = ipaddress.ip_address(left)
            if "." not in right:
                prefix = left.rsplit(".", 1)[0]
                right = f"{prefix}.{right}"
            end = ipaddress.ip_address(right)
        except ValueError as exc:
            raise RangeInvestigationError(f"intervalo IPv4 inválido: {text}") from exc
        if not isinstance(start, ipaddress.IPv4Address) or not isinstance(end, ipaddress.IPv4Address):
            raise RangeInvestigationError("a varredura de faixa aceita IPv4 neste fluxo")
        if int(end) < int(start):
            raise RangeInvestigationError("o IP final da faixa é menor que o IP inicial")
        count = int(end) - int(start) + 1
        if count > max_addresses:
            raise RangeInvestigationError(
                f"a faixa contém {count} endereços; o limite configurado é {max_addresses}"
            )
        addresses = [ipaddress.IPv4Address(number) for number in range(int(start), int(end) + 1)]
    else:
        raise RangeInvestigationError("informe uma faixa CIDR ou IP-INICIAL-IP-FINAL")

    if len(addresses) > max_addresses:
        raise RangeInvestigationError(
            f"a faixa contém {len(addresses)} endereços; o limite configurado é {max_addresses}"
        )
    if private_only and any(not address.is_private for address in addresses):
        raise RangeInvestigationError(
            "a varredura automática está restrita a faixas privadas; ajuste AGENT_RANGE_PRIVATE_ONLY somente em ambiente autorizado"
        )
    return [str(address) for address in addresses]


def _monitor_executor(settings: Settings) -> SSHExecutor:
    host = str(settings.ssh_bastion_host or "").strip()
    user = str(settings.ssh_bastion_user or "").strip()
    if not host or not user:
        raise RangeInvestigationError(
            "Monitor 1 não está configurado em SSH_SRV_VPN_IP/SSH_SRV_VPN_USER"
        )
    password = get_secret(
        "SSH_BASTION_PASSWORD",
        settings.ssh_bastion_password,
        settings=settings,
        required=True,
    )
    return SSHExecutor(
        host=host,
        port=int(settings.ssh_bastion_port or 22),
        username=user,
        password=password,
        connect_timeout=settings.ssh_connect_timeout,
        private_key_path=settings.ssh_bastion_private_key_path,
        private_key_passphrase=get_secret(
            "SSH_BASTION_PRIVATE_KEY_PASSPHRASE",
            settings.ssh_bastion_private_key_passphrase,
            settings=settings,
        ),
        allow_agent=settings.ssh_allow_agent,
        look_for_keys=settings.ssh_look_for_keys,
        strict_host_key_checking=settings.ssh_strict_host_key_checking,
        known_hosts_path=settings.ssh_known_hosts_path,
    )


def _scan_command(addresses: list[str], ports: list[int], concurrency: int) -> str:
    # Todos os valores são IPs/inteiros já validados. A lista é passada como dados
    # para um shell fixo e não aceita texto arbitrário do operador.
    ip_words = " ".join(shlex.quote(address) for address in addresses)
    port_words = " ".join(str(port) for port in ports)
    return (
        f"set +e; c=0; for ip in {ip_words}; do "
        "( for p in " + port_words + "; do "
        "if timeout 2 bash -c \"</dev/tcp/${ip}/${p}\" 2>/dev/null; then "
        "printf '%s|%s\\n' \"$ip\" \"$p\"; break; fi; done ) & "
        f"c=$((c+1)); if [ $((c % {max(1, concurrency)})) -eq 0 ]; then wait; fi; "
        "done; wait"
    )


def scan_range_from_monitor1(
    range_value: str,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    max_addresses = _int("AGENT_RANGE_MAX_ADDRESSES", 512, 2, 4096)
    max_hosts = _int("AGENT_RANGE_MAX_DISCOVERED_HOSTS", 128, 1, 512)
    concurrency = _int("AGENT_RANGE_SCAN_CONCURRENCY", 32, 1, 128)
    private_only = _bool("AGENT_RANGE_PRIVATE_ONLY", True)
    addresses = _expand_range(
        range_value,
        max_addresses=max_addresses,
        private_only=private_only,
    )
    ports = _scan_ports()
    executor = _monitor_executor(settings)
    started = time.monotonic()
    try:
        report_progress(
            "range_scan_monitor1",
            detail=f"Acessando o Monitor 1 para varrer {len(addresses)} endereço(s).",
            percent=8,
            range=range_value,
            candidate_addresses=len(addresses),
        )
        executor.connect()
        result = executor.run(
            _scan_command(addresses, ports, concurrency),
            EnvironmentType.MONITORING,
            approved=False,
            timeout=max(30, min(600, len(addresses) * 3)),
        )
    finally:
        executor.close()

    responsive: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in str(result.stdout or "").splitlines():
        address, separator, port_text = line.strip().partition("|")
        if not separator or address in seen:
            continue
        try:
            ipaddress.ip_address(address)
            port = int(port_text)
        except (ValueError, TypeError):
            continue
        seen.add(address)
        responsive.append({"address": address, "ssh_port": port})

    if len(responsive) > max_hosts:
        raise RangeInvestigationError(
            f"foram encontrados {len(responsive)} hosts SSH na faixa, acima do limite {max_hosts}; "
            "aumente AGENT_RANGE_MAX_DISCOVERED_HOSTS conscientemente para analisar todos"
        )
    report_progress(
        "range_scan_monitor1",
        status="completed",
        detail=f"Varredura concluída: {len(responsive)} host(s) com SSH acessível.",
        percent=16,
        responsive_hosts=len(responsive),
        candidate_addresses=len(addresses),
    )
    return {
        "range": range_value,
        "monitor1": settings.ssh_bastion_host,
        "ports": ports,
        "candidate_addresses": len(addresses),
        "responsive": responsive,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def _triage_one(
    address: str,
    port: int,
    objective: str,
    settings: Settings,
) -> dict[str, Any]:
    target = resolve_target(
        address,
        EnvironmentType.UNKNOWN,
        port,
        settings=settings,
    )
    executor = build_executor(target, settings=settings)
    try:
        executor.connect()
        with use_progress(lambda _event: None):
            triage = triage_host(
                executor,
                objective=objective,
                environment=EnvironmentType.UNKNOWN,
                label=address,
                timeout=_int("AGENT_RANGE_TRIAGE_TIMEOUT_SECONDS", 35, 10, 120),
            )
        return {
            "address": address,
            "ssh_port": port,
            "connected": True,
            **triage,
        }
    except Exception as exc:
        return {
            "address": address,
            "ssh_port": port,
            "connected": False,
            "status": "inconclusive",
            "confidence": 0,
            "score": 0,
            "summary": f"Falha ao autenticar/coletar: {type(exc).__name__}: {exc}",
            "facts": [],
            "recommendations": ["Validar acesso SSH deste endereço."],
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        executor.close()


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    analysis = dict(result.get("analysis") or {})
    classification = dict(result.get("environment_classification") or {})
    return {
        "address": result.get("target"),
        "hostname": result.get("hostname"),
        "investigation_id": result.get("investigation_id"),
        "environment": classification.get("environment") or "unknown",
        "status": analysis.get("status") or "inconclusive",
        "confidence": int(analysis.get("confidence") or 0),
        "summary": analysis.get("summary"),
        "probable_cause": analysis.get("probable_cause"),
        "conclusion": analysis.get("conclusion"),
        "facts": list(analysis.get("facts") or [])[:10],
        "recommendations": list(analysis.get("recommendations") or [])[:8],
        "approval_available": bool(result.get("approval_token")),
        "playbook": result.get("playbook"),
    }


def _fallback_synthesis(hosts: list[dict[str, Any]]) -> dict[str, Any]:
    if not hosts:
        return {
            "status": "inconclusive",
            "confidence": 0,
            "summary": "Nenhum host recebeu análise aprofundada.",
            "probable_cause": "Nenhuma causa raiz pôde ser confirmada.",
            "conclusion": "A faixa precisa de nova coleta ou ajuste de acesso.",
            "facts": [],
            "recommendations": ["Revisar os hosts que falharam na autenticação e repetir a varredura."],
            "root_host": "",
        }
    root = max(
        hosts,
        key=lambda item: _STATUS_WEIGHT.get(str(item.get("status") or "inconclusive"), 0)
        + int(item.get("confidence") or 0),
    )
    facts: list[str] = []
    for host in hosts:
        label = host.get("hostname") or host.get("address")
        facts.extend(f"[{label}] {fact}" for fact in (host.get("facts") or [])[:5])
    return {
        "status": root.get("status") or "inconclusive",
        "confidence": int(root.get("confidence") or 0),
        "summary": f"A análise correlacionou {len(hosts)} host(s); o achado mais relevante está em {root.get('hostname') or root.get('address')}.",
        "probable_cause": root.get("probable_cause") or "Causa não confirmada.",
        "conclusion": root.get("conclusion") or "Revise os fatos consolidados por host.",
        "facts": facts[:30],
        "recommendations": list(root.get("recommendations") or []),
        "root_host": root.get("address") or "",
    }


def _synthesize_range(
    range_value: str,
    objective: str,
    triage_rows: list[dict[str, Any]],
    deep_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    fallback = _fallback_synthesis(deep_rows)
    payload = {
        "range": range_value,
        "objective": objective,
        "triage": [
            {
                "address": item.get("address"),
                "hostname": item.get("hostname"),
                "status": item.get("status"),
                "score": item.get("score"),
                "summary": item.get("summary"),
                "facts": item.get("facts"),
            }
            for item in triage_rows
        ],
        "deep_analysis": deep_rows,
    }
    prompt = (
        "Você é o sintetizador de causa raiz de uma varredura AIOps em vários servidores. Responda somente JSON válido.\n"
        "Correlacione os hosts sem inventar uma causa única quando as falhas forem independentes.\n"
        "O root_host deve ser um endereço presente em deep_analysis. Diferencie sintoma de causa e use somente fatos coletados.\n"
        "Formato: {\"status\":\"healthy|attention|critical|inconclusive\",\"confidence\":0,"
        "\"summary\":\"...\",\"probable_cause\":\"...\",\"conclusion\":\"...\","
        "\"facts\":[\"...\"],\"recommendations\":[\"...\"],\"root_host\":\"IP\"}.\n\nDADOS:\n"
        + json.dumps(redact_object(payload), ensure_ascii=False, default=str)[:42000]
    )
    result, diagnostics = resilient_model_call(prompt, "range_synthesis")
    if not isinstance(result, dict):
        return fallback, diagnostics
    status = str(result.get("status") or "")
    try:
        confidence = int(result.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = -1
    valid_hosts = {str(item.get("address") or "") for item in deep_rows}
    if status not in _VALID_STATUS or not 0 <= confidence <= 100 or str(result.get("root_host") or "") not in valid_hosts:
        return fallback, diagnostics
    return {
        "status": status,
        "confidence": confidence,
        "summary": str(result.get("summary") or fallback["summary"]),
        "probable_cause": str(result.get("probable_cause") or fallback["probable_cause"]),
        "conclusion": str(result.get("conclusion") or fallback["conclusion"]),
        "facts": [str(item) for item in result.get("facts") or fallback["facts"]][:30],
        "recommendations": [str(item) for item in result.get("recommendations") or fallback["recommendations"]][:16],
        "root_host": str(result.get("root_host") or fallback["root_host"]),
    }, diagnostics


def run_range_investigation(
    range_value: str,
    objective: str,
    *,
    environment: EnvironmentType = EnvironmentType.UNKNOWN,
    mode: str = "propose",
    approve: bool = False,
    ssh_port: int | None = None,
    provider_name: str | None = None,
    model_name: str | None = None,
    playbook_mode: str = "auto",
    playbook_id: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    del approve, ssh_port  # a faixa sempre investiga/propoe primeiro; aprovação é posterior e por host raiz
    settings = settings or get_settings()
    started = time.monotonic()
    general_objective = str(objective or "").strip() or (
        "Realizar análise geral de saúde, identificar falhas de sistema, rede, recursos, containers e monitoramento, "
        "diferenciar sintomas de causas e encontrar a causa raiz quando houver problema."
    )
    scan = scan_range_from_monitor1(range_value, settings=settings)
    responsive = list(scan.get("responsive") or [])
    if not responsive:
        raise RangeInvestigationError(
            f"nenhum host com SSH acessível foi encontrado em {range_value} a partir do Monitor 1"
        )

    triage_rows: list[dict[str, Any]] = []
    total = len(responsive)
    for index, item in enumerate(responsive, start=1):
        raise_if_cancelled("Varredura de faixa cancelada durante a triagem.")
        report_progress(
            "range_triage",
            detail=f"Triagem {index}/{total}: conectando em {item['address']}:{item['ssh_port']}.",
            percent=min(48, 16 + int(index / max(1, total) * 32)),
            current_host=item["address"],
            triaged=index - 1,
            total_hosts=total,
        )
        triage_rows.append(
            _triage_one(
                str(item["address"]),
                int(item["ssh_port"]),
                general_objective,
                settings,
            )
        )

    connected = [item for item in triage_rows if item.get("connected")]
    if not connected:
        raise RangeInvestigationError(
            "a porta SSH respondeu, mas nenhum host aceitou a autenticação configurada em SSH_DEFAULT_USER/SSH_DEFAULT_PASSWORD"
        )

    deep_limit = _int("AGENT_RANGE_DEEP_DIVE_LIMIT", 16, 1, 128)
    full_threshold = _int("AGENT_RANGE_FULL_ANALYSIS_THRESHOLD", 12, 1, 128)
    suspicious = [item for item in connected if int(item.get("score") or 0) >= 20]
    if len(connected) <= full_threshold:
        selected = list(connected)
    else:
        selected = sorted(
            suspicious or connected,
            key=lambda item: (int(item.get("score") or 0), int(item.get("confidence") or 0)),
            reverse=True,
        )[:deep_limit]
    if len(selected) > deep_limit:
        selected = selected[:deep_limit]

    deep_results: list[dict[str, Any]] = []
    deep_summaries: list[dict[str, Any]] = []
    for index, item in enumerate(selected, start=1):
        raise_if_cancelled("Varredura de faixa cancelada durante o aprofundamento.")
        report_progress(
            "range_deep_analysis",
            detail=f"Análise profunda {index}/{len(selected)} em {item['address']}.",
            percent=min(88, 50 + int(index / max(1, len(selected)) * 38)),
            current_host=item["address"],
            deep_analyzed=index - 1,
            deep_total=len(selected),
        )
        try:
            with use_progress(lambda _event: None):
                result = run_target_tracked(
                    str(item["address"]),
                    general_objective,
                    environment=environment,
                    mode="investigate" if mode == "investigate" else "propose",
                    approve=False,
                    ssh_port=int(item.get("ssh_port") or 22),
                    provider_name=provider_name,
                    model_name=model_name,
                    playbook_mode=playbook_mode,
                    playbook_id=playbook_id,
                    settings=settings,
                )
            deep_results.append(result)
            deep_summaries.append(_result_summary(result))
        except Exception as exc:
            deep_summaries.append(
                {
                    "address": item["address"],
                    "status": "inconclusive",
                    "confidence": 0,
                    "summary": f"Falha no aprofundamento: {type(exc).__name__}: {exc}",
                    "probable_cause": "A coleta profunda não foi concluída.",
                    "facts": [],
                    "recommendations": ["Repetir investigação focada neste host."],
                    "approval_available": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    valid_deep = [item for item in deep_summaries if item.get("investigation_id")]
    if not valid_deep:
        raise RangeInvestigationError("nenhum host concluiu a análise profunda após a triagem")

    report_progress(
        "range_synthesis",
        detail="Aplicando Ensemble para correlacionar os hosts e escolher a causa raiz mais sustentada.",
        percent=90,
    )
    synthesis, synthesis_diag = _synthesize_range(
        range_value,
        general_objective,
        triage_rows,
        valid_deep,
    )
    root_host = str(synthesis.get("root_host") or "")
    root_result = next(
        (result for result in deep_results if str(result.get("target") or "") == root_host),
        deep_results[0],
    )

    host_rows: list[dict[str, Any]] = []
    deep_by_address = {str(item.get("address") or ""): item for item in deep_summaries}
    for triage in triage_rows:
        address = str(triage.get("address") or "")
        deep = deep_by_address.get(address)
        host_rows.append(
            {
                "address": address,
                "ssh_port": triage.get("ssh_port"),
                "hostname": (deep or {}).get("hostname") or triage.get("hostname"),
                "connected": bool(triage.get("connected")),
                "triage_score": int(triage.get("score") or 0),
                "triage_summary": triage.get("summary"),
                "deep_analyzed": bool(deep and deep.get("investigation_id")),
                "status": (deep or {}).get("status") or triage.get("status") or "inconclusive",
                "confidence": int((deep or {}).get("confidence") or triage.get("confidence") or 0),
                "investigation_id": (deep or {}).get("investigation_id"),
                "approval_available": bool((deep or {}).get("approval_available")),
                "probable_cause": (deep or {}).get("probable_cause") or triage.get("probable_cause"),
                "error": triage.get("error") or (deep or {}).get("error"),
            }
        )

    range_metadata = {
        "enabled": True,
        "requested_range": range_value,
        "monitor1": scan.get("monitor1"),
        "objective": general_objective,
        "candidate_addresses": scan.get("candidate_addresses"),
        "ssh_responsive": len(responsive),
        "authenticated_and_triaged": len(connected),
        "deep_analyzed": len(valid_deep),
        "root_host": root_host,
        "hosts": host_rows,
        "strategy": (
            "full_analysis_all_authenticated_hosts"
            if len(connected) <= full_threshold
            else "triage_all_then_deep_dive_suspicious"
        ),
        "limits": {
            "deep_dive_limit": deep_limit,
            "full_analysis_threshold": full_threshold,
            "max_discovered_hosts": _int("AGENT_RANGE_MAX_DISCOVERED_HOSTS", 128, 1, 512),
        },
    }

    analysis = dict(root_result.get("analysis") or {})
    analysis.update(
        {
            "status": synthesis["status"],
            "confidence": synthesis["confidence"],
            "summary": synthesis["summary"],
            "probable_cause": synthesis["probable_cause"],
            "conclusion": synthesis["conclusion"],
            "facts": synthesis["facts"],
            "recommendations": synthesis["recommendations"],
            "range_scan": range_metadata,
            "range_synthesis_diagnostics": synthesis_diag,
            "ticket_report": (
                f"Varredura {range_value}: {len(responsive)} host(s) com SSH acessível, "
                f"{len(connected)} autenticado(s) e {len(valid_deep)} analisado(s) em profundidade. "
                f"Host com causa mais sustentada: {root_host or 'não definido'}. "
                f"Causa provável: {synthesis['probable_cause']} Conclusão: {synthesis['conclusion']}"
            ),
        }
    )
    root_result["analysis"] = analysis
    root_result["range_scan"] = range_metadata
    root_result["range_target"] = range_value
    root_result["duration_ms"] = int((time.monotonic() - started) * 1000)
    report_progress(
        "completed",
        status="completed",
        detail=(
            f"Faixa concluída: {len(connected)} host(s) triados, {len(valid_deep)} aprofundado(s); "
            f"causa mais sustentada em {root_host}."
        ),
        percent=100,
        root_host=root_host,
        responsive_hosts=len(responsive),
    )
    return redact_object(root_result) if not root_result.get("approval_token") else {
        **redact_object({key: value for key, value in root_result.items() if key != "approval_token"}),
        "approval_token": root_result.get("approval_token"),
    }
