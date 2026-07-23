"use strict";

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
  const type = createPixelIcon(entry.type);
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

function createPixelIcon(type) {
  const canvas = document.createElement("canvas");
  canvas.className = "file-type";
  canvas.width = 16;
  canvas.height = 16;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", type === "directory" ? "文件夹" : "文件");
  const context = canvas.getContext("2d");
  context.imageSmoothingEnabled = false;
  context.clearRect(0, 0, 16, 16);
  if (type === "directory") {
    context.fillStyle = "#5eead4";
    context.fillRect(1, 4, 14, 10);
    context.fillRect(2, 2, 6, 3);
    context.fillStyle = "#0f766e";
    context.fillRect(2, 7, 12, 6);
    context.fillStyle = "#99f6e4";
    context.fillRect(3, 5, 10, 2);
  } else {
    context.fillStyle = "#cbd5e1";
    context.fillRect(3, 1, 9, 14);
    context.fillRect(12, 4, 2, 11);
    context.fillStyle = "#64748b";
    context.fillRect(12, 3, 1, 1);
    context.fillRect(11, 2, 1, 2);
    context.fillStyle = "#2dd4bf";
    context.fillRect(5, 7, 6, 1);
    context.fillRect(5, 10, 7, 1);
    context.fillRect(5, 13, 5, 1);
  }
  return canvas;
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
  // 上传前再次检查当前身份是否拥有写权限；没有权限时立即结束，不创建上传会话。
  if (!can("write")) { toast(state.authenticationAvailable ? "当前身份没有上传权限，请先登录" : "服务端未开放上传权限", true); return; }
  // batch 保存“这一批文件”的统计状态，供进度条和实时速度计算共同使用。
  const batch = {
    // 本批次包含的文件数量。
    count: files.length,
    // 把每个 File 对象的字节数累加，得到整个批次的总字节数。
    totalBytes: files.reduce((total, file) => total + file.size, 0),
    // 已经完整上传完毕的文件所占字节数；当前文件的进度不放在这里。
    completedBytes: 0,
    // 浏览器实际送入网络层的累计字节数，重试产生的流量也会被统计。
    networkBytes: 0,
    // 上一次计算速度时记录的网络累计字节数。
    lastSpeedBytes: 0,
    // performance.now() 使用单调时钟，适合计算两次刷新之间的耗时。
    lastSpeedTime: performance.now(),
    // 最近一次计算得到的上传速度，单位为字节/秒。
    speed: 0,
  };
  // 显示底部上传状态面板。
  elements.tray.hidden = false;
  // 逐个遍历用户选择的文件；这里的 await 使多个文件也是串行上传。
  for (const [index, file] of files.entries()) {
    // 根据当前浏览目录和文件名生成服务端目标路径。
    const path = joinPath(file.name);
    // 等待当前文件上传完成后才会进入下一个文件。
    try { await uploadOne(file, path, batch, index); }
    // 任一文件失败就提示错误、隐藏面板并终止整个批次。
    catch (error) { toast(`${file.name}：${error.message}`, true); elements.tray.hidden = true; return; }
    // 当前文件完整提交后，才把它计入批次已完成字节数。
    batch.completedBytes += file.size;
  }
  // 用最后一个文件刷新一次界面，确保进度显示为“上传完成”。
  updateUploadDisplay(batch, files.at(-1), files.length - 1, files.at(-1)?.size || 0, "上传完成");
  // 保留完成状态 650 毫秒，避免面板瞬间消失而看不清结果。
  await new Promise(resolve => setTimeout(resolve, 650));
  // 隐藏上传状态面板。
  elements.tray.hidden = true;
  // 弹出整个批次的成功提示。
  toast(`已上传 ${files.length} 个文件`);
  // 重新读取服务端目录，让新上传的文件出现在文件列表中。
  await loadFiles();
}

