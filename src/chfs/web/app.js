"use strict";

import { concatenateBytes, hashBytes, toHex } from "./sha256.js";

// 浏览器端只负责交互和协议适配；路径验证、权限和磁盘操作始终由服务端内核执行。
const state = {
  path: "",
  token: sessionStorage.getItem("chfs_token") || "",
  principal: null,
  authenticationAvailable: false,
};

const elements = {
  rows: document.querySelector("#fileRows"),
  empty: document.querySelector("#emptyState"),
  loading: document.querySelector("#loadingState"),
  count: document.querySelector("#itemCount"),
  breadcrumbs: document.querySelector("#breadcrumbs"),
  loginButton: document.querySelector("#loginButton"),
  loginDialog: document.querySelector("#loginDialog"),
  loginForm: document.querySelector("#loginForm"),
  loginError: document.querySelector("#loginError"),
  folderDialog: document.querySelector("#folderDialog"),
  folderForm: document.querySelector("#folderForm"),
  folderError: document.querySelector("#folderError"),
  filePicker: document.querySelector("#filePicker"),
  dropZone: document.querySelector("#dropZone"),
  tray: document.querySelector("#uploadTray"),
  progress: document.querySelector("#uploadProgress"),
  overallProgress: document.querySelector("#uploadOverallProgress"),
  overallText: document.querySelector("#uploadOverallText"),
  uploadTitle: document.querySelector("#uploadTitle"),
  uploadDetail: document.querySelector("#uploadDetail"),
  uploadCounter: document.querySelector("#uploadCounter"),
  uploadSpeed: document.querySelector("#uploadSpeed"),
  toast: document.querySelector("#toast"),
  uploadButton: document.querySelector("#uploadButton"),
  newFolderButton: document.querySelector("#newFolderButton"),
};

function headers(json = false) {
  const result = {};
  if (state.token) result.Authorization = `Bearer ${state.token}`;
  if (json) result["Content-Type"] = "application/json";
  return result;
}

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers(options.json), ...(options.headers || {}) } });
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `请求失败（${response.status}）`);
    error.code = payload?.error?.code;
    throw error;
  }
  return payload;
}

function joinPath(name) { return state.path ? `${state.path}/${name}` : name; }
function encode(value) { return encodeURIComponent(value); }

async function refreshSession() {
  try {
    const data = await api("/api/v1/session");
    state.principal = data.principal;
    state.authenticationAvailable = data.authentication_available;
  } catch (error) {
    if (error.code === "authentication_failed") {
      state.token = "";
      sessionStorage.removeItem("chfs_token");
      const guestData = await api("/api/v1/session");
      state.principal = guestData.principal;
      state.authenticationAvailable = guestData.authentication_available;
    } else throw error;
  }
  elements.loginButton.textContent = state.principal.authenticated ? `${state.principal.name} · 退出` : "登录";
  elements.loginButton.hidden = !state.authenticationAvailable && !state.principal.authenticated;
  updatePermissionControls();
}

function can(permission) {
  const permissions = state.principal?.permissions || [];
  return permissions.includes("admin") || permissions.includes(permission);
}

function updatePermissionControls() {
  const mayWrite = can("write");
  elements.uploadButton.disabled = !mayWrite;
  elements.newFolderButton.disabled = !mayWrite;
  const hint = mayWrite ? "" : (state.authenticationAvailable ? "登录具有写入权限的账户后使用" : "服务端未开放写入权限");
  elements.uploadButton.title = hint;
  elements.newFolderButton.title = hint;
}

async function loadFiles() {
  elements.loading.hidden = false;
  elements.empty.hidden = true;
  elements.rows.replaceChildren();
  renderBreadcrumbs();
  try {
    const data = await api(`/api/v1/files?path=${encode(state.path)}`);
    elements.count.textContent = `${data.entries.length} 项`;
    elements.loading.hidden = true;
    elements.empty.hidden = data.entries.length !== 0;
    for (const entry of data.entries) elements.rows.append(createRow(entry));
  } catch (error) {
    elements.loading.textContent = error.message;
    elements.count.textContent = "无法读取";
    if (error.code === "permission_denied" || error.code === "authentication_failed") elements.loginDialog.showModal();
  }
}

