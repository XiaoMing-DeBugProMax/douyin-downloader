from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect

UNKNOWN_ERROR = "解析服务暂时不可用，请稍后重试。"


def contrast_ratio(page: Page, selector: str, background_token: str, pseudo: str = "") -> float:
    return page.locator(selector).evaluate(
        """
        (element, options) => {
          const toRgb = (color) => {
            const probe = document.createElement("span");
            probe.style.color = color;
            document.body.append(probe);
            const values = getComputedStyle(probe).color.match(/[\\d.]+/g).slice(0, 3);
            probe.remove();
            return values.map(Number);
          };
          const luminance = (rgb) => {
            const channels = rgb.map((value) => {
              const channel = value / 255;
              return channel <= 0.04045
                ? channel / 12.92
                : ((channel + 0.055) / 1.055) ** 2.4;
            });
            return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
          };
          const style = getComputedStyle(element, options.pseudo || null);
          const background = toRgb(
            getComputedStyle(document.documentElement)
              .getPropertyValue(options.backgroundToken)
              .trim()
          );
          const foreground = toRgb(style.color);
          const opacity = Number(style.opacity);
          const rendered = foreground.map(
            (value, index) => value * opacity + background[index] * (1 - opacity)
          );
          const lighter = Math.max(luminance(rendered), luminance(background));
          const darker = Math.min(luminance(rendered), luminance(background));
          return (lighter + 0.05) / (darker + 0.05);
        }
        """,
        {"backgroundToken": background_token, "pseudo": pseudo},
    )


def parse_video(page: Page, local_app_url: str) -> None:
    page.goto(local_app_url)
    page.locator("#share-text").fill("https://v.douyin.com/example/")
    page.locator("#parse-button").click()
    page.locator("#result").wait_for(state="visible")


def test_navigation_failures_never_expose_launch_tokens(page: Page, local_app_url: str) -> None:
    sentinel = "SENTINEL-LAUNCH-TOKEN-MUST-NOT-LEAK"
    parsed = urlsplit(local_app_url)
    navigation_url = local_app_url
    if parsed.query:
        navigation_url = f"{parsed.scheme}://{parsed.netloc}/?launch_token={sentinel}"

    requested_urls: list[str] = []
    page.on("request", lambda request: requested_urls.append(request.url))
    page.route(navigation_url, lambda route: route.abort("failed"))
    with pytest.raises(PlaywrightError) as caught:
        page.goto(navigation_url)

    observed = "\n".join((navigation_url, *requested_urls, str(caught.value)))
    assert "launch_token" not in observed
    assert sentinel not in observed


def test_parse_preview_theme_and_default_download(page: Page, local_app_url: str) -> None:
    parse_video(page, local_app_url)
    assert page.locator("#author").inner_text() == "钟哥！！"
    assert page.locator("#duration").inner_text() == "15 秒"

    with page.expect_download() as download_info:
        page.locator("#download-default").click()
    assert download_info.value.suggested_filename.endswith(".mp4")

    page.locator("#theme-button").click()
    page.locator('[data-theme="dark"]').click()
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert page.evaluate("Object.keys(localStorage)") == ["douyin-local-theme"]

    page.reload()
    assert page.locator("html").get_attribute("data-theme") == "dark"
    assert page.locator("#result").is_hidden()


def test_duplicate_parse_click_has_one_active_request(page: Page, local_app_url: str) -> None:
    parse_requests: list[str] = []
    page.on(
        "request",
        lambda request: parse_requests.append(request.url)
        if request.url.endswith("/api/parse")
        else None,
    )
    page.goto(local_app_url)
    page.locator("#share-text").fill("https://v.douyin.com/example/")
    page.locator("#parse-button").dblclick()
    assert page.locator("#parse-button").is_disabled()
    page.locator("#result").wait_for(state="visible")
    assert page.locator("#result").count() == 1
    assert len(parse_requests) == 1


def test_parse_another_returns_to_focused_empty_input(page: Page, local_app_url: str) -> None:
    parse_video(page, local_app_url)
    page.locator("#parse-another").click()
    expect(page.locator("#result")).to_be_hidden()
    expect(page.locator("#share-text")).to_be_empty()
    expect(page.locator("#share-text")).to_be_focused()


def test_empty_unsupported_and_unknown_errors_use_chinese_copy(
    page: Page,
    local_app_url: str,
) -> None:
    page.goto(local_app_url)
    page.locator("#parse-button").click()
    expect(page.locator("#error")).to_have_text("没有识别到抖音链接，请粘贴完整分享文案。")

    page.locator("#share-text").fill("https://example.com/unsupported")
    page.locator("#parse-button").click()
    expect(page.locator("#error")).to_have_text("目前只支持抖音公开视频。")

    page.route("**/api/parse", lambda route: route.abort())
    page.locator("#share-text").fill("https://v.douyin.com/example/")
    page.locator("#parse-button").click()
    expect(page.locator("#error")).to_have_text(UNKNOWN_ERROR)


def test_custom_picker_cancel_is_silent(page: Page, local_app_url: str) -> None:
    page.add_init_script(
        """
        window.showSaveFilePicker = async () => {
          throw new DOMException("cancelled", "AbortError");
        };
        """
    )
    parse_video(page, local_app_url)
    status_before = page.locator("#status").inner_text()
    page.locator("#download-custom").click()
    expect(page.locator("#error")).to_be_empty()
    expect(page.locator("#status")).to_have_text(status_before)


