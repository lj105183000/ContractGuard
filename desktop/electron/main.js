"use strict";

const {
  app,
  BrowserWindow,
  Tray,
  Menu,
  Notification,
  ipcMain,
  shell,
  dialog,
  session,
  nativeImage,
} = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { BackendProcess } = require("./backend");
const { HistoryStore } = require("./history");

const isDev = !app.isPackaged;
app.setName(isDev ? "ContractGuard Dev" : "ContractGuard");
app.setAppUserModelId(
  isDev ? "com.contractguard.desktop.dev" : "com.contractguard.desktop",
);
// 开发版使用独立 userData，避免和已安装版本共享单实例锁/历史/日志。
if (isDev) {
  const devUserData = path.join(app.getPath("appData"), "ContractGuard Dev");
  try {
    fs.mkdirSync(devUserData, { recursive: true });
  } catch {
    // ignore
  }
  app.setPath("userData", devUserData);
}

const assetsDir = path.join(__dirname, "..", "assets");
const iconPath = path.join(assetsDir, "icon.ico");
const trayIconPath = path.join(assetsDir, "tray.png");

let mainWindow = null;
let tray = null;
let backend = null;
let history = null;
let appUrl = "";
let quitting = false;
let trayHintShown = false;

const gotSingleInstanceLock = app.requestSingleInstanceLock();

if (!gotSingleInstanceLock) {
  console.warn(
    "[desktop] 已有一个 ContractGuard 实例在运行，本次启动退出；请检查系统托盘或任务管理器。",
  );
  app.quit();
} else {
  app.on("second-instance", () => showMainWindow());
  app.whenReady().then(bootstrap).catch(handleFatalError);
}

async function bootstrap() {
  history = new HistoryStore(app.getPath("userData"));
  history.ensure();

  await createWindow();
  createTray();
  registerIpc();
  registerDownloads();
  registerExternalLinks();

  backend = new BackendProcess({
    isDev,
    userDataDir: app.getPath("userData"),
  });

  try {
    const backendUrl = await backend.start();
    appUrl = process.env.CONTRACTGUARD_RENDERER_URL || backendUrl;
    await mainWindow.loadURL(appUrl);
  } catch (error) {
    dialog.showErrorBox(
      "无法启动本地审查服务",
      `${error.message}\n\n日志文件：${backend?.logPath || "未生成"}\n` +
        "请检查后端依赖（uv sync）或重新安装应用。",
    );
    quitting = true;
    app.quit();
  }
}

async function createWindow() {
  const windowIcon = fs.existsSync(iconPath) ? iconPath : undefined;
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1024,
    minHeight: 700,
    show: false,
    backgroundColor: "#f4efe4",
    title: "合同哨兵 ContractGuard",
    icon: windowIcon,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  mainWindow.on("close", (event) => {
    if (quitting) return;
    if (!tray) {
      quitting = true;
      app.quit();
      return;
    }
    event.preventDefault();
    mainWindow.hide();
    showTrayHintOnce();
  });

  await mainWindow.loadFile(path.join(__dirname, "..", "loading.html"));
  mainWindow.show();
}

function createTray() {
  let image = nativeImage.createFromPath(trayIconPath);
  if (image.isEmpty()) image = nativeImage.createFromPath(iconPath);
  if (image.isEmpty()) {
    console.warn("[tray] 找不到托盘图标，关闭窗口将直接退出应用。");
    return;
  }

  tray = new Tray(image);
  tray.setToolTip("合同哨兵 ContractGuard");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "显示合同哨兵", click: showMainWindow },
      { label: "打开历史记录目录", click: () => history?.openFolder() },
      { type: "separator" },
      {
        label: "退出",
        click: () => {
          quitting = true;
          app.quit();
        },
      },
    ]),
  );
  tray.on("double-click", showMainWindow);
}

function registerIpc() {
  ipcMain.handle("history:list", (event) => {
    assertTrusted(event);
    return history.list();
  });
  ipcMain.handle("history:get", (event, jobId) => {
    assertTrusted(event);
    return history.get(String(jobId || ""));
  });
  ipcMain.handle("history:save", (event, entry) => {
    assertTrusted(event);
    return history.save(entry);
  });
  ipcMain.handle("history:remove", (event, jobId) => {
    assertTrusted(event);
    return history.remove(String(jobId || ""));
  });
  ipcMain.handle("history:clear", (event) => {
    assertTrusted(event);
    return history.clear();
  });
  ipcMain.handle("history:open-folder", (event) => {
    assertTrusted(event);
    return history.openFolder();
  });
  ipcMain.handle("app:info", (event) => {
    assertTrusted(event);
    return {
      version: app.getVersion(),
      isDev,
      dataDir: app.getPath("userData"),
      appUrl,
    };
  });
  ipcMain.on("notify:review-completed", (event, payload) => {
    try {
      assertTrusted(event);
      showReviewNotification(payload);
    } catch (error) {
      console.warn(`[notification] ${error.message}`);
    }
  });
}

function assertTrusted(event) {
  const sender = event.senderFrame?.url || "";
  if (!appUrl) throw new Error("应用尚未就绪");
  try {
    if (new URL(sender).origin !== new URL(appUrl).origin) {
      throw new Error("拒绝来自未知页面的请求");
    }
  } catch (error) {
    if (error.message === "拒绝来自未知页面的请求") throw error;
    throw new Error("拒绝来自未知页面的请求");
  }
}

function registerDownloads() {
  session.defaultSession.on("will-download", (event, item) => {
    const filename = item.getFilename() || "ContractGuard_report";
    const extension = path.extname(filename).slice(1).toLowerCase();
    const filters =
      extension === "docx"
        ? [{ name: "Word 文档", extensions: ["docx"] }]
        : extension === "json"
          ? [{ name: "JSON 文件", extensions: ["json"] }]
          : [{ name: "Markdown 文件", extensions: ["md"] }];
    item.setSaveDialogOptions({
      title: filename.includes("修订稿") ? "保存合同修订稿" : "保存审查报告",
      defaultPath: path.join(app.getPath("downloads"), filename),
      filters,
    });
  });
}

function registerExternalLinks() {
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) {
      shell.openExternal(url).catch(() => {});
    }
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!appUrl) return;
    let allowed = false;
    try {
      allowed = new URL(url).origin === new URL(appUrl).origin;
    } catch {
      allowed = false;
    }
    if (allowed) return;
    event.preventDefault();
    if (/^https?:\/\//i.test(url)) {
      shell.openExternal(url).catch(() => {});
    }
  });
}

function showReviewNotification(payload = {}) {
  if (!Notification.isSupported()) return;
  const filename = payload.filename || "合同";
  const total = Number(payload.total) || 0;
  const high = Number(payload.high) || 0;
  const notification = new Notification({
    title: "审查完成",
    body: `${filename}：${high} 条高风险 / 共 ${total} 条`,
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
  });
  notification.on("click", showMainWindow);
  notification.show();
}

function showTrayHintOnce() {
  if (trayHintShown) return;
  trayHintShown = true;
  if (!Notification.isSupported()) return;
  new Notification({
    title: "合同哨兵仍在运行",
    body: "窗口已隐藏到系统托盘。右键托盘图标可退出。",
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
  }).show();
}

function showMainWindow() {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

function handleFatalError(error) {
  dialog.showErrorBox("合同哨兵启动失败", String(error?.stack || error));
  quitting = true;
  app.quit();
}

app.on("before-quit", () => {
  quitting = true;
  backend?.stop();
});

app.on("will-quit", () => {
  backend?.stop();
  tray?.destroy();
  tray = null;
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin" && quitting) app.quit();
});
