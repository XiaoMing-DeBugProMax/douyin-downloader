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
const tags = document.querySelector("#tags");
const duration = document.querySelector("#duration");
const downloadDefault = document.querySelector("#download-default");
const downloadCustom = document.querySelector("#download-custom");
const archiveStatus = document.querySelector("#archive-status");
const archiveStart = document.querySelector("#archive-start");
const archiveOpen = document.querySelector("#archive-open");
const parseAnother = document.querySelector("#parse-another");
const themeButton = document.querySelector("#theme-button");
const themeMenu = document.querySelector("#theme-menu");
const themeChoices = Array.from(themeMenu.querySelectorAll("[data-theme]"));
const workspaceTabs = Array.from(document.querySelectorAll("[data-workspace]"));
const workspacePanels = Array.from(document.querySelectorAll(".workspace-panel"));
const settingsForm = document.querySelector("#settings-form");
const settingsRoot = document.querySelector("#settings-root");
const settingsRootSelect = document.querySelector("#settings-root-select");
const settingsTemplate = document.querySelector("#settings-template");
const settingsAudio = document.querySelector("#settings-audio");
const settingsDescription = document.querySelector("#settings-description");
const settingsConcurrency = document.querySelector("#settings-concurrency");
const settingsRetry = document.querySelector("#settings-retry");
const settingsSave = document.querySelector("#settings-save");
const settingsStatus = document.querySelector("#settings-status");
const settingsError = document.querySelector("#settings-error");

let currentParse = null;
let isParsing = false;
let currentArchiveState = "not_archived";
let settingsLoaded = false;

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

function workspaceTabIndex(tab) {
  const index = workspaceTabs.indexOf(tab);
  return index >= 0 ? index : 0;
}

function focusWorkspaceTab(index) {
  const normalized = (index + workspaceTabs.length) % workspaceTabs.length;
  workspaceTabs[normalized].focus();
}

function activateWorkspace(name) {
  for (const tab of workspaceTabs) {
    const selected = tab.dataset.workspace === name;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  for (const panel of workspacePanels) {
    panel.hidden = panel.id !== `${name}-workspace`;
  }
  if (name === "settings") loadSettings();
}

for (const tab of workspaceTabs) {
  tab.addEventListener("click", () => activateWorkspace(tab.dataset.workspace));
  tab.addEventListener("keydown", (event) => {
    const index = workspaceTabIndex(tab);
    let nextIndex = null;
    if (event.key === "ArrowRight") nextIndex = index + 1;
    if (event.key === "ArrowLeft") nextIndex = index - 1;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = workspaceTabs.length - 1;
    if (nextIndex !== null) {
      event.preventDefault();
      focusWorkspaceTab(nextIndex);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activateWorkspace(tab.dataset.workspace);
    }
  });
}

function showNotice(message) {
  status.textContent = message;
  errorAlert.textContent = "";
}

function showError(message) {
  errorAlert.textContent = message;
  status.textContent = "";
}

async function responseError(response) {
  try {
    const payload = await response.json();
    if (typeof payload?.error?.message === "string" && payload.error.message.trim()) {
      return { code: payload.error.code, message: payload.error.message };
    }
  } catch (_) {
    // Fall through to the stable unknown-error copy.
  }
  return { code: "UNKNOWN", message: UNKNOWN_ERROR };
}

async function responseErrorMessage(response) {
  return (await responseError(response)).message;
}

function renderSettings(payload) {
  settingsRoot.value = typeof payload.archive_root === "string" ? payload.archive_root : "";
  settingsTemplate.value = payload.naming_template;
  settingsAudio.checked = payload.profile.include_audio;
  settingsDescription.checked = payload.profile.include_description;
  settingsConcurrency.value = String(payload.download_concurrency);
  settingsRetry.value = String(payload.retry_limit);
}

function showSettingsNotice(message) {
  settingsStatus.textContent = message;
  settingsError.textContent = "";
}

function showSettingsError(message, target = null) {
  settingsError.textContent = message;
  settingsStatus.textContent = "";
  if (target) target.focus();
}

async function loadSettings(force = false) {
  if (settingsLoaded && !force) return;
  try {
    const response = await fetch("/api/settings");
    if (!response.ok) {
      showSettingsError(await responseErrorMessage(response));
      return;
    }
    renderSettings(await response.json());
    settingsLoaded = true;
  } catch (_) {
    showSettingsError(UNKNOWN_ERROR);
  }
}

settingsRootSelect.addEventListener("click", async () => {
  settingsRootSelect.disabled = true;
  try {
    const response = await fetch("/api/settings/archive-root/select", { method: "POST" });
    if (!response.ok) {
      const failure = await responseError(response);
      if (failure.code !== "ARCHIVE_SELECTION_CANCELLED") {
        showSettingsError(failure.message);
      }
      return;
    }
    renderSettings(await response.json());
    settingsLoaded = true;
    showSettingsNotice("归档根目录已更新。")
  } catch (_) {
    showSettingsError(UNKNOWN_ERROR);
  } finally {
    settingsRootSelect.disabled = false;
  }
});

settingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const concurrency = Number(settingsConcurrency.value);
  const retryLimit = Number(settingsRetry.value);
  const template = settingsTemplate.value.trim();
  if (!template || /[\\/]/u.test(template)) {
    showSettingsError("基础名称模板不能包含目录分隔符。", settingsTemplate);
    return;
  }
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 5) {
    showSettingsError("下载并发必须是 1 到 5 之间的整数。", settingsConcurrency);
    return;
  }
  if (!Number.isInteger(retryLimit) || retryLimit < 0 || retryLimit > 3) {
    showSettingsError("失败重试必须是 0 到 3 之间的整数。", settingsRetry);
    return;
  }

  settingsSave.disabled = true;
  try {
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        naming_template: template,
        profile: {
          include_audio: settingsAudio.checked,
          include_description: settingsDescription.checked,
        },
        download_concurrency: concurrency,
        retry_limit: retryLimit,
      }),
    });
    if (!response.ok) {
      showSettingsError(await responseErrorMessage(response));
      return;
    }
    renderSettings(await response.json());
    settingsLoaded = true;
    showSettingsNotice("设置已保存。");
  } catch (_) {
    showSettingsError(UNKNOWN_ERROR);
  } finally {
    settingsSave.disabled = false;
  }
});

