"use strict";

// 浏览器端只负责交互和协议适配；路径验证、权限和磁盘操作始终由服务端内核执行。
const state = {
  path: "",
  space: "shared",
  token: sessionStorage.getItem("chfs_token") || "",
  principal: null,
  authenticationAvailable: false,
  sharedTextRevision: -1,
  sharedTextDirty: false,
  sharedTextEditVersion: 0,
  computerAccess: false,
  computerHome: null,
  computerQuickAccess: [],
  selectedPaths: new Set(),
  entriesByPath: new Map(),
  currentEntries: [],
  protectedRootEntries: false,
  sortMode: "modified-desc",
  fileClipboard: null,
};

const elements = {
  folderPicker: document.querySelector("#folderPicker"),
  uploadFolderButton: document.querySelector("#uploadFolderButton"),
  rows: document.querySelector("#fileRows"),
  empty: document.querySelector("#emptyState"),
  loading: document.querySelector("#loadingState"),
  count: document.querySelector("#itemCount"),
  sortSelect: document.querySelector("#sortSelect"),
  breadcrumbs: document.querySelector("#breadcrumbs"),
  loginButton: document.querySelector("#loginButton"),
  loginDialog: document.querySelector("#loginDialog"),
  loginForm: document.querySelector("#loginForm"),
  loginError: document.querySelector("#loginError"),
  folderDialog: document.querySelector("#folderDialog"),
  folderForm: document.querySelector("#folderForm"),
  folderError: document.querySelector("#folderError"),
  uploadConflictDialog: document.querySelector("#uploadConflictDialog"),
  uploadConflictPath: document.querySelector("#uploadConflictPath"),
  applyConflictChoice: document.querySelector("#applyConflictChoice"),
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
  sharedText: document.querySelector("#sharedText"),
  sharedTextStatus: document.querySelector("#sharedTextStatus"),
  sharedTextCount: document.querySelector("#sharedTextCount"),
  refreshTextButton: document.querySelector("#refreshTextButton"),
  copyTextButton: document.querySelector("#copyTextButton"),
  pasteTextButton: document.querySelector("#pasteTextButton"),
  clearTextButton: document.querySelector("#clearTextButton"),
  sharedSpaceButton: document.querySelector("#sharedSpaceButton"),
  computerSpaceButton: document.querySelector("#computerSpaceButton"),
  homeButton: document.querySelector("#homeButton"),
  quickAccess: document.querySelector("#quickAccess"),
  quickAccessButtons: document.querySelector("#quickAccessButtons"),
  parentButton: document.querySelector("#parentButton"),
  downloadFilesButton: document.querySelector("#downloadFilesButton"),
  copyFilesButton: document.querySelector("#copyFilesButton"),
  cutFilesButton: document.querySelector("#cutFilesButton"),
  pasteFilesButton: document.querySelector("#pasteFilesButton"),
  deleteFilesButton: document.querySelector("#deleteFilesButton"),
  selectAllFiles: document.querySelector("#selectAllFiles"),
};
let sharedTextSaveTimer;

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
    error.status = response.status;
    throw error;
  }
  return payload;
}

function joinPath(name) { return state.path ? `${state.path}/${name}` : name; }
function joinPathAt(basePath, name) { return basePath ? `${basePath}/${name}` : name; }
function encode(value) { return encodeURIComponent(value); }

async function refreshSession() {
  try {
    const data = await api("/api/v1/session");
    state.principal = data.principal;
    state.authenticationAvailable = data.authentication_available;
    state.computerAccess = Boolean(data.computer_access);
    state.computerHome = data.computer_home || null;
    state.computerQuickAccess = Array.isArray(data.computer_quick_access) ? data.computer_quick_access : [];
  } catch (error) {
    if (error.code === "authentication_failed") {
      state.token = "";
      sessionStorage.removeItem("chfs_token");
      const guestData = await api("/api/v1/session");
      state.principal = guestData.principal;
      state.authenticationAvailable = guestData.authentication_available;
      state.computerAccess = Boolean(guestData.computer_access);
      state.computerHome = guestData.computer_home || null;
      state.computerQuickAccess = Array.isArray(guestData.computer_quick_access) ? guestData.computer_quick_access : [];
    } else throw error;
  }
  elements.loginButton.textContent = state.principal.authenticated ? `${state.principal.name} · 退出` : "登录";
  elements.loginButton.hidden = !state.authenticationAvailable && !state.principal.authenticated;
  elements.computerSpaceButton.hidden = !state.computerAccess;
  elements.homeButton.hidden = !state.computerAccess || !state.computerHome;
  renderQuickAccess();
  if (!state.computerAccess && state.space === "computer") {
    state.space = "shared";
    state.path = "";
  }
  updateLocationControls();
  updatePermissionControls();
}

