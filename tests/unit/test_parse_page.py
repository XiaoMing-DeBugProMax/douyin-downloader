from pathlib import Path

STATIC_DIR = Path(__file__).parents[2] / "src" / "douyin_downloader" / "web" / "static"


def test_parse_page_uses_safe_text_rendering_and_required_controls() -> None:
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    for element_id in ("share-text", "parse-button", "status", "error", "result"):
        assert f'id="{element_id}"' in page
    assert "innerHTML" not in script
    assert "document.cookie" not in script
    assert "textContent" in script
    assert "button.disabled = true" in script
