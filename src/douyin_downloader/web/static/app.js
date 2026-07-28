function showNotice(message) {
  document.querySelector("#status").textContent = message;
  document.querySelector("#error").textContent = "";
}

function showError(message) {
  document.querySelector("#error").textContent = message;
  document.querySelector("#status").textContent = "";
}

async function toAppError(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch (_) {
    payload = null;
  }
  const error = new Error(payload?.error?.message || "解析服务暂时不可用，请稍后重试。");
  error.code = payload?.error?.code || "UNKNOWN";
  return error;
}

function normalizeError(error) {
  return error instanceof Error && error.message ? error.message : "解析服务暂时不可用，请稍后重试。";
}

let currentParse = null;

function renderPreview(video, parseToken) {
  currentParse = { token: parseToken, suggestedName: video.suggested_filename };
  document.querySelector("#video-cover").src = video.cover_url;
  document.querySelector("#video-author").textContent = video.author;
  document.querySelector("#video-description").textContent = video.description;
  document.querySelector("#video-duration").textContent = `${Math.round(video.duration_ms / 1000)} 秒`;
  document.querySelector("#result").hidden = false;
}

document.querySelector("#parse-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#parse-button");
  const shareText = document.querySelector("#share-text").value;
  button.disabled = true;
  document.querySelector("#result").hidden = true;
  showNotice("正在解析…");
  try {
    const response = await fetch("/api/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ share_text: shareText }),
    });
    if (!response.ok) {
      throw await toAppError(response);
    }
    const payload = await response.json();
    renderPreview(payload.video, payload.parse_token);
    showNotice("解析完成。");
  } catch (error) {
    showError(normalizeError(error));
  } finally {
    button.disabled = false;
  }
});

function downloadUrl(token) {
  return `/api/download/${encodeURIComponent(token)}`;
}

async function saveToChosenLocation(token, suggestedName) {
  if (!window.showSaveFilePicker) {
    showNotice("当前浏览器不支持选择保存位置，将使用默认下载方式。");
    window.location.assign(downloadUrl(token));
    return;
  }
  try {
    const handle = await window.showSaveFilePicker({
      suggestedName,
      types: [{ description: "MP4 视频", accept: { "video/mp4": [".mp4"] } }],
    });
    const response = await fetch(downloadUrl(token));
    if (!response.ok || !response.body) throw await toAppError(response);
    const writable = await handle.createWritable();
    await response.body.pipeTo(writable);
    showNotice("视频已保存。");
  } catch (error) {
    if (error && error.name === "AbortError") return;
    showError(normalizeError(error));
  }
}

document.querySelector("#default-download").addEventListener("click", () => {
  if (currentParse) window.location.assign(downloadUrl(currentParse.token));
});

document.querySelector("#save-to-location").addEventListener("click", () => {
  if (currentParse) saveToChosenLocation(currentParse.token, currentParse.suggestedName);
});