function can(permission) {
  const permissions = state.principal?.permissions || [];
  return permissions.includes("admin") || permissions.includes(permission);
}

function updatePermissionControls() {
  const mayWrite = can("write");
  const mayRead = can("read");
  elements.sharedText.disabled = !mayRead;
  elements.sharedText.readOnly = !mayWrite;
  elements.refreshTextButton.disabled = !mayRead;
  elements.copyTextButton.disabled = !mayRead;
  elements.pasteTextButton.disabled = !mayWrite;
  elements.clearTextButton.disabled = !mayWrite;
  updateLocationControls();
  updateSelectionControls();
  if (!mayRead) {
    elements.sharedText.value = "";
    elements.sharedTextStatus.textContent = "当前身份没有读取权限";
    state.sharedTextRevision = -1;
    state.sharedTextDirty = false;
    updateSharedTextCount();
  }
  const hint = mayWrite ? "" : (state.authenticationAvailable ? "登录具有写入权限的账户后使用" : "服务端未开放写入权限");
  elements.uploadButton.title = hint;
  elements.uploadFolderButton.title = hint;
  elements.newFolderButton.title = hint;
  elements.pasteTextButton.title = hint;
  elements.clearTextButton.title = hint;
}

function updateSharedTextCount() {
  const bytes = new TextEncoder().encode(elements.sharedText.value).byteLength;
  elements.sharedTextCount.textContent = `${elements.sharedText.value.length} 字符 · ${formatBytes(bytes)}`;
}

function presentSharedTextStatus(data, prefix = "已同步") {
  const updated = data.updated_at ? new Date(data.updated_at).toLocaleString() : "尚未保存";
  elements.sharedTextStatus.textContent = `${prefix} · ${updated} · 版本 ${data.revision}`;
}

async function loadSharedText(force = false) {
  if (!can("read") || (state.sharedTextDirty && !force)) return;
  try {
    const data = await api("/api/v1/shared-text");
    if (force || !state.sharedTextDirty) {
      if (force) clearTimeout(sharedTextSaveTimer);
      if (force || data.revision !== state.sharedTextRevision) elements.sharedText.value = data.text;
      state.sharedTextRevision = data.revision;
      state.sharedTextDirty = false;
      updateSharedTextCount();
      presentSharedTextStatus(data);
    }
  } catch (error) {
    elements.sharedTextStatus.textContent = error.message;
  }
}

function scheduleSharedTextSave(delay = 600) {
  if (!can("write")) return;
  clearTimeout(sharedTextSaveTimer);
  sharedTextSaveTimer = setTimeout(saveSharedText, delay);
}

async function saveSharedText(showToast = false) {
  if (!can("write")) return;
  clearTimeout(sharedTextSaveTimer);
  const editVersion = state.sharedTextEditVersion;
  const text = elements.sharedText.value;
  elements.sharedTextStatus.textContent = "正在保存…";
  try {
    const data = await api("/api/v1/shared-text", {
      method: "PUT",
      json: true,
      body: JSON.stringify({ text }),
    });
    state.sharedTextRevision = data.revision;
    if (state.sharedTextEditVersion === editVersion) {
      state.sharedTextDirty = false;
      presentSharedTextStatus(data, "自动保存");
    } else {
      scheduleSharedTextSave();
    }
    if (showToast) toast("共享文本已同步");
  } catch (error) {
    elements.sharedTextStatus.textContent = error.message;
    toast(error.message, true);
  }
}

async function copySharedText() {
  const text = elements.sharedText.value;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    elements.sharedText.focus();
    elements.sharedText.select();
    if (!document.execCommand("copy")) {
      toast("浏览器禁止自动复制，请长按文本手动复制", true);
      return;
    }
  }
  toast("文本已复制");
}

