"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("contractguardDesktop", {
  isDesktop: true,
  platform: process.platform,
  history: {
    list: () => ipcRenderer.invoke("history:list"),
    get: (jobId) => ipcRenderer.invoke("history:get", jobId),
    save: (entry) => ipcRenderer.invoke("history:save", entry),
    remove: (jobId) => ipcRenderer.invoke("history:remove", jobId),
    clear: () => ipcRenderer.invoke("history:clear"),
    openFolder: () => ipcRenderer.invoke("history:open-folder"),
  },
  notifyReviewCompleted: (payload) => ipcRenderer.send("notify:review-completed", payload),
  appInfo: () => ipcRenderer.invoke("app:info"),
});