function splitDescription(value) {
  const original = typeof value === "string" ? value.trim() : "";
  const match = original.match(/((?:#[^\s#]+\s*)+)$/u);
  if (!match) return { description: original, tags: "" };

  return {
    description: original.slice(0, match.index).trimEnd(),
    tags: match[1].trim(),
  };
}

function renderPreview(video, parseToken) {
  currentParse = {
    token: parseToken,
    awemeId: video.aweme_id,
    suggestedName:
      typeof video.suggested_filename === "string" ? video.suggested_filename : "video.mp4",
  };
  const displayText = splitDescription(video.description);
  const descriptionText = displayText.description || "暂无文案";
  cover.src = video.cover_url;
  author.textContent = video.author;
  author.title = video.author;
  description.textContent = descriptionText;
  description.title = descriptionText;
  tags.textContent = displayText.tags;
  tags.title = displayText.tags;
  tags.hidden = !displayText.tags;
  duration.textContent = `${Math.round(video.duration_ms / 1000)} 秒`;
  result.hidden = false;
  setArchiveState("not_archived");
  refreshArchiveStatus(video.aweme_id);
}

function setArchiveState(state) {
  currentArchiveState = state;
  const archived = state === "archived";
  const needsRepair = state === "needs_repair";
  const locationUnavailable = state === "location_unavailable";
  const unavailable = state === "unavailable";
  archiveStatus.textContent = archived
    ? "已归档"
    : needsRepair
      ? "待修复"
      : locationUnavailable
        ? "位置不可用"
    : unavailable
      ? "归档暂时不可用"
      : "尚未归档";
  archiveStart.hidden = archived || locationUnavailable;
  archiveStart.disabled = unavailable || locationUnavailable;
  archiveStart.textContent = needsRepair ? "修复本地归档" : "加入本地归档";
  archiveOpen.hidden = !(archived || needsRepair);
}

async function refreshArchiveStatus(awemeId) {
  try {
    const response = await fetch(`/api/archive/work/${encodeURIComponent(awemeId)}`);
    if (!response.ok) {
      if (currentParse?.awemeId === awemeId) setArchiveState("unavailable");
      return;
    }
    const payload = await response.json();
    if (currentParse?.awemeId === awemeId) setArchiveState(payload.status);
  } catch (_) {
    if (currentParse?.awemeId === awemeId) setArchiveState("unavailable");
  }
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

archiveStart.addEventListener("click", async () => {
  if (!currentParse || archiveStart.disabled) return;
  archiveStart.disabled = true;
  archiveStart.textContent = "正在归档";
  try {
    const response = await fetch("/api/archive/single", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parse_token: currentParse.token }),
    });
    if (!response.ok) {
      const failure = await responseError(response);
      if (failure.code !== "ARCHIVE_SELECTION_CANCELLED") showError(failure.message);
      return;
    }
    const payload = await response.json();
    setArchiveState(payload.status);
    showNotice("本地归档已更新。");
  } catch (_) {
    showError(UNKNOWN_ERROR);
  } finally {
    setArchiveState(currentArchiveState);
  }
});

archiveOpen.addEventListener("click", async () => {
  if (!currentParse) return;
  try {
    const response = await fetch(
      `/api/archive/work/${encodeURIComponent(currentParse.awemeId)}/open`,
      { method: "POST" },
    );
    if (!response.ok) showError(await responseErrorMessage(response));
  } catch (_) {
    showError(UNKNOWN_ERROR);
  }
});

parseAnother.addEventListener("click", () => {
  currentParse = null;
  form.reset();
  result.hidden = true;
  cover.removeAttribute("src");
  author.textContent = "";
  author.removeAttribute("title");
  description.textContent = "";
  description.removeAttribute("title");
  tags.textContent = "";
  tags.removeAttribute("title");
  tags.hidden = true;
  duration.textContent = "";
  setArchiveState("not_archived");
  showNotice("");
  shareText.focus();
});
