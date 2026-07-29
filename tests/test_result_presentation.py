from __future__ import annotations

from app.core.settings import Settings
from app.services import result_presentation


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        postgres_dsn="sqlite+pysqlite:///:memory:",
        ai_provider="groq",
    )


def test_ticket_report_uses_only_canonical_confidence() -> None:
    report = result_presentation.build_ticket_report_ptbr(
        {
            "status": "attention",
            "confidence": 70,
            "summary": "A memória RAM está sob atenção.",
            "probable_cause": "Processo com consumo elevado.",
            "conclusion": "Não houve indisponibilidade.",
            "facts": ["Uso de RAM em 82%."],
            "recommendations": ["Acompanhar o consumo."],
            "ticket_report": "Confidence: 95%",
        }
    )

    assert "Confiança validada: 70%" in report
    assert "95%" not in report
    assert "Status da análise: Atenção" in report
    assert "Recomendações:" in report


def test_finalize_result_marks_ptbr_and_syncs_status_confidence(monkeypatch) -> None:
    synchronized = {}
    monkeypatch.setattr(
        result_presentation,
        "_sync_investigation",
        lambda result, analysis: synchronized.update({"result": result, "analysis": analysis}),
    )

    result = result_presentation.finalize_result_presentation(
        {
            "investigation_id": "00000000-0000-0000-0000-000000000001",
            "analysis": {
                "status": "healthy",
                "confidence": 85,
                "summary": "O servidor está operando normalmente.",
                "facts": ["A memória está dentro do limite."],
                "probable_cause": "Não foi identificada falha.",
                "conclusion": "Ambiente saudável.",
                "recommendations": ["Manter o acompanhamento."],
            },
        },
        settings=_settings(),
    )

    assert result["analysis"]["language"] == "pt-BR"
    assert result["analysis"]["confidence"] == 85
    assert result["confidence"] == 85
    assert result["status"] == "healthy"
    assert "Confiança validada: 85%" in result["analysis"]["ticket_report"]
    assert synchronized["analysis"]["confidence"] == 85


def test_english_user_fields_are_translated_without_changing_numbers(monkeypatch) -> None:
    class Provider:
        def generate_json(self, prompt):
            assert "português do Brasil" in prompt
            return (
                {
                    "summary": "O servidor apresenta uso elevado de memória.",
                    "facts": ["A utilização de memória chegou a 82%."],
                    "probable_cause": "Processo com consumo elevado.",
                    "conclusion": "O ambiente requer acompanhamento.",
                    "recommendations": ["Validar o processo responsável."],
                },
                {},
            )

    monkeypatch.setattr(result_presentation, "get_provider", lambda *args, **kwargs: Provider())
    monkeypatch.setattr(result_presentation, "_sync_investigation", lambda *args, **kwargs: None)

    result = result_presentation.finalize_result_presentation(
        {
            "selected_provider": "groq",
            "selected_model": "modelo",
            "analysis": {
                "status": "attention",
                "confidence": 70,
                "summary": "The server has high memory usage and the service is running with available evidence.",
                "facts": ["Memory usage is 82 percent."],
                "probable_cause": "The likely cause is a process with high memory usage.",
                "conclusion": "The investigation should continue with more evidence.",
                "recommendations": ["Check the process and review the service."],
            },
        },
        settings=_settings(),
    )

    assert result["analysis"]["summary"].startswith("O servidor")
    assert result["analysis"]["confidence"] == 70
    assert "Confiança validada: 70%" in result["analysis"]["ticket_report"]
