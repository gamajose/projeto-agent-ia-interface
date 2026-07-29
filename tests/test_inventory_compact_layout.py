from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_inventory_cards_use_dense_responsive_grid() -> None:
    css = (PROJECT_ROOT / "app" / "ui" / "product-polish.css").read_text(encoding="utf-8")

    assert "#view-inventory .inventory-grid" in css
    assert "repeat(auto-fill, minmax(270px, 1fr))" in css
    assert "padding: 13px 14px 10px" in css
    assert "margin-top: 10px" in css
    assert "grid-template-columns: 1fr" in css
    assert "#view-inventory .inventory-card .card-actions .ghost-button" in css
