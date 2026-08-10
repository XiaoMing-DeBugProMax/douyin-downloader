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
const tasksWorkspace = document.querySelector("#tasks-workspace");
const tasksRefresh = document.querySelector("#tasks-refresh");
const tasksStatus = document.querySelector("#tasks-status");
const tasksError = document.querySelector("#tasks-error");
const tasksEmpty = document.querySelector("#tasks-empty");
const tasksList = document.querySelector("#tasks-list");
const taskCancelDialog = document.querySelector("#task-cancel-dialog");
const taskCancelRetain = document.querySelector("#task-cancel-retain");
const taskCancelDelete = document.querySelector("#task-cancel-delete");

let currentParse = null;
let isParsing = false;
let currentArchiveState = "not_archived";
let currentAudioOutcome = "not_requested";
let currentDescriptionOutcome = "not_requested";
let settingsLoaded = false;
let tasksLoading = false;
let tasksRefreshTimer = null;
let pendingCancelTaskId = null;

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
  if (name === "tasks") {
    loadTasks();
  } else if (tasksRefreshTimer !== null) {
    window.clearTimeout(tasksRefreshTimer);
    tasksRefreshTimer = null;
  }
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

const TASK_LIFECYCLES = {
  running: "活动",
  finished: "已结束",
  paused: "已暂停",
  interrupted: "已中断",
  cancelled: "已取消",
};
const TASK_PHASES = {
  resolving: "解析",
  downloading: "下载",
  verifying: "校验",
  processing: "生成成果",
  promoting: "登记成果",
  idle: "空闲",
};
const TASK_RESULTS = {
  none: "未结束",
  success: "成功",
  partial_success: "部分成功",
  failed: "失败",
  cancelled: "已取消",
};

function createElement(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "未知";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

function taskStateFields(task) {
  const fields = createElement("dl", "task-state-fields");
  for (const [label, value] of [
    ["生命周期", TASK_LIFECYCLES[task.lifecycle] || task.lifecycle],
    ["执行阶段", TASK_PHASES[task.phase] || task.phase],
    ["最终结果", TASK_RESULTS[task.result] || task.result],
  ]) {
    const field = createElement("div");
    field.append(createElement("dt", "", label), createElement("dd", "", value));
    fields.append(field);
  }
  return fields;
}

function taskProgress(task) {
  const progress = task.progress;
  const node = createElement("p", "task-progress");
  const parts = [
    `${progress.completed_items} / ${progress.total_items} 个作品`,
    progress.total_bytes === null
      ? formatBytes(progress.completed_bytes)
      : `${formatBytes(progress.completed_bytes)} / ${formatBytes(progress.total_bytes)}`,
  ];
  if (progress.percentage !== null) parts.push(`${progress.percentage}%`);
  const estimatesAreCurrent = task.lifecycle === "running" && task.phase === "downloading";
  if (estimatesAreCurrent && progress.speed_bytes_per_second !== null) {
    parts.push(`${formatBytes(progress.speed_bytes_per_second)}/s`);
  }
  if (estimatesAreCurrent && progress.eta_seconds !== null) {
    parts.push(`ETA ${progress.eta_seconds} 秒`);
  }
  node.textContent = parts.join(" · ");
  return node;
}

function taskError(error) {
  const node = createElement("div", "task-error");
  node.append(
    createElement("strong", "", error.code),
    createElement("span", "", error.message),
    createElement("span", "", error.suggestion),
  );
  return node;
}

async function controlTask(taskId, action, button, retainParts = null) {
  button.disabled = true;
  tasksError.textContent = "";
  const options = { method: "POST" };
  if (retainParts !== null) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify({ retain_parts: retainParts });
  }
  try {
    const response = await fetch(
      `/api/tasks/${encodeURIComponent(taskId)}/${action}`,
      options,
    );
    if (!response.ok) {
      tasksError.textContent = await responseErrorMessage(response);
      return;
    }
    await loadTasks(true);
  } catch (_) {
    tasksError.textContent = UNKNOWN_ERROR;
  } finally {
    button.disabled = false;
  }
}

function openCancelDialog(taskId) {
  pendingCancelTaskId = taskId;
  taskCancelDialog.showModal();
}

