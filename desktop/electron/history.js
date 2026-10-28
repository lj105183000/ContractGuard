"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { shell } = require("electron");

const MAX_ENTRIES = 30;
const MAX_BYTES = 20 * 1024 * 1024;
const MAX_TOTAL_BYTES = 200 * 1024 * 1024;

class HistoryStore {
  constructor(userDataDir) {
    this.dir = path.join(userDataDir, "history");
  }

  ensure() {
    fs.mkdirSync(this.dir, { recursive: true });
  }

  _file(jobId) {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(jobId)) {
      throw new Error("非法的任务 ID");
    }
    return path.join(this.dir, `${jobId}.json`);
  }

  list() {
    this.ensure();
    const entries = [];
    for (const name of fs.readdirSync(this.dir)) {
      if (!name.endsWith(".json")) continue;
      try {
        const data = JSON.parse(fs.readFileSync(path.join(this.dir, name), "utf8"));
        if (!data || typeof data !== "object" || !data.jobId) continue;
        entries.push({
          jobId: data.jobId,
          filename: data.filename || "",
          savedAt: data.savedAt || "",
          mode: data.mode || data.job?.result?.mode || "mock",
          status: data.job?.status || "completed",
          summary: data.job?.result?.summary || {},
        });
      } catch (error) {
        console.warn(`[history] 跳过损坏文件 ${name}：${error.message}`);
      }
    }
    return entries.sort(
      (a, b) => (Date.parse(b.savedAt) || 0) - (Date.parse(a.savedAt) || 0),
    );
  }

  get(jobId) {
    const file = this._file(jobId);
    if (!fs.existsSync(file)) return null;
    return JSON.parse(fs.readFileSync(file, "utf8"));
  }

  save(entry) {
    if (!entry || typeof entry !== "object") {
      throw new Error("无效的历史记录");
    }
    const job = entry.job;
    const jobId = String(entry.jobId || job?.job_id || "");
    if (!job || job.job_id !== jobId) {
      throw new Error("历史记录缺少任务快照");
    }
    const payload = {
      jobId,
      filename: entry.filename || job.filename || "",
      savedAt: entry.savedAt || new Date().toISOString(),
      mode: entry.mode || job.result?.mode || "mock",
      job,
    };
    const json = JSON.stringify(payload);
    if (Buffer.byteLength(json, "utf8") > MAX_BYTES) {
      throw new Error(
        `历史记录超过 ${Math.round(MAX_BYTES / (1024 * 1024))}MB，未保存`,
      );
    }

    this.ensure();
    const file = this._file(jobId);
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, json, "utf8");
    try {
      fs.renameSync(tmp, file);
    } catch {
      // Windows 上目标文件已存在时 rename 可能失败，先删除再重命名。
      try {
        fs.unlinkSync(file);
      } catch {
        // ignore
      }
      fs.renameSync(tmp, file);
    }
    this._prune();
    return { jobId, savedAt: payload.savedAt };
  }

  remove(jobId) {
    try {
      fs.unlinkSync(this._file(jobId));
      return true;
    } catch {
      return false;
    }
  }

  clear() {
    this.ensure();
    for (const name of fs.readdirSync(this.dir)) {
      if (!name.endsWith(".json") && !name.endsWith(".tmp")) continue;
      try {
        fs.unlinkSync(path.join(this.dir, name));
      } catch {
        // ignore
      }
    }
    return true;
  }

  openFolder() {
    this.ensure();
    return shell.openPath(this.dir);
  }

  _prune() {
    const files = fs
      .readdirSync(this.dir)
      .filter((name) => name.endsWith(".json"))
      .map((name) => {
        const file = path.join(this.dir, name);
        const stat = fs.statSync(file);
        return { file, mtime: stat.mtimeMs, size: stat.size };
      })
      .sort((a, b) => b.mtime - a.mtime);

    let kept = 0;
    let total = 0;
    for (const item of files) {
      // 始终保留最新一条；其余按条数 + 总大小双重预算淘汰最旧记录。
      if (
        kept > 0 &&
        (kept >= MAX_ENTRIES || total + item.size > MAX_TOTAL_BYTES)
      ) {
        try {
          fs.unlinkSync(item.file);
        } catch {
          // ignore
        }
        continue;
      }
      kept += 1;
      total += item.size;
    }
  }
}

module.exports = { HistoryStore };