async function pasteSharedText() {
  try {
    const text = await navigator.clipboard.readText();
    const start = elements.sharedText.selectionStart;
    const end = elements.sharedText.selectionEnd;
    elements.sharedText.setRangeText(text, start, end, "end");
    markSharedTextDirty();
    elements.sharedText.focus();
  } catch {
    elements.sharedText.focus();
    toast("浏览器不允许直接读取剪贴板，请长按文本框选择“粘贴”", true);
  }
}

function clearSharedText() {
  if (!window.confirm("确定清空共享文本吗？其他设备也会同步清空。")) return;
  elements.sharedText.value = "";
  markSharedTextDirty(0);
  elements.sharedText.focus();
}

function markSharedTextDirty(delay = 600) {
  state.sharedTextDirty = true;
  state.sharedTextEditVersion += 1;
  elements.sharedTextStatus.textContent = "正在等待自动保存…";
  updateSharedTextCount();
  scheduleSharedTextSave(delay);
}

async function loadFiles() {
  elements.loading.hidden = false;
  elements.empty.hidden = true;
  elements.rows.replaceChildren();
  state.selectedPaths.clear();
  state.entriesByPath.clear();
  state.currentEntries = [];
  state.protectedRootEntries = false;
  elements.selectAllFiles.checked = false;
  elements.selectAllFiles.indeterminate = false;
  updateSelectionControls();
  renderBreadcrumbs();
  try {
    const data = await api(`/api/v1/files?space=${encode(state.space)}&path=${encode(state.path)}`);
    elements.count.textContent = `${data.entries.length} 项`;
    elements.loading.hidden = true;
    elements.empty.hidden = data.entries.length !== 0;
    const protectEntries = isComputerRoot();
    state.currentEntries = data.entries;
    state.protectedRootEntries = protectEntries;
    for (const entry of data.entries) {
      // “此电脑”根层列出的是磁盘入口，不是可复制、移动或删除的普通目录。
      if (!protectEntries) state.entriesByPath.set(entry.path, entry);
    }
    renderSortedEntries();
    updateSelectionControls();
  } catch (error) {
    elements.loading.textContent = error.message;
    elements.count.textContent = "无法读取";
    if (error.code === "permission_denied" || error.code === "authentication_failed") elements.loginDialog.showModal();
  }
}

function renderSortedEntries() {
  elements.rows.replaceChildren();
  const entries = sortEntries(state.currentEntries, state.sortMode);
  for (const entry of entries) {
    elements.rows.append(createRow(entry, state.protectedRootEntries));
  }
}

function sortEntries(entries, mode) {
  const direction = mode.endsWith("-desc") ? -1 : 1;
  const collator = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });
  return [...entries].sort((left, right) => {
    if (mode.startsWith("type-")) {
      // 与资源管理器一致：文件夹始终排在文件前；文件按扩展名分组。
      if (left.type !== right.type) return left.type === "directory" ? -1 : 1;
      const typeComparison = collator.compare(fileTypeKey(left), fileTypeKey(right));
      if (typeComparison !== 0) return typeComparison * direction;
    } else {
      const timeComparison = Number(left.modified_ns) - Number(right.modified_ns);
      if (timeComparison !== 0) return timeComparison * direction;
    }
    return collator.compare(left.name, right.name);
  });
}

function fileTypeKey(entry) {
  if (entry.type === "directory") return "";
  const dotIndex = entry.name.lastIndexOf(".");
  return dotIndex > 0 && dotIndex < entry.name.length - 1
    ? entry.name.slice(dotIndex + 1).toLocaleLowerCase("zh-CN")
    : "";
}

