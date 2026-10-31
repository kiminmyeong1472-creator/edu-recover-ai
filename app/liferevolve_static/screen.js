"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const input = byId("screen-file");
  const selection = byId("selection");
  const selectedPreview = byId("selected-preview");
  const feedback = byId("feedback");
  const loginRecovery = byId("login-recovery");
  const saveButton = byId("save-screen");
  const clearButton = byId("clear-selection");
  const refreshButton = byId("refresh-screens");
  const libraryStatus = byId("library-status");
  const list = byId("saved-screens");
  const dialog = byId("image-dialog");
  let selected = null;
  let selectionEpoch = 0;
  let busy = false;
  let loading = false;
  let maxBytes = 8 * 1024 * 1024;

  function message(text, state = "") {
    feedback.textContent = text;
    feedback.className = `feedback${state ? ` ${state}` : ""}`;
  }

  function bytesLabel(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return "크기 미확인";
    return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)}MB` : `${Math.ceil(bytes / 1024)}KB`;
  }

  function setBusy(value) {
    busy = value;
    input.disabled = value;
    saveButton.disabled = value;
    clearButton.disabled = value;
    saveButton.textContent = value ? "선택한 화면을 저장하는 중…" : "플랫폼에 저장 ↑";
  }

  function clearSelected() {
    selectionEpoch += 1;
    if (selected) URL.revokeObjectURL(selected.url);
    selected = null;
    input.value = "";
    selectedPreview.removeAttribute("src");
    selection.hidden = true;
  }

  function sameOriginImage(path) {
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/life/api/screens/")) {
      throw new Error("화면 파일 주소를 확인할 수 없습니다.");
    }
    return url.href;
  }

  function statusError(status) {
    if (status === 401) {
      loginRecovery.hidden = false;
      return "로그인이 만료되었습니다. 다시 로그인한 뒤 저장하십시오.";
    }
    if (status === 403) return "현재 화면에서 요청을 처리할 수 없습니다. 페이지를 새로 연 뒤 다시 시도하십시오.";
    if (status === 413) return "화면 파일이 너무 큽니다. 더 작은 파일을 선택하십시오.";
    if (status === 429 || status === 507) return "보관할 수 있는 수량 또는 용량을 초과했습니다. 필요 없는 화면을 삭제한 뒤 다시 시도하십시오.";
    if (status === 503) return "지금은 플랫폼에 저장할 수 없습니다. 선택한 원본은 휴대폰에 보관하고 나중에 다시 시도하십시오.";
    if (status === 400 || status === 415 || status === 422) return "이 파일을 화면 이미지로 확인할 수 없습니다. PNG·JPG·WebP 원본을 선택하십시오.";
    if (status === 404) return "해당 화면 자료를 찾을 수 없습니다. 목록을 새로 확인하십시오.";
    return "요청을 처리하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도하십시오.";
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
    if (!response.ok) throw new Error(statusError(response.status));
    return response.status === 204 ? null : response.json();
  }

  function viewImage(src, title) {
    byId("dialog-title").textContent = title || "원본 화면 보기";
    byId("full-image").src = src;
    if (typeof dialog.showModal === "function") dialog.showModal();
    else window.open(src, "_blank", "noopener,noreferrer");
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderCard(screen) {
    const imageUrl = sameOriginImage(screen.image_url);
    const downloadUrl = sameOriginImage(screen.download_url);
    const article = element("article", "screen-card");
    const preview = element("button", "screen-thumbnail");
    preview.type = "button";
    preview.setAttribute("aria-label", `${screen.name || "보관한 화면"} 확대`);
    const image = element("img");
    image.src = imageUrl;
    image.alt = screen.name || "보관한 화면";
    image.loading = "lazy";
    image.addEventListener("error", () => {
      image.alt = "화면을 불러오지 못했습니다. 다시 로그인하거나 목록을 새로 확인하십시오.";
    });
    preview.append(image);
    preview.addEventListener("click", () => viewImage(imageUrl, screen.name));
    const body = element("div", "screen-card-body");
    const title = element("h3", "", screen.name || "화면 자료");
    const meta = element("p", "screen-card-meta", `${screen.width} × ${screen.height} · ${bytesLabel(screen.byte_size)}`);
    const date = new Date(screen.created_at);
    const time = element("p", "screen-card-meta", Number.isNaN(date.getTime()) ? "저장 시각 미확인" : date.toLocaleString("ko-KR"));
    const stored = element("span", "screen-card-status", "원본 보관 · 과거 자료");
    const actions = element("div", "screen-card-actions");
    const download = element("a", "", "원본 저장 ↓");
    download.href = downloadUrl;
    download.setAttribute("download", screen.name || "screen.png");
    const remove = element("button", "button-reset delete-screen", "삭제");
    remove.type = "button";
    remove.addEventListener("click", async () => {
      if (!window.confirm("플랫폼에 보관한 이 화면을 삭제하시겠습니까? 휴대폰의 원본 파일은 삭제하지 않습니다.")) return;
      remove.disabled = true;
      try {
        await api(`/life/api/screens/${encodeURIComponent(screen.id)}`, { method: "DELETE", headers: { "X-LifeRevolve-Action": "screen-import" } });
        message("플랫폼에서 화면을 삭제했습니다. 휴대폰의 원본은 그대로 보관됩니다.", "success");
        await loadScreens();
      } catch (error) {
        message(error.message || "화면을 삭제하지 못했습니다.", "error");
      } finally {
        remove.disabled = false;
      }
    });
    actions.append(download, remove);
    body.append(title, meta, time, stored, actions);
    article.append(preview, body);
    return article;
  }

  async function loadScreens() {
    if (loading) return;
    loading = true;
    refreshButton.disabled = true;
    libraryStatus.className = "library-status";
    libraryStatus.textContent = "보관한 화면을 불러오는 중입니다.";
    try {
      const data = await api("/life/api/screens");
      const screens = Array.isArray(data.screens) ? data.screens : [];
      const cards = screens.map(renderCard);
      list.replaceChildren(...cards);
      byId("saved-count").textContent = String(screens.length);
      byId("empty-library").hidden = screens.length !== 0;
      if (data.limits && Number.isFinite(data.limits.max_image_bytes) && data.limits.max_image_bytes > 0) {
        maxBytes = data.limits.max_image_bytes;
        byId("file-limit").textContent = `한 장당 최대 ${bytesLabel(maxBytes)}`;
      }
      libraryStatus.textContent = screens.length ? `${screens.length}장의 보관한 화면을 불러왔습니다. 글자 읽기와 AI 분석은 실행하지 않았습니다.` : "아직 플랫폼에 보관한 화면이 없습니다.";
      loginRecovery.hidden = true;
    } catch (error) {
      libraryStatus.className = "library-status error";
      libraryStatus.textContent = error.message || "보관한 화면을 불러오지 못했습니다.";
      byId("empty-library").hidden = true;
    } finally {
      loading = false;
      refreshButton.disabled = false;
    }
  }

  input.addEventListener("change", async () => {
    if (busy) return;
    const file = input.files && input.files[0];
    clearSelected();
    if (!file) {
      message("화면 파일 선택을 취소했습니다.");
      return;
    }
    const typeOk = ["image/png", "image/jpeg", "image/webp"].includes(file.type);
    const extensionOk = /\.(png|jpe?g|webp)$/i.test(file.name);
    if (!(typeOk || (!file.type && extensionOk)) || file.size < 1 || file.size > maxBytes) {
      message(`PNG·JPG·WebP 파일을 ${bytesLabel(maxBytes)} 이하로 선택하십시오.`, "error");
      return;
    }
    const epoch = selectionEpoch;
    const url = URL.createObjectURL(file);
    const probe = new Image();
    probe.src = url;
    try {
      await probe.decode();
      if (epoch !== selectionEpoch) {
        URL.revokeObjectURL(url);
        return;
      }
      selected = { file, url, width: probe.naturalWidth, height: probe.naturalHeight };
      selectedPreview.src = url;
      byId("selected-name").textContent = file.name || "선택한 화면";
      byId("selected-detail").textContent = `${probe.naturalWidth} × ${probe.naturalHeight} · ${bytesLabel(file.size)} · 아직 전송하지 않았습니다.`;
      selection.hidden = false;
      message("원본을 확인했습니다. 보관하려면 ‘플랫폼에 저장’을 선택하십시오.");
    } catch (_error) {
      URL.revokeObjectURL(url);
      if (epoch === selectionEpoch) message("화면 파일을 열지 못했습니다. 다른 원본 파일을 선택하십시오.", "error");
    }
  });

  function base64File(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("선택한 파일을 읽지 못했습니다."));
      reader.onload = () => {
        const data = String(reader.result || "");
        const comma = data.indexOf(",");
        if (comma < 0) reject(new Error("선택한 파일을 읽지 못했습니다."));
        else resolve(data.slice(comma + 1));
      };
      reader.readAsDataURL(file);
    });
  }

  saveButton.addEventListener("click", async () => {
    if (busy || !selected) return;
    const current = selected;
    setBusy(true);
    message("선택한 원본을 플랫폼에 저장하는 중입니다. 완료될 때까지 기다려 주십시오.");
    try {
      const base64 = await base64File(current.file);
      const receipt = await api("/life/api/screens", { method: "POST", headers: { "Content-Type": "application/json", "X-LifeRevolve-Action": "screen-import" }, body: JSON.stringify({ name: current.file.name || "screen.png", base64 }) });
      if (!receipt || !receipt.screen || receipt.screen.status !== "STORED") throw new Error("저장 완료를 확인하지 못했습니다. 보관함을 새로 확인하십시오.");
      clearSelected();
      message("플랫폼에 원본 화면을 저장했습니다. 보관함에서 다시 확인하십시오.", "success");
      await loadScreens();
    } catch (error) {
      message(error.message || "저장하지 못했습니다. 선택한 파일을 확인한 뒤 다시 시도하십시오.", "error");
    } finally {
      setBusy(false);
    }
  });

  clearButton.addEventListener("click", () => { if (!busy) { clearSelected(); message("선택을 해제했습니다. 전송하지 않았습니다."); } });
  byId("preview-selection").addEventListener("click", () => { if (selected) viewImage(selected.url, selected.file.name); });
  refreshButton.addEventListener("click", loadScreens);
  byId("close-dialog").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => byId("full-image").removeAttribute("src"));
  window.addEventListener("pagehide", () => { if (selected) URL.revokeObjectURL(selected.url); });
  loadScreens();
})();