async function uploadOne(file, path, batch, index) {
  // 初始化当前文件的上传状态显示。
  updateUploadDisplay(batch, file, index, 0, "准备上传");

  // 路径、大小和最后修改时间共同标识一个待续传文件。
  const storageKey = `chfs-resume:${path}:${file.size}:${file.lastModified}`;
  // 从浏览器本地存储读取上次生成的续传标识。
  let resumeKey = localStorage.getItem(storageKey);
  // 第一次上传该文件时，还不存在续传标识。
  if (!resumeKey) {
    // 优先使用安全的随机 UUID；旧浏览器没有该接口时退化为时间戳加随机字符串。
    resumeKey = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    // 持久化续传标识，页面刷新后仍能向服务端找回同一个上传会话。
    localStorage.setItem(storageKey, resumeKey);
  }
  // 创建上传会话；若 resumeKey 已存在，服务端可能返回已有会话及其当前偏移。
  let session = await api("/api/v1/uploads", {
    // POST 表示创建或恢复一个上传事务。
    method: "POST",
    // 告诉 api() 请求体和响应体都按 JSON 处理。
    json: true,
    // 声明目标路径、文件总大小、续传标识，并禁止静默覆盖同名文件。
    body: JSON.stringify({ path, size: file.size, resume_key: resumeKey, overwrite: false }),
  });
  // 分块大小由服务端决定，当前默认值是 16 MiB。
  const chunkSize = session.chunk_size;
  // 默认快速模式不重复读取并哈希已经上传的前缀。
  // offset 大于零说明服务端临时文件中已经保存了一部分数据。
  if (session.offset > 0) {
    // 让界面从服务端确认的断点位置继续显示进度。
    updateUploadDisplay(batch, file, index, session.offset, "从断点继续");
  }

  // 只在还有未上传内容时读取首个分块；文件已经完整上传时直接进入提交阶段。
  let prepared = session.offset < file.size ? await prepareUploadChunk(file, session.offset, chunkSize) : null;
  // prepared 为 null 表示没有更多分块需要发送。
  while (prepared) {
    // 在当前分块传输期间并行预读下一分块，减少磁盘等待。
    // 当前分块不是最后一块时，立即启动下一块的读取任务。
    const nextPromise = prepared.end < file.size
      // Promise 现在开始读取下一块，但下面并不会同时发送它。
      ? prepareUploadChunk(file, prepared.end, chunkSize)
      // 当前已经是最后一块，用一个立即完成的 Promise 统一后续控制流。
      : Promise.resolve(null);
    // 分块真正发出前，把界面进度定位到该分块的起始偏移。
    updateUploadDisplay(batch, file, index, prepared.position, "准备上传");
    // 等待当前 PATCH 请求完整结束；因此同一时刻只有一个分块请求在传输。
    session = await sendChunkWithRetry(
      // 服务端上传会话的唯一标识。
      session.upload_id,
      // 当前分块在完整文件中的起始字节位置。
      prepared.position,
      // 已读入内存的 ArrayBuffer；这就是 XHR 的请求体。
      prepared.bytes,
      // 传入文件总大小；当前 sendChunk() 尚未实际使用这个参数。
      file.size,
      // XHR 每次报告上传进度时都会调用此回调。
      (loaded, networkDelta) => {
        // 累加从上一次进度事件到本次事件新增的网络字节数。
        batch.networkBytes += networkDelta;
        // “分块起点 + 分块内已发送量”就是当前文件的可视进度。
        updateUploadDisplay(batch, file, index, prepared.position + loaded, "正在上传");
      },
    );
    // PATCH 成功后，以服务端返回的 offset 为准更新界面。
    updateUploadDisplay(batch, file, index, session.offset, "分块已写入");
    // 等待预读任务完成，并把下一分块设为新的当前分块。
    prepared = await nextPromise;
  }
  // 所有字节已到达临时文件，接下来要求服务端持久化并原子发布目标文件。
  updateUploadDisplay(batch, file, index, file.size, "正在原子提交");
  // 调用完成接口；它与“传分块”是两个不同的 HTTP 请求阶段。
  const completed = await api(`/api/v1/uploads/${encode(session.upload_id)}/complete`, {
    // POST 表示执行上传事务的最终提交动作。
    method: "POST",
    // 完成接口使用 JSON 格式。
    json: true,
    // 快速模式不发送分块清单哈希，所以请求体是空对象。
    body: JSON.stringify({}),
  });
  // 成功提交后删除续传标识，避免以后误恢复一个已经结束的会话。
  localStorage.removeItem(storageKey);
  // 显示服务端计算的完整文件 SHA-256 的前 12 个十六进制字符。
  elements.uploadDetail.textContent = `SHA-256 ${completed.sha256.slice(0, 12)}…`;
  // 强制把当前文件进度条设置为 100%。
  elements.progress.value = 100;
}

