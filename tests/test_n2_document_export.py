from __future__ import annotations

import io
import zipfile

from pypdf import PdfReader

from app.services.n2_document_export_runtime import export_n2_document
from app.services.n2_documentation import sanitize_n2_review


def _sample_review() -> dict:
    return {
        "schema": "n2-documentation-review-v1",
        "site_id": "cqe",
        "client": "CACIQUE ENERGIA",
        "date": "12/08/2026",
        "responsibles": {
            "infra": "Fulano",
            "dba": "Ciclano",
            "noc": "Flavio",
            "review": "Luis",
            "review_noc": "Maria",
        },
        "selected_hosts": [
            {
                "host": "cqe-dbpostgres",
                "ip": "192.168.106.75",
                "kind": "server",
                "role": "database",
                "environment": "production",
                "fields": {
                    "server": "cqe-dbpostgres",
                    "address_ipv4": "192.168.106.75",
                    "address_vpn": "172.27.232.153",
                    "hostname": "cqe-dbpostgres",
                    "processor": "Intel Xeon",
                    "memory": "16 GiB",
                    "storage": "/dev/mapper/root 80G 40G 40G 50% /",
                    "os": "Oracle Linux 8.10",
                },
                "collection_notes": "Coleta somente leitura concluída.",
            }
        ],
        "sections": [
            {
                "id": "database",
                "title": "Banco de dados / TOTVS / TNSNAMES",
                "fields": [
                    {"key": "rdbms_version", "label": "Versão Oracle / RDBMS", "control": "text", "value": "19c"},
                    {"key": "instances", "label": "Instâncias", "control": "text", "value": "WINT"},
                    {"key": "sid", "label": "SID", "control": "text", "value": "WINT"},
                    {"key": "totvs_username", "label": "Username técnico TOTVS", "control": "text", "value": "wint"},
                    {"key": "serial_number", "label": "Número de série TOTVS", "control": "text", "value": "12345"},
                    {"key": "sgdb_structure", "label": "Estrutura SGDB", "control": "textarea", "value": "/u01/app/oracle"},
                    {"key": "tnsnames", "label": "TNSNAMES", "control": "textarea", "value": "WINT=(DESCRIPTION=...)"},
                    {"key": "database_notes", "label": "Evidências", "control": "textarea", "value": "Oracle identificado por arquivos e processos."},
                ],
            },
            {
                "id": "backup",
                "title": "Backup Oracle / ERP e métodos de validação",
                "fields": [
                    {"key": "backup_strategy", "label": "Estratégia", "control": "textarea", "value": "Backup redundante validado."},
                    {"key": "datapump_frequency", "label": "Datapump", "control": "text", "value": "Diário"},
                    {"key": "rman_frequency", "label": "RMAN", "control": "text", "value": "Diário"},
                    {"key": "logical_backup_method", "label": "Lógico", "control": "textarea", "value": "Log de datapump validado."},
                    {"key": "physical_backup_method", "label": "Físico", "control": "textarea", "value": "Log RMAN validado."},
                ],
            },
            {
                "id": "monitoring",
                "title": "Monitoramento",
                "fields": [
                    {"key": "monitoring_url", "label": "URL", "control": "text", "value": "https://monitor.exemplo"},
                    {"key": "monitoring_user", "label": "Usuário", "control": "text", "value": "monitor"},
                    {"key": "monitoring_site", "label": "Site", "control": "text", "value": "cqe"},
                    {"key": "monitoring_endpoint", "label": "Endpoint", "control": "text", "value": "172.27.232.153:6558"},
                    {"key": "monitoring_host_count", "label": "Hosts", "control": "text", "value": "4"},
                    {"key": "monitoring_problem_count", "label": "Problemas", "control": "text", "value": "0"},
                    {"key": "monitoring_notes", "label": "Notas", "control": "textarea", "value": "Checkmk ativo."},
                ],
            },
            {
                "id": "closing",
                "title": "Considerações finais",
                "fields": [
                    {"key": "closing_notes", "label": "Considerações finais", "control": "textarea", "value": "Ambiente revisado pelo N2."}
                ],
            },
        ],
        "security": {"credentials_included": False, "server_reboot": "absolute_denial"},
    }


def test_sanitize_n2_review_drops_sensitive_keys_and_redacts_inline_values() -> None:
    review = _sample_review()
    review["password"] = "segredo-super-sensivel"
    review["metadata"] = {"community": "public", "safe": "ok", "notes": "token=abc123 outro texto"}

    sanitized = sanitize_n2_review(review)

    assert "password" not in sanitized
    assert "community" not in sanitized["metadata"]
    assert sanitized["metadata"]["safe"] == "ok"
    assert "abc123" not in sanitized["metadata"]["notes"]
    assert "[REDACTED]" in sanitized["metadata"]["notes"]


def test_n2_docx_export_is_valid_and_keeps_sensitive_template_cells_blank() -> None:
    content, filename, media_type = export_n2_document(_sample_review(), "docx")

    assert content.startswith(b"PK")
    assert filename.endswith(".docx")
    assert media_type.endswith("wordprocessingml.document")
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "CACIQUE ENERGIA" in document_xml
    assert "Informações para Ativação na TOTVS" in document_xml
    assert "Mapeamento do Winthor" in document_xml
    assert "Retenção de Backups Obsoletos" in document_xml
    assert "Considerações Finais" in document_xml
    assert "segredo-super-sensivel" not in document_xml


def test_n2_pdf_export_is_valid_and_contains_core_sections() -> None:
    content, filename, media_type = export_n2_document(_sample_review(), "pdf")

    assert content.startswith(b"%PDF")
    assert filename.endswith(".pdf")
    assert media_type == "application/pdf"
    reader = PdfReader(io.BytesIO(content))
    assert len(reader.pages) >= 2
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "CACIQUE ENERGIA" in text
    assert "Infraestrutura" in text
    assert "Banco de Dados" in text
    assert "Monitoramento" in text
    assert "Considerações Finais" in text
    assert "segredo-super-sensivel" not in text