function taskControlActions(task) {
  const actions = createElement("div", "task-control-actions");
  if (task.lifecycle === "running") {
    const pause = createElement("button", "button button-secondary task-pause", "暂停");
    pause.type = "button";
    pause.addEventListener("click", () => controlTask(task.task_id, "pause", pause));
    actions.append(pause);
  } else if (task.lifecycle === "paused") {
    const resume = createElement("button", "button button-primary task-resume", "继续");
    resume.type = "button";
    resume.addEventListener("click", () => controlTask(task.task_id, "resume", resume));
    actions.append(resume);
  }
  if (task.lifecycle === "running" || task.lifecycle === "paused") {
    const cancel = createElement("button", "button button-quiet task-cancel", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", () => openCancelDialog(task.task_id));
    actions.append(cancel);
  }
  return actions;
}

function renderWorkTask(work) {
  const node = createElement("article", "task-work");
  const heading = createElement("div", "task-work-heading");
  heading.append(
    createElement("h4", "", `作品 ${work.aweme_id}`),
    taskControlActions(work.task),
  );
  node.append(
    heading,
    taskStateFields(work.task),
    taskProgress(work.task),
  );
  if (work.task.error) node.append(taskError(work.task.error));
  return node;
}

function renderSourceTask(source, index) {
  const node = createElement("article", "task-source");
  node.append(
    createElement("h3", "", `来源任务 ${index + 1}`),
    taskStateFields(source.task),
    taskProgress(source.task),
  );
  if (source.task.error) node.append(taskError(source.task.error));
  const details = createElement("details", "task-work-details");
  details.append(
    createElement("summary", "", `展开 ${source.work_tasks.length} 个作品明细`),
  );
  const works = createElement("div", "task-work-list");
  for (const work of source.work_tasks) works.append(renderWorkTask(work));
  details.append(works);
  node.append(details);
  return node;
}

function taskIsTerminal(task) {
  return task.lifecycle === "finished" || task.lifecycle === "cancelled";
}

async function clearTaskOperation(operationId, button) {
  button.disabled = true;
  try {
    const response = await fetch(`/api/tasks/${encodeURIComponent(operationId)}`, {
      method: "DELETE",
    });
    if (!response.ok) {
      tasksError.textContent = await responseErrorMessage(response);
      return;
    }
    await loadTasks(true);
  } catch (_) {
    tasksError.textContent = UNKNOWN_ERROR;
  } finally {
    button.disabled = false;
  }
}

function renderTaskOperation(operation) {
  const card = createElement("article", "task-operation");
  const heading = createElement("div", "task-operation-heading");
  const title = createElement("div");
  title.append(
    createElement("p", "task-kind", "归档操作"),
    createElement("h2", "task-id", operation.task.task_id),
  );
  heading.append(title);
  if (taskIsTerminal(operation.task)) {
    const clear = createElement("button", "button button-quiet task-clear", "清理记录");
    clear.type = "button";
    clear.addEventListener("click", () => clearTaskOperation(operation.task.task_id, clear));
    heading.append(clear);
  } else {
    heading.append(taskControlActions(operation.task));
  }
  card.append(heading, taskStateFields(operation.task), taskProgress(operation.task));
  if (operation.task.error) card.append(taskError(operation.task.error));
  const sources = createElement("div", "task-source-list");
  operation.source_tasks.forEach((source, index) => {
    sources.append(renderSourceTask(source, index));
  });
  card.append(sources);
  return card;
}

function renderTasks(payload) {
  tasksList.replaceChildren();
  const operations = Array.isArray(payload?.operations) ? payload.operations : [];
  tasksEmpty.hidden = operations.length !== 0;
  for (const operation of operations) tasksList.append(renderTaskOperation(operation));
  return operations;
}

function scheduleTaskRefresh(operations) {
  if (tasksRefreshTimer !== null) window.clearTimeout(tasksRefreshTimer);
  tasksRefreshTimer = null;
  if (
    !tasksWorkspace.hidden &&
    operations.some((operation) => !taskIsTerminal(operation.task))
  ) {
    tasksRefreshTimer = window.setTimeout(() => loadTasks(true), 1000);
  }
}

async function loadTasks(force = false) {
  if (tasksLoading && !force) return;
  tasksLoading = true;
  tasksStatus.textContent = "正在读取任务历史…";
  tasksError.textContent = "";
  try {
    const response = await fetch("/api/tasks");
    if (!response.ok) {
      tasksError.textContent = await responseErrorMessage(response);
      tasksStatus.textContent = "";
      return;
    }
    const operations = renderTasks(await response.json());
    tasksStatus.textContent = operations.length ? `共 ${operations.length} 个归档操作。` : "";
    scheduleTaskRefresh(operations);
  } catch (_) {
    tasksError.textContent = UNKNOWN_ERROR;
    tasksStatus.textContent = "";
  } finally {
    tasksLoading = false;
  }
}

tasksRefresh.addEventListener("click", () => loadTasks(true));

async function confirmTaskCancellation(retainParts, button) {
  const taskId = pendingCancelTaskId;
  if (taskId === null) return;
  pendingCancelTaskId = null;
  taskCancelDialog.close();
  await controlTask(taskId, "cancel", button, retainParts);
}

taskCancelRetain.addEventListener("click", () => {
  confirmTaskCancellation(true, taskCancelRetain);
});
taskCancelDelete.addEventListener("click", () => {
  confirmTaskCancellation(false, taskCancelDelete);
});
taskCancelDialog.addEventListener("close", () => {
  pendingCancelTaskId = null;
});

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

function setArchiveState(
  state,
  audioOutcome = "not_requested",
  descriptionOutcome = "not_requested",
) {
  currentArchiveState = state;
  currentAudioOutcome = audioOutcome;
  currentDescriptionOutcome = descriptionOutcome;
  const archived = state === "archived";
  const needsRepair = state === "needs_repair";
  const locationUnavailable = state === "location_unavailable";
  const unavailable = state === "unavailable";
  const statusText = archived
    ? "已归档"
    : needsRepair
      ? "待修复"
      : locationUnavailable
        ? "位置不可用"
    : unavailable
      ? "归档暂时不可用"
      : "尚未归档";
  const audioText = {
    ready: "音轨：已提取",
    no_audio: "音轨：无音轨",
    probe_failed: "音轨：探测失败",
    extract_failed: "音轨：提取失败",
    validation_failed: "音轨：校验失败",
    missing: "音轨：待补充",
  }[audioOutcome];
  const descriptionText = {
    ready: "文案：已导出",
    missing: "文案：待补充",
  }[descriptionOutcome];
  archiveStatus.textContent = [statusText, audioText, descriptionText]
    .filter(Boolean)
    .join(" · ");
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
    if (currentParse?.awemeId === awemeId) {
      setArchiveState(
        payload.status,
        payload.audio_outcome,
        payload.description_outcome,
      );
    }
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
    setArchiveState(
      payload.status,
      payload.audio_outcome,
      payload.description_outcome,
    );
    showNotice("本地归档已更新。");
  } catch (_) {
    showError(UNKNOWN_ERROR);
  } finally {
    setArchiveState(
      currentArchiveState,
      currentAudioOutcome,
      currentDescriptionOutcome,
    );
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