async function prepareUploadChunk(file, position, chunkSize) {
  // 计算分块结束位置；最后一块不足 chunkSize 时不能越过文件末尾。
  const end = Math.min(position + chunkSize, file.size);
  // slice() 创建 Blob 视图，arrayBuffer() 再把这一段完整复制/读取到连续内存。
  const bytes = await file.slice(position, end).arrayBuffer();
  // 同时返回起点、终点和内存数据，发送循环需要用它们推进偏移。
  return { position, end, bytes };
}

async function sendChunkWithRetry(uploadId, offset, bytes, totalSize, onProgress) {
  // 保存最后一次异常；三次都失败后把它抛给上层。
  let lastError;
  // 当前策略最多尝试三次，并且每次都重传整个分块。
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    // sendChunk 成功时立即返回服务端的新会话状态。
    try { return await sendChunk(uploadId, offset, bytes, totalSize, onProgress); }
    // 网络错误或非 2xx 响应都会进入这里。
    catch (error) {
      // 覆盖保存最新错误，使最终提示反映最后一次失败。
      lastError = error;
      // 前两次失败后分别等待 350 ms、700 ms；第三次失败不再等待。
      if (attempt < 3) await new Promise(resolve => setTimeout(resolve, 350 * attempt));
    }
  }
  // 三次尝试均失败，把最后一次错误交给 uploadFiles() 统一显示。
  throw lastError;
}

function sendChunk(uploadId, offset, bytes, totalSize, onProgress) {
  // XMLHttpRequest 是事件式 API，这里用 Promise 包装成可 await 的形式。
  return new Promise((resolve, reject) => {
    // 记录上一次 progress 事件的累计值，用来计算本次新增流量。
    let lastLoaded = 0;
    // 每个分块创建一个新的 XHR（XMLHttpRequest）对象和一个 PATCH 请求。
    const xhr = new XMLHttpRequest();
    // URL 中同时携带上传会话 ID 和当前分块在文件中的偏移。
    xhr.open("PATCH", `/api/v1/uploads/${encode(uploadId)}?offset=${offset}`);
    // 登录状态下附加 Bearer Token，供服务端鉴权。
    if (state.token) xhr.setRequestHeader("Authorization", `Bearer ${state.token}`);
    // 监听“请求体上传到网络层”的进度；它不等同于服务端已经写盘。
    xhr.upload.addEventListener("progress", event => {
      // 只有浏览器能确定请求体总长度时，进度数值才具有可比性。
      if (event.lengthComputable) {
        // event.loaded 是累计值，减去旧值才是本次事件新增的字节数。
        const delta = Math.max(0, event.loaded - lastLoaded);
        // 保存当前累计值，供下一次 progress 事件计算差值。
        lastLoaded = event.loaded;
        // 把分块内累计量和本次增量交给界面统计逻辑。
        onProgress(event.loaded, delta);
      }
    });
    // HTTP 响应完整到达时触发 load；HTTP 4xx/5xx 也会进入该事件。
    xhr.addEventListener("load", () => {
      // 任何 2xx 状态都视为成功，并解析服务端返回的 JSON 会话状态。
      if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
      // 非 2xx 状态需要转成 rejected Promise。
      else {
        // 优先读取服务端结构化错误消息。
        try { reject(new Error(JSON.parse(xhr.responseText).error.message)); }
        // 响应不是预期 JSON 时，退化为显示 HTTP 状态码。
        catch { reject(new Error(`分块上传失败（${xhr.status}）`)); }
      }
    });
    // DNS、断网等传输层错误没有正常 HTTP 响应，会触发 error。
    xhr.addEventListener("error", () => reject(new Error("网络连接中断，正在重试")));
    // 真正开始发送 ArrayBuffer；调用后浏览器才把该分块送入网络栈。
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