function createRow(entry, protectedRoot = false) {
  const row = document.createElement("tr");
  const selectionCell = document.createElement("td");
  selectionCell.className = "selection-column";
  const selection = document.createElement("input");
  selection.type = "checkbox";
  selection.checked = state.selectedPaths.has(entry.path);
  selection.setAttribute("aria-label", `选择 ${entry.name}`);
  selection.disabled = protectedRoot;
  selection.addEventListener("change", () => {
    if (selection.checked) state.selectedPaths.add(entry.path);
    else state.selectedPaths.delete(entry.path);
    updateSelectionControls();
  });
  selectionCell.append(selection);
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
  if (!protectedRoot) actions.append(actionButton("下载", () => downloadEntry(entry)));
  if (can("delete") && !protectedRoot) actions.append(actionButton("删除", () => removeEntry(entry), true));
  actionsCell.append(actions);
  row.append(selectionCell, nameCell, size, modified, actionsCell);
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
  updateLocationControls();
  elements.breadcrumbs.replaceChildren();
  const parts = state.path ? state.path.split("/") : [];
  const root = breadcrumb(state.space === "computer" ? "此电脑" : "共享目录", "");
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

function switchSpace(space, path = "") {
  if (space === "computer" && !state.computerAccess) return;
  state.space = space;
  state.path = path;
  updateLocationControls();
  loadFiles();
}

function renderQuickAccess() {
  elements.quickAccessButtons.replaceChildren();
  state.computerQuickAccess.forEach((entry) => {
    if (!entry || typeof entry.label !== "string" || typeof entry.path !== "string") return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "location-button quick-access-button";
    button.textContent = entry.label;
    button.dataset.path = entry.path;
    button.addEventListener("click", () => switchSpace("computer", entry.path));
    elements.quickAccessButtons.append(button);
  });
  elements.quickAccess.hidden = !state.computerAccess || elements.quickAccessButtons.childElementCount === 0;
}

function updateLocationControls() {
  elements.sharedSpaceButton.classList.toggle("active", state.space === "shared");
  elements.computerSpaceButton.classList.toggle(
    "active",
    state.space === "computer" && state.path !== state.computerHome,
  );
  elements.homeButton.classList.toggle(
    "active",
    state.space === "computer" && Boolean(state.computerHome) && state.path === state.computerHome,
  );
  elements.quickAccessButtons.querySelectorAll(".quick-access-button").forEach((button) => {
    button.classList.toggle("active", state.space === "computer" && button.dataset.path === state.path);
  });
  elements.parentButton.disabled = !state.path;
  const mayWriteHere = can("write") && !isComputerRoot();
  elements.uploadButton.disabled = !mayWriteHere;
  elements.uploadFolderButton.disabled = !mayWriteHere;
  elements.newFolderButton.disabled = !mayWriteHere;
}

function isComputerRoot() {
  return state.space === "computer" && !state.path;
}

function navigateParent() {
  if (!state.path) return;
  const parts = state.path.split("/");
  parts.pop();
  navigate(parts.join("/"));
}

function download(path) {
  // GET 下载可使用登录时签发的窄路径 HttpOnly Cookie，不把令牌暴露在 URL 中。
  const anchor = document.createElement("a");
  anchor.href = `/api/v1/content?space=${encode(state.space)}&path=${encode(path)}`;
  anchor.download = "";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}

function triggerDownload(url) {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}

function downloadArchive(paths) {
  const query = new URLSearchParams({ space: state.space });
  for (const path of paths) query.append("path", path);
  triggerDownload(`/api/v1/archive?${query.toString()}`);
}

function downloadEntry(entry) {
  if (entry.type === "file") download(entry.path);
  else downloadArchive([entry.path]);
}

function downloadSelected() {
  const entries = [...state.selectedPaths]
    .map(path => state.entriesByPath.get(path))
    .filter(Boolean);
  if (!entries.length) return;
  if (entries.length === 1 && entries[0].type === "file") download(entries[0].path);
  else downloadArchive(entries.map(entry => entry.path));
}

async function removeEntry(entry) {
  const description = entry.type === "directory" ? "文件夹及其中所有内容" : "文件";
  if (!window.confirm(`确定删除${description}“${entry.name}”吗？此操作无法撤销。`)) return;
  try {
    await api(`/api/v1/files?space=${encode(state.space)}&path=${encode(entry.path)}&recursive=${entry.type === "directory"}`, { method: "DELETE" });
    toast("已删除");
    await loadFiles();
  } catch (error) { toast(error.message, true); }
}

function updateSelectionControls() {
  const selectedCount = state.selectedPaths.size;
  const totalCount = state.entriesByPath.size;
  const mayWrite = can("write");
  elements.downloadFilesButton.disabled = selectedCount === 0 || !can("read");
  elements.copyFilesButton.disabled = selectedCount === 0 || !can("read");
  elements.cutFilesButton.disabled = selectedCount === 0 || !mayWrite || !can("delete");
  elements.deleteFilesButton.disabled = selectedCount === 0 || !can("delete");
  elements.pasteFilesButton.disabled = !state.fileClipboard || !mayWrite || state.fileClipboard.space !== state.space;
  elements.selectAllFiles.checked = totalCount > 0 && selectedCount === totalCount;
  elements.selectAllFiles.indeterminate = selectedCount > 0 && selectedCount < totalCount;
  elements.selectAllFiles.disabled = totalCount === 0;
}

function setFileClipboard(operation) {
  if (!state.selectedPaths.size) return;
  state.fileClipboard = {
    operation,
    space: state.space,
    paths: [...state.selectedPaths],
  };
  toast(`${operation === "move" ? "已剪切" : "已复制"} ${state.selectedPaths.size} 项，请进入目标目录后粘贴`);
  updateSelectionControls();
}

async function pasteFiles() {
  const clipboard = state.fileClipboard;
  if (!clipboard) return;
  if (clipboard.space !== state.space) {
    toast("暂不支持跨共享目录与此电脑复制", true);
    return;
  }
  try {
    await api("/api/v1/file-operations", {
      method: "POST",
      json: true,
      body: JSON.stringify({
        space: state.space,
        operation: clipboard.operation,
        sources: clipboard.paths,
        destination: state.path,
      }),
    });
    if (clipboard.operation === "move") state.fileClipboard = null;
    toast(clipboard.operation === "move" ? "移动完成" : "复制完成");
    await loadFiles();
  } catch (error) {
    toast(error.message, true);
  }
  updateSelectionControls();
}

async function deleteSelectedFiles() {
  const entries = [...state.selectedPaths]
    .map(path => state.entriesByPath.get(path))
    .filter(Boolean);
  if (!entries.length) return;
  if (!window.confirm(`确定删除选中的 ${entries.length} 项吗？文件夹将连同其中内容删除，此操作无法撤销。`)) return;
  try {
    for (const entry of entries) {
      await api(
        `/api/v1/files?space=${encode(state.space)}&path=${encode(entry.path)}&recursive=${entry.type === "directory"}`,
        { method: "DELETE" },
      );
    }
    toast(`已删除 ${entries.length} 项`);
    await loadFiles();
  } catch (error) {
    toast(error.message, true);
    await loadFiles();
  }
}

async function uploadFiles(
  files,
  pathForFile = file => file.name,
  context = { space: state.space, path: state.path },
) {
  // 上传前再次检查当前身份是否拥有写权限；没有权限时立即结束，不创建上传会话。
  if (!can("write")) { toast(state.authenticationAvailable ? "当前身份没有上传权限，请先登录" : "服务端未开放上传权限", true); return; }
  if (context.space === "computer" && !context.path) { toast("请先进入一个磁盘或目录再上传", true); return; }
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
    // 高频 progress 事件只累计字节；界面最多约每 100 ms 重绘一次。
    lastRenderTime: 0,
    // 最近一次计算得到的上传速度，单位为字节/秒。
    speed: 0,
  };
  // 显示底部上传状态面板。
  elements.tray.hidden = false;
  // 仅在用户勾选“后续同名文件也这样处理”后保存批次级策略。
  let conflictPolicy = null;
  let uploadedCount = 0;
  let skippedCount = 0;
  // 逐个遍历用户选择的文件；这里的 await 使多个文件也是串行上传。
  for (const [index, file] of files.entries()) {
    // 普通上传使用文件名；文件夹上传传入带目录层级的相对路径。
    const path = joinPathAt(context.path, pathForFile(file));
    let overwrite = false;
    let finished = false;
    while (!finished) {
      try {
        // 等待当前文件上传完成后才会进入下一个文件。
        await uploadOne(file, path, batch, index, overwrite, context.space);
        uploadedCount += 1;
        finished = true;
      } catch (error) {
        // 只有明确的“目标文件已存在”才进入覆盖/跳过流程；其他冲突仍按错误处理。
        if (!overwrite && isExistingFileConflict(error)) {
          const choice = conflictPolicy
            ? { action: conflictPolicy, applyToRemaining: true }
            : await chooseUploadConflict(path);
          if (choice.applyToRemaining) conflictPolicy = choice.action;
          if (choice.action === "skip") {
            skippedCount += 1;
            updateUploadDisplay(batch, file, index, file.size, "已跳过同名文件");
            finished = true;
          } else {
            overwrite = true;
          }
          continue;
        }
        toast(`${file.name}：${error.message}`, true);
        elements.tray.hidden = true;
        await loadFiles();
        return;
      }
    }
    // 上传成功或主动跳过都表示这个文件已经处理完，可继续推进批次总进度。
    batch.completedBytes += file.size;
  }
  // 用最后一个文件刷新一次界面，确保进度显示为“上传完成”。
  updateUploadDisplay(batch, files.at(-1), files.length - 1, files.at(-1)?.size || 0, "上传完成");
  // 保留完成状态 650 毫秒，避免面板瞬间消失而看不清结果。
  await new Promise(resolve => setTimeout(resolve, 650));
  // 隐藏上传状态面板。
  elements.tray.hidden = true;
  // 弹出整个批次的成功提示。
  const summary = skippedCount
    ? `已上传 ${uploadedCount} 个，跳过 ${skippedCount} 个`
    : `已上传 ${uploadedCount} 个文件`;
  toast(summary);
  // 重新读取服务端目录，让新上传的文件出现在文件列表中。
  await loadFiles();
}