function createRow(entry) {
  const row = document.createElement("tr");
  const nameCell = document.createElement("td");
  const nameWrap = document.createElement("div");
  nameWrap.className = "file-name";
  const type = document.createElement("span");
  type.className = "file-type";
  type.textContent = entry.type === "directory" ? "DIR" : "FILE";
  const name = document.createElement("button");
  name.className = "name-button";
  name.type = "button";
  name.textContent = entry.name;
  name.title = entry.name;
  name.addEventListener("click", () => entry.type === "directory" ? navigate(entry.path) : download(entry.path));
  nameWrap.append(type, name);
  nameCell.append(nameWrap);

  const size = document.createElement("td");
  size.textContent = entry.type === "directory" ? "—" : formatBytes(entry.size);
  const modified = document.createElement("td");
  modified.textContent = new Date(entry.modified_ns / 1_000_000).toLocaleString("zh-CN", { dateStyle: "medium", timeStyle: "short" });
  const actionsCell = document.createElement("td");
  const actions = document.createElement("div");
  actions.className = "row-actions";
  if (entry.type === "file") actions.append(actionButton("下载", () => download(entry.path)));
  if (can("delete")) actions.append(actionButton("删除", () => removeEntry(entry), true));
  actionsCell.append(actions);
  row.append(nameCell, size, modified, actionsCell);
  return row;
}

function actionButton(label, handler, danger = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `button button-quiet${danger ? " button-danger" : ""}`;
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function renderBreadcrumbs() {
  elements.breadcrumbs.replaceChildren();
  const parts = state.path ? state.path.split("/") : [];
  const root = breadcrumb("全部文件", "");
  elements.breadcrumbs.append(root);
  parts.forEach((part, index) => elements.breadcrumbs.append(breadcrumb(part, parts.slice(0, index + 1).join("/"))));
}

function breadcrumb(label, path) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "breadcrumb";
  button.textContent = label;
  button.addEventListener("click", () => navigate(path));
  return button;
}

function navigate(path) { state.path = path; loadFiles(); }

function download(path) {
  // GET 下载可使用登录时签发的窄路径 HttpOnly Cookie，不把令牌暴露在 URL 中。
  const anchor = document.createElement("a");
  anchor.href = `/api/v1/content?path=${encode(path)}`;
  anchor.download = "";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}

async function removeEntry(entry) {
  const description = entry.type === "directory" ? "文件夹及其中所有内容" : "文件";
  if (!window.confirm(`确定删除${description}“${entry.name}”吗？此操作无法撤销。`)) return;
  try {
    await api(`/api/v1/files?path=${encode(entry.path)}&recursive=${entry.type === "directory"}`, { method: "DELETE" });
    toast("已删除");
    await loadFiles();
  } catch (error) { toast(error.message, true); }
}

async function uploadFiles(files) {
  if (!can("write")) { toast(state.authenticationAvailable ? "当前身份没有上传权限，请先登录" : "服务端未开放上传权限", true); return; }
  const batch = {
    count: files.length,
    totalBytes: files.reduce((total, file) => total + file.size, 0),
    completedBytes: 0,
    networkBytes: 0,
    lastSpeedBytes: 0,
    lastSpeedTime: performance.now(),
    speed: 0,
  };
  elements.tray.hidden = false;
  for (const [index, file] of files.entries()) {
    const path = joinPath(file.name);
    try { await uploadOne(file, path, batch, index); }
    catch (error) { toast(`${file.name}：${error.message}`, true); elements.tray.hidden = true; return; }
    batch.completedBytes += file.size;
  }
  updateUploadDisplay(batch, files.at(-1), files.length - 1, files.at(-1)?.size || 0, "上传完成");
  await new Promise(resolve => setTimeout(resolve, 650));
  elements.tray.hidden = true;
  toast(`已上传 ${files.length} 个文件`);
  await loadFiles();
}

