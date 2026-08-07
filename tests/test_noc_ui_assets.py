from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_product_polish_injects_noc_navigation_and_dashboard() -> None:
    source = (PROJECT_ROOT / "app" / "ui" / "product-polish.js").read_text(encoding="utf-8")

    required = (
        'viewMeta.noc = ["NOC AUTÔNOMO", "Incidentes"]',
        'button.dataset.view = "noc"',
        'section.id = "view-noc"',
        "/ui/api/noc/dashboard",
        "/ui/api/noc/incidents/",
        "FLAPPING",
        "loadNocDashboard",
    )
    for item in required:
        assert item in source


def test_noc_styles_are_packaged_with_ui_assets() -> None:
    css = PROJECT_ROOT / "app" / "ui" / "noc.css"
    assert css.is_file()
    text = css.read_text(encoding="utf-8")
    assert ".noc-summary-grid" in text
    assert ".noc-flapping" in text