function isExistingFileConflict(error) {
  return error?.status === 409 && error?.code === "conflict" && error?.message === "目标文件已存在";
}

function chooseUploadConflict(path) {
  return new Promise(resolve => {
    const dialog = elements.uploadConflictDialog;
    elements.uploadConflictPath.textContent = `“${path}”已经存在，要覆盖还是跳过？`;
    elements.applyConflictChoice.checked = false;
    dialog.returnValue = "skip";
    dialog.addEventListener("close", () => {
      resolve({
        action: dialog.returnValue === "overwrite" ? "overwrite" : "skip",
        applyToRemaining: elements.applyConflictChoice.checked,
      });
    }, { once: true });
    dialog.showModal();
  });
}

function folderRelativePath(file) {
  // webkitRelativePath 由浏览器的文件夹选择器提供，例如“照片/旅行/a.jpg”。
  const rawPath = file.webkitRelativePath;
  if (typeof rawPath !== "string" || !rawPath) throw new Error(`无法读取“${file.name}”的文件夹路径`);

  // 浏览器通常使用正斜杠；同时兼容可能出现的 Windows 反斜杠。
  const parts = rawPath.replaceAll("\\", "/").split("/");
  // 不接受空片段和相对跳转，避免客户端构造出含义不明确的目标路径。
  if (parts.length < 2 || parts.some(part => !part || part === "." || part === "..")) {
    throw new Error(`文件夹路径无效：${rawPath}`);
  }
  return parts.join("/");
}