async function uploadOne(file, path, batch, index) {
  updateUploadDisplay(batch, file, index, 0, "准备上传");

  const storageKey = `chfs-resume:${path}:${file.size}:${file.lastModified}`;
  let resumeKey = localStorage.getItem(storageKey);
  if (!resumeKey) {
    resumeKey = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    localStorage.setItem(storageKey, resumeKey);
  }
  let session = await api("/api/v1/uploads", {
    method: "POST",
    json: true,
    body: JSON.stringify({ path, size: file.size, resume_key: resumeKey, overwrite: false }),
  });
  const chunkSize = session.chunk_size;
  const chunkDigests = [];

  // 续传时重新读取本地前缀并核对分块清单，防止同名同大小但内容不同的文件混接。
  if (session.offset > 0) {
    updateUploadDisplay(batch, file, index, 0, "验证续传点");
    for (let position = 0; position < session.offset; position += chunkSize) {
      const digest = await hashBytes(await file.slice(position, Math.min(position + chunkSize, session.offset)).arrayBuffer());
      chunkDigests.push(digest);
      updateUploadDisplay(batch, file, index, Math.min(position + chunkSize, session.offset), "验证续传点");
    }
    const prefixManifest = toHex(await hashBytes(concatenateBytes(chunkDigests)));
    if (prefixManifest !== session.prefix_manifest_sha256) {
      await api(`/api/v1/uploads/${encode(session.upload_id)}`, { method: "DELETE" });
      localStorage.removeItem(storageKey);
      throw new Error("本地文件与服务器续传数据不一致，旧临时数据已清理，请重新选择文件");
    }
  }

  for (let position = session.offset; position < file.size; position += chunkSize) {
    const end = Math.min(position + chunkSize, file.size);
    const bytes = await file.slice(position, end).arrayBuffer();
    const digest = await hashBytes(bytes);
    chunkDigests.push(digest);
    updateUploadDisplay(batch, file, index, position, "校验并上传");
    session = await sendChunkWithRetry(
      session.upload_id,
      position,
      bytes,
      toHex(digest),
      file.size,
      (loaded, networkDelta) => {
        batch.networkBytes += networkDelta;
        updateUploadDisplay(batch, file, index, position + loaded, "正在上传");
      },
    );
    updateUploadDisplay(batch, file, index, session.offset, "分块已校验");
  }
  const manifest = toHex(await hashBytes(concatenateBytes(chunkDigests)));
  updateUploadDisplay(batch, file, index, file.size, "正在原子提交");
  const completed = await api(`/api/v1/uploads/${encode(session.upload_id)}/complete`, {
    method: "POST",
    json: true,
    body: JSON.stringify({ manifest_sha256: manifest }),
  });
  localStorage.removeItem(storageKey);
  elements.uploadDetail.textContent = `SHA-256 ${completed.sha256.slice(0, 12)}…`;
  elements.progress.value = 100;
}

async function sendChunkWithRetry(uploadId, offset, bytes, digest, totalSize, onProgress) {
  let lastError;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try { return await sendChunk(uploadId, offset, bytes, digest, totalSize, onProgress); }
    catch (error) {
      lastError = error;
      if (attempt < 3) await new Promise(resolve => setTimeout(resolve, 350 * attempt));
    }
  }
  throw lastError;
}

function sendChunk(uploadId, offset, bytes, digest, totalSize, onProgress) {
  return new Promise((resolve, reject) => {
    let lastLoaded = 0;
    const xhr = new XMLHttpRequest();
    xhr.open("PATCH", `/api/v1/uploads/${encode(uploadId)}?offset=${offset}`);
    xhr.setRequestHeader("X-CHFS-Chunk-SHA256", digest);
    if (state.token) xhr.setRequestHeader("Authorization", `Bearer ${state.token}`);
    xhr.upload.addEventListener("progress", event => {
      if (event.lengthComputable) {
        const delta = Math.max(0, event.loaded - lastLoaded);
        lastLoaded = event.loaded;
        onProgress(event.loaded, delta);
      }
    });
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
      else {
        try { reject(new Error(JSON.parse(xhr.responseText).error.message)); }
        catch { reject(new Error(`分块上传失败（${xhr.status}）`)); }
      }
    });
    xhr.addEventListener("error", () => reject(new Error("网络连接中断，正在重试")));
    xhr.send(bytes);
  });
}

