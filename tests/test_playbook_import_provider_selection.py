from app.services.intelligent_playbook_import import _automatic_as_none


def test_automatic_provider_values_use_configured_default() -> None:
    assert _automatic_as_none(None) is None
    assert _automatic_as_none("") is None
    assert _automatic_as_none("auto") is None
    assert _automatic_as_none("automático") is None
    assert _automatic_as_none("default") is None


def test_explicit_provider_is_preserved() -> None:
    assert _automatic_as_none("groq") == "groq"
    assert _automatic_as_none("  ollama  ") == "ollama"