function folderUploadPlan(files) {
  const paths = new Map();
  const directories = new Set();

  for (const file of files) {
    const relativePath = folderRelativePath(file);
    paths.set(file, relativePath);
    const parts = relativePath.split("/");

    // 收集从根文件夹到文件父目录的每一级路径，并用 Set 自动去重。
    for (let depth = 1; depth < parts.length; depth += 1) {
      directories.add(parts.slice(0, depth).join("/"));
    }
  }

  // 父目录必须先于子目录创建；同一深度按名称排序，使执行顺序稳定可复现。
  const orderedDirectories = [...directories].sort((left, right) => {
    const depthDifference = left.split("/").length - right.split("/").length;
    return depthDifference || left.localeCompare(right, "zh-CN");
  });
  return { paths, directories: orderedDirectories };
}

async function uploadFolder(files) {
  if (!can("write")) {
    toast(state.authenticationAvailable ? "当前身份没有上传权限，请先登录" : "服务端未开放上传权限", true);
    return;
  }
  if (isComputerRoot()) {
    toast("请先进入一个磁盘或目录再上传", true);
    return;
  }

  try {
    const context = { space: state.space, path: state.path };
    // 在修改服务端状态前先验证全部文件路径，避免无效文件夹留下半成品目录。
    const plan = folderUploadPlan(files);
    for (const directory of plan.directories) {
      try {
        await api("/api/v1/directories", {
          method: "POST",
          json: true,
          body: JSON.stringify({ space: context.space, path: joinPathAt(context.path, directory) }),
        });
      } catch (error) {
        // 文件夹上传允许合并进已有目录；若冲突原因不是目录已存在，仍应停止。
        if (!(error?.status === 409 && error?.code === "conflict" && error?.message === "目标已存在")) throw error;
      }
    }
    await uploadFiles(files, file => plan.paths.get(file), context);
  } catch (error) {
    toast(`上传文件夹失败：${error.message}`, true);
    await loadFiles();
  }
}