function updateUploadDisplay(batch, file, index, fileLoaded, phase) {
  const now = performance.now();
  const elapsed = now - batch.lastSpeedTime;
  if (elapsed >= 250) {
    const delta = batch.networkBytes - batch.lastSpeedBytes;
    const currentSpeed = delta / (elapsed / 1000);
    batch.speed = batch.speed === 0 ? currentSpeed : batch.speed * 0.65 + currentSpeed * 0.35;
    batch.lastSpeedBytes = batch.networkBytes;
    batch.lastSpeedTime = now;
  }
  const currentTotal = file?.size || 0;
  const currentPercent = currentTotal === 0 ? 100 : Math.min(100, Math.round(fileLoaded * 100 / currentTotal));
  const overallLoaded = batch.completedBytes + fileLoaded;
  const overallPercent = batch.totalBytes === 0 ? 100 : Math.min(100, Math.round(overallLoaded * 100 / batch.totalBytes));
  elements.uploadTitle.textContent = phase;
  elements.uploadCounter.textContent = `${index + 1} / ${batch.count}`;
  elements.uploadDetail.textContent = `${file?.name || "文件"} · ${formatBytes(fileLoaded)} / ${formatBytes(currentTotal)}`;
  elements.uploadSpeed.textContent = `${formatBytes(batch.speed)}/s`;
  elements.progress.value = currentPercent;
  elements.overallProgress.value = overallPercent;
  elements.overallText.textContent = `${overallPercent}%`;
}


function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / (1024 ** index);
  return `${value.toFixed(index === 0 || value >= 10 ? 0 : 1)} ${units[index]}`;
}

let toastTimer;
function toast(message, danger = false) {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.style.borderColor = danger ? "var(--danger)" : "var(--border)";
  elements.toast.hidden = false;
  toastTimer = setTimeout(() => { elements.toast.hidden = true; }, 3200);
}

elements.loginButton.addEventListener("click", async () => {
  if (state.principal?.authenticated) {
    try { await api("/api/v1/session", { method: "DELETE" }); } catch { /* 会话可能已经过期 */ }
    state.token = "";
    sessionStorage.removeItem("chfs_token");
    await refreshSession();
    await loadFiles();
  } else elements.loginDialog.showModal();
});

elements.loginForm.addEventListener("submit", async event => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  elements.loginError.textContent = "";
  try {
    const data = await api("/api/v1/session", {
      method: "POST", json: true,
      body: JSON.stringify({ username: document.querySelector("#usernameInput").value, password: document.querySelector("#passwordInput").value }),
    });
    state.token = data.token;
    sessionStorage.setItem("chfs_token", state.token);
    state.principal = data.principal;
    elements.loginDialog.close();
    elements.loginForm.reset();
    await refreshSession();
    await loadFiles();
  } catch (error) { elements.loginError.textContent = error.message; }
});

document.querySelector("#newFolderButton").addEventListener("click", () => elements.folderDialog.showModal());
elements.folderForm.addEventListener("submit", async event => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  const name = document.querySelector("#folderNameInput").value.trim();
  elements.folderError.textContent = "";
  if (!name || name.includes("/") || name.includes("\\")) { elements.folderError.textContent = "名称不能为空，也不能包含斜杠。"; return; }
  try {
    await api("/api/v1/directories", { method: "POST", json: true, body: JSON.stringify({ path: joinPath(name) }) });
    elements.folderDialog.close(); elements.folderForm.reset(); toast("文件夹已创建"); await loadFiles();
  } catch (error) { elements.folderError.textContent = error.message; }
});

document.querySelector("#uploadButton").addEventListener("click", () => elements.filePicker.click());
elements.filePicker.addEventListener("change", () => { if (elements.filePicker.files.length) uploadFiles([...elements.filePicker.files]); elements.filePicker.value = ""; });
document.querySelector("#refreshButton").addEventListener("click", loadFiles);
for (const name of ["dragenter", "dragover"]) elements.dropZone.addEventListener(name, event => { event.preventDefault(); elements.dropZone.classList.add("dragging"); });
for (const name of ["dragleave", "drop"]) elements.dropZone.addEventListener(name, event => { event.preventDefault(); elements.dropZone.classList.remove("dragging"); });
elements.dropZone.addEventListener("drop", event => { if (event.dataTransfer.files.length) uploadFiles([...event.dataTransfer.files]); });

(async function start() {
  try { await refreshSession(); await loadFiles(); }
  catch (error) { document.querySelector("#connectionState").textContent = "服务不可用"; toast(error.message, true); }
})();
