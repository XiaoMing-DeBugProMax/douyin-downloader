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

function renderPreview(video) {
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
    renderPreview(payload.video);
    showNotice("解析完成。");
  } catch (error) {
    showError(normalizeError(error));
  } finally {
    button.disabled = false;
  }
});
