const THEME_STORAGE_KEY = "douyin-local-theme";
const THEMES = new Set(["light", "dark", "calm"]);
const UNKNOWN_ERROR = "解析服务暂时不可用，请稍后重试。";

const root = document.documentElement;
const form = document.querySelector("#parse-form");
const shareText = document.querySelector("#share-text");
const parseButton = document.querySelector("#parse-button");
const parseButtonLabel = parseButton.querySelector(".button-label");
const status = document.querySelector("#status");
const errorAlert = document.querySelector("#error");
const result = document.querySelector("#result");
const cover = document.querySelector("#cover");
const author = document.querySelector("#author");
const description = document.querySelector("#description");
const duration = document.querySelector("#duration");
const downloadDefault = document.querySelector("#download-default");
const downloadCustom = document.querySelector("#download-custom");
const parseAnother = document.querySelector("#parse-another");
const themeButton = document.querySelector("#theme-button");
const themeMenu = document.querySelector("#theme-menu");
const themeChoices = Array.from(themeMenu.querySelectorAll("[data-theme]"));

let currentParse = null;
let isParsing = false;

function storedTheme() {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return THEMES.has(value) ? value : "light";
  } catch (_) {
    return "light";
  }
}

function applyTheme(theme, persist = false) {
  const selectedTheme = THEMES.has(theme) ? theme : "light";
  root.dataset.theme = selectedTheme;
  for (const choice of themeChoices) {
    choice.setAttribute("aria-checked", String(choice.dataset.theme === selectedTheme));
  }
  if (persist) {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, selectedTheme);
    } catch (_) {
      // The theme still applies for this page when storage is unavailable.
    }
  }
}

function setThemeMenu(open) {
  themeMenu.hidden = !open;
  themeButton.setAttribute("aria-expanded", String(open));
}

function focusedThemeIndex() {
  const index = themeChoices.indexOf(document.activeElement);
  return index >= 0 ? index : 0;
}

function selectedThemeIndex() {
  const index = themeChoices.findIndex((choice) => choice.getAttribute("aria-checked") === "true");
  return index >= 0 ? index : 0;
}

function focusThemeChoice(index) {
  const normalizedIndex = (index + themeChoices.length) % themeChoices.length;
  themeChoices[normalizedIndex].focus();
}

applyTheme(storedTheme());

themeButton.addEventListener("click", (event) => {
  const opening = themeButton.getAttribute("aria-expanded") !== "true";
  setThemeMenu(opening);
  if (opening && event.detail === 0) {
    focusThemeChoice(selectedThemeIndex());
  }
});

themeButton.addEventListener("keydown", (event) => {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  event.preventDefault();
  setThemeMenu(true);
  const selectedIndex = selectedThemeIndex();
  focusThemeChoice(event.key === "ArrowDown" ? selectedIndex : selectedIndex - 1);
});

for (const choice of themeChoices) {
  choice.addEventListener("click", () => {
    applyTheme(choice.dataset.theme, true);
    setThemeMenu(false);
    themeButton.focus();
  });
}

themeMenu.addEventListener("keydown", (event) => {
  if (event.key === "Tab") {
    setThemeMenu(false);
    return;
  }

  let nextIndex = null;
  if (event.key === "ArrowDown") {
    nextIndex = focusedThemeIndex() + 1;
  } else if (event.key === "ArrowUp") {
    nextIndex = focusedThemeIndex() - 1;
  } else if (event.key === "Home") {
    nextIndex = 0;
  } else if (event.key === "End") {
    nextIndex = themeChoices.length - 1;
  }

  if (nextIndex !== null) {
    event.preventDefault();
    focusThemeChoice(nextIndex);
  }
});

document.addEventListener("click", (event) => {
  if (!themeMenu.hidden && !event.target.closest(".theme-picker")) {
    setThemeMenu(false);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !themeMenu.hidden) {
    setThemeMenu(false);
    themeButton.focus();
  }
});

function showNotice(message) {
  status.textContent = message;
  errorAlert.textContent = "";
}

function showError(message) {
  errorAlert.textContent = message;
  status.textContent = "";
}

async function responseErrorMessage(response) {
  try {
    const payload = await response.json();
    if (typeof payload?.error?.message === "string" && payload.error.message.trim()) {
      return payload.error.message;
    }
  } catch (_) {
    // Fall through to the stable unknown-error copy.
  }
  return UNKNOWN_ERROR;
}

function renderPreview(video, parseToken) {
  currentParse = {
    token: parseToken,
    suggestedName:
      typeof video.suggested_filename === "string" ? video.suggested_filename : "video.mp4",
  };
  cover.src = video.cover_url;
  author.textContent = video.author;
  description.textContent = video.description || "暂无文案";
  duration.textContent = `${Math.round(video.duration_ms / 1000)} 秒`;
  result.hidden = false;
}

function setParsingState(parsing) {
  isParsing = parsing;
  if (parsing) {
    parseButton.disabled = true;
    parseButtonLabel.textContent = "正在解析";
  } else {
    parseButton.disabled = false;
    parseButtonLabel.textContent = "开始解析";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (isParsing) return;

  const input = shareText.value.trim();
  if (!input) {
    showError("没有识别到抖音链接，请粘贴完整分享文案。");
    shareText.focus();
    return;
  }

  setParsingState(true);
  currentParse = null;
  result.hidden = true;
  showNotice("正在解析，请稍候…");

  try {
    const response = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ share_text: input }),
    });
    if (!response.ok) {
      showError(await responseErrorMessage(response));
      return;
    }

    const payload = await response.json();
    renderPreview(payload.video, payload.parse_token);
    showNotice("解析完成，可以下载了。");
  } catch (_) {
    showError(UNKNOWN_ERROR);
  } finally {
    setParsingState(false);
  }
});

function downloadUrl(token) {
  return `/api/download/${encodeURIComponent(token)}`;
}

function startDefaultDownload(token) {
  window.location.assign(downloadUrl(token));
}

async function saveToChosenLocation(token, suggestedName) {
  if (typeof window.showSaveFilePicker !== "function") {
    showNotice("当前浏览器不支持选择保存位置，将使用默认下载方式。");
    startDefaultDownload(token);
    return;
  }

  try {
    const handle = await window.showSaveFilePicker({
      suggestedName,
      types: [{ description: "MP4 视频", accept: { "video/mp4": [".mp4"] } }],
    });
    const response = await fetch(downloadUrl(token));
    if (!response.ok || !response.body) {
      showError(await responseErrorMessage(response));
      return;
    }
    const writable = await handle.createWritable();
    await response.body.pipeTo(writable);
    showNotice("视频已保存。");
  } catch (error) {
    if (error?.name === "AbortError") return;
    showError(UNKNOWN_ERROR);
  }
}

downloadDefault.addEventListener("click", () => {
  if (currentParse) startDefaultDownload(currentParse.token);
});

downloadCustom.addEventListener("click", () => {
  if (currentParse) {
    saveToChosenLocation(currentParse.token, currentParse.suggestedName);
  }
});

parseAnother.addEventListener("click", () => {
  currentParse = null;
  form.reset();
  result.hidden = true;
  cover.removeAttribute("src");
  author.textContent = "";
  description.textContent = "";
  duration.textContent = "";
  showNotice("");
  shareText.focus();
});