async function uploadOne(file, path, batch, index, overwrite = false, space = "shared") {
  // 初始化当前文件的上传状态显示。
  updateUploadDisplay(batch, file, index, 0, "准备上传");

  // 路径、大小和最后修改时间共同标识一个待续传文件。
  const storageKey = `chfs-resume:${space}:${path}:${file.size}:${file.lastModified}`;
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
    // 默认禁止静默覆盖；只有用户在冲突对话框中明确选择覆盖时才传 true。
    body: JSON.stringify({ space, path, size: file.size, resume_key: resumeKey, overwrite }),
  });
  // 分块大小由服务端决定；大块可减少高速局域网中的请求确认空档。
  const chunkSize = session.chunk_size;
  // 默认快速模式不重复读取并哈希已经上传的前缀。
  // offset 大于零说明服务端临时文件中已经保存了一部分数据。
  if (session.offset > 0) {
    // 让界面从服务端确认的断点位置继续显示进度。
    updateUploadDisplay(batch, file, index, session.offset, "从断点继续");
  }

  // slice() 只创建 Blob 文件视图，XHR 直接从文件向网络层流式读取。
  // 这里不再把整个分块复制到 JavaScript ArrayBuffer。
  let prepared = session.offset < file.size ? prepareUploadChunk(file, session.offset, chunkSize) : null;
  while (prepared) {
    // 分块真正发出前，把界面进度定位到该分块的起始偏移。
    updateUploadDisplay(batch, file, index, prepared.position, "准备上传");
    // 等待当前 PATCH 请求完整结束；因此同一时刻只有一个分块请求在传输。
    session = await sendChunkWithRetry(
      // 服务端上传会话的唯一标识。
      session.upload_id,
      // 当前分块在完整文件中的起始字节位置。
      prepared.position,
      // Blob 直接交给浏览器网络栈，不经过 JavaScript 连续内存副本。
      prepared.body,
      space,
      // 传入文件总大小；当前 sendChunk() 尚未实际使用这个参数。
      file.size,
      // XHR 每次报告上传进度时都会调用此回调。
      (loaded, networkDelta) => {
        // 累加从上一次进度事件到本次事件新增的网络字节数。
        batch.networkBytes += networkDelta;
        // 移动浏览器可能非常频繁地触发 progress。每次更新多个 DOM 节点会与
        // 网络发送竞争主线程，因此只在 100 ms 到期或当前分块结束时重绘。
        const now = performance.now();
        if (now - batch.lastRenderTime >= 100 || loaded >= prepared.body.size) {
          batch.lastRenderTime = now;
          // “分块起点 + 分块内已发送量”就是当前文件的可视进度。
          updateUploadDisplay(batch, file, index, prepared.position + loaded, "正在上传");
        }
      },
    );
    // PATCH 成功后，以服务端返回的 offset 为准更新界面。
    updateUploadDisplay(batch, file, index, session.offset, "分块已写入");
    prepared = session.offset < file.size ? prepareUploadChunk(file, session.offset, chunkSize) : null;
  }
  // 所有字节已到达临时文件，接下来要求服务端持久化并原子发布目标文件。
  updateUploadDisplay(batch, file, index, file.size, "正在原子提交");
  // 调用完成接口；它与“传分块”是两个不同的 HTTP 请求阶段。
  const completed = await api(`/api/v1/uploads/${encode(session.upload_id)}/complete?space=${encode(space)}`, {
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

function prepareUploadChunk(file, position, chunkSize) {
  // 计算分块结束位置；最后一块不足 chunkSize 时不能越过文件末尾。
  const end = Math.min(position + chunkSize, file.size);
  // slice() 创建轻量 Blob 视图；浏览器在 xhr.send() 时按需读取文件内容。
  const body = file.slice(position, end);
  return { position, end, body };
}

async function sendChunkWithRetry(uploadId, offset, body, space, totalSize, onProgress) {
  // 保存最后一次异常；三次都失败后把它抛给上层。
  let lastError;
  // 当前策略最多尝试三次，并且每次都重传整个分块。
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    // sendChunk 成功时立即返回服务端的新会话状态。
    try { return await sendChunk(uploadId, offset, body, space, totalSize, onProgress); }
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

function sendChunk(uploadId, offset, body, space, totalSize, onProgress) {
  // XMLHttpRequest 是事件式 API，这里用 Promise 包装成可 await 的形式。
  return new Promise((resolve, reject) => {
    // 记录上一次 progress 事件的累计值，用来计算本次新增流量。
    let lastLoaded = 0;
    // 每个分块创建一个新的 XHR（XMLHttpRequest）对象和一个 PATCH 请求。
    const xhr = new XMLHttpRequest();
    // URL 中同时携带上传会话 ID 和当前分块在文件中的偏移。
    xhr.open("PATCH", `/api/v1/uploads/${encode(uploadId)}?space=${encode(space)}&offset=${offset}`);
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
    // Blob 由浏览器直接读取并送入网络栈。
    xhr.send(body);
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
    await loadSharedText(true);
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
    await loadSharedText(true);
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
    await api("/api/v1/directories", {
      method: "POST",
      json: true,
      body: JSON.stringify({ space: state.space, path: joinPath(name) }),
    });
    elements.folderDialog.close(); elements.folderForm.reset(); toast("文件夹已创建"); await loadFiles();
  } catch (error) { elements.folderError.textContent = error.message; }
});

elements.uploadFolderButton.addEventListener("click", () => {
  elements.folderPicker.click();
});

elements.folderPicker.addEventListener("change", async () => {
  const files = [...elements.folderPicker.files];
  // 先清空选择器，使用户在失败后仍能立即重新选择同一个文件夹。
  elements.folderPicker.value = "";
  if (files.length) await uploadFolder(files);
});

elements.sharedSpaceButton.addEventListener("click", () => switchSpace("shared"));
elements.computerSpaceButton.addEventListener("click", () => switchSpace("computer"));
elements.homeButton.addEventListener("click", () => switchSpace("computer", state.computerHome || ""));
elements.parentButton.addEventListener("click", navigateParent);
elements.downloadFilesButton.addEventListener("click", downloadSelected);
elements.copyFilesButton.addEventListener("click", () => setFileClipboard("copy"));
elements.cutFilesButton.addEventListener("click", () => setFileClipboard("move"));
elements.pasteFilesButton.addEventListener("click", pasteFiles);
elements.deleteFilesButton.addEventListener("click", deleteSelectedFiles);
elements.selectAllFiles.addEventListener("change", () => {
  state.selectedPaths.clear();
  for (const checkbox of elements.rows.querySelectorAll('input[type="checkbox"]')) {
    if (!checkbox.disabled) checkbox.checked = elements.selectAllFiles.checked;
  }
  if (elements.selectAllFiles.checked) {
    for (const path of state.entriesByPath.keys()) state.selectedPaths.add(path);
  }
  updateSelectionControls();
});
elements.sortSelect.addEventListener("change", () => {
  state.sortMode = elements.sortSelect.value;
  renderSortedEntries();
  updateSelectionControls();
});

document.querySelector("#uploadButton").addEventListener("click", () => elements.filePicker.click());
elements.filePicker.addEventListener("change", () => { if (elements.filePicker.files.length) uploadFiles([...elements.filePicker.files]); elements.filePicker.value = ""; });
document.querySelector("#refreshButton").addEventListener("click", loadFiles);
elements.sharedText.addEventListener("input", () => {
  markSharedTextDirty();
});
elements.sharedText.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    saveSharedText(true);
  }
});
elements.refreshTextButton.addEventListener("click", () => loadSharedText(true));
elements.copyTextButton.addEventListener("click", copySharedText);
elements.pasteTextButton.addEventListener("click", pasteSharedText);
elements.clearTextButton.addEventListener("click", clearSharedText);
for (const name of ["dragenter", "dragover"]) elements.dropZone.addEventListener(name, event => { event.preventDefault(); elements.dropZone.classList.add("dragging"); });
for (const name of ["dragleave", "drop"]) elements.dropZone.addEventListener(name, event => { event.preventDefault(); elements.dropZone.classList.remove("dragging"); });
elements.dropZone.addEventListener("drop", event => { if (event.dataTransfer.files.length) uploadFiles([...event.dataTransfer.files]); });

(async function start() {
  try {
    await refreshSession();
    await Promise.all([loadSharedText(true), loadFiles()]);
    window.setInterval(() => loadSharedText(false), 3000);
  }
  catch (error) { document.querySelector("#connectionState").textContent = "服务不可用"; toast(error.message, true); }
})();