def test_supported_picker_streams_bytes_to_chosen_file(page: Page, local_app_url: str) -> None:
    page.add_init_script(
        """
        window.__pickerOptions = null;
        window.__writtenBytes = [];
        window.showSaveFilePicker = async (options) => {
          window.__pickerOptions = options;
          return {
            createWritable: async () => new WritableStream({
              write(chunk) {
                window.__writtenBytes.push(...new Uint8Array(chunk));
              },
            }),
          };
        };
        """
    )
    parse_video(page, local_app_url)
    page.locator("#download-custom").click()
    expect(page.locator("#status")).to_have_text("视频已保存。")
    assert page.evaluate("window.__pickerOptions.suggestedName.endsWith('.mp4')") is True
    assert page.evaluate("window.__pickerOptions.types[0].accept['video/mp4']") == [".mp4"]
    assert page.evaluate("window.__writtenBytes") == list(b"mp4-data")
    expect(page.locator("#error")).to_be_empty()


def test_missing_picker_falls_back_to_default_download(page: Page, local_app_url: str) -> None:
    page.add_init_script("delete window.showSaveFilePicker;")
    parse_video(page, local_app_url)
    with page.expect_download() as download_info:
        page.locator("#download-custom").click()
    assert download_info.value.suggested_filename.endswith(".mp4")
    expect(page.locator("#status")).to_have_text(
        "当前浏览器不支持选择保存位置，将使用默认下载方式。"
    )


def test_keyboard_focus_theme_menu_and_skip_link(page: Page, local_app_url: str) -> None:
    page.goto(local_app_url)
    page.keyboard.press("Tab")
    expect(page.locator(".skip-link")).to_be_focused()
    assert page.locator(".skip-link").evaluate(
        "(element) => getComputedStyle(element).outlineStyle"
    ) != "none"

    page.locator("#theme-button").focus()
    expect(page.locator("#theme-button")).to_have_attribute("aria-haspopup", "menu")
    page.keyboard.press("Enter")
    expect(page.locator("#theme-menu")).to_be_visible()
    light_choice = page.locator('#theme-menu [data-theme="light"]')
    expect(light_choice).to_be_focused()
    assert light_choice.evaluate(
        "(element) => parseFloat(getComputedStyle(element).outlineWidth)"
    ) >= 2
    page.keyboard.press("ArrowDown")
    expect(page.locator('#theme-menu [data-theme="dark"]')).to_be_focused()
    page.keyboard.press("End")
    expect(page.locator('#theme-menu [data-theme="calm"]')).to_be_focused()
    page.keyboard.press("Home")
    expect(light_choice).to_be_focused()
    page.keyboard.press("ArrowUp")
    expect(page.locator('#theme-menu [data-theme="calm"]')).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#theme-menu")).to_be_hidden()
    expect(page.locator("#theme-button")).to_be_focused()
    page.keyboard.press("Space")
    expect(page.locator("#theme-menu")).to_be_visible()
    expect(light_choice).to_be_focused()
    page.keyboard.press("End")
    page.keyboard.press("Space")
    expect(page.locator("html")).to_have_attribute("data-theme", "calm")
    expect(page.locator("#theme-menu")).to_be_hidden()
    expect(page.locator("#theme-button")).to_be_focused()

    page.keyboard.press("Enter")
    expect(page.locator('#theme-menu [data-theme="calm"]')).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.locator("#theme-menu")).to_be_hidden()
    expect(page.locator("#share-text")).to_be_focused()

    page.locator("#theme-button").focus()
    page.keyboard.press("Enter")
    expect(page.locator('#theme-menu [data-theme="calm"]')).to_be_focused()
    page.keyboard.press("Shift+Tab")
    expect(page.locator("#theme-menu")).to_be_hidden()
    expect(page.locator("#theme-button")).to_be_focused()


def test_light_theme_small_text_meets_wcag_aa(page: Page, local_app_url: str) -> None:
    page.goto(local_app_url)
    for selector, background, pseudo in (
        (".eyebrow", "--page", ""),
        (".intro-copy", "--page", ""),
        (".input-hint", "--panel", ""),
        ("#share-text", "--panel-soft", "::placeholder"),
        (".usage-note", "--page", ""),
    ):
        assert contrast_ratio(page, selector, background, pseudo) >= 4.5


def test_mobile_result_and_theme_menu_stay_inside_viewport(
    page: Page,
    local_app_url: str,
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    parse_video(page, local_app_url)
    page.locator("#theme-button").click()

    viewport_width = page.evaluate("window.innerWidth")
    document_width = page.evaluate("document.documentElement.scrollWidth")
    menu_box = page.locator("#theme-menu").bounding_box()
    assert document_width == viewport_width
    assert menu_box is not None
    assert menu_box["x"] >= 0
    assert menu_box["x"] + menu_box["width"] <= viewport_width

    for selector in ("#download-default", "#download-custom", "#parse-another"):
        box = page.locator(selector).bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= viewport_width


def test_static_assets_are_local_and_avoid_unsafe_rendering() -> None:
    static_dir = (
        Path(__file__).parents[2] / "src" / "douyin_downloader" / "web" / "static"
    )
    page_markup = (static_dir / "index.html").read_text(encoding="utf-8")
    script = (static_dir / "app.js").read_text(encoding="utf-8")
    e2e_fixtures = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")

    assert "innerHTML" not in script
    assert "document.cookie" not in script
    assert "new Blob" not in script
    assert 'src="http' not in page_markup
    assert 'href="http' not in page_markup
    assert "RedactedLaunchURL" not in e2e_fixtures
    assert "issue_launch_token" not in e2e_fixtures
    assert "launch_token" not in e2e_fixtures
    assert "add_cookies" in e2e_fixtures
