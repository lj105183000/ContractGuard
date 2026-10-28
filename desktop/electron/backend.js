"use strict";

const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const { readEnvFile } = require("./env");

const DEV_PORT = 8000;
const HEALTH_TIMEOUT_MS = 120000;
const HEALTH_INTERVAL_MS = 500;

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function copyMissing(source, target) {
  const stat = fs.statSync(source);
  if (stat.isDirectory()) {
    fs.mkdirSync(target, { recursive: true });
    for (const name of fs.readdirSync(source)) {
      copyMissing(path.join(source, name), path.join(target, name));
    }
    return;
  }
  if (!fs.existsSync(target)) {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(source, target);
  }
}

function ensureSeedData(seedDir, dataDir) {
  if (!fs.existsSync(seedDir)) {
    throw new Error(`安装包缺少种子数据目录：${seedDir}`);
  }
  fs.mkdirSync(dataDir, { recursive: true });
  copyMissing(seedDir, dataDir);
}

function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close(() => (port ? resolve(port) : reject(new Error("无法获取空闲端口"))));
    });
  });
}

function portAvailable(port) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once("error", () => resolve(false));
    server.listen(port, "127.0.0.1", () => server.close(() => resolve(true)));
  });
}

class BackendProcess {
  constructor({ isDev, userDataDir }) {
    this.isDev = isDev;
    this.userDataDir = userDataDir;
    this.child = null;
    this.spawned = false;
    this.spawnError = null;
    this.stopped = false;
    this.port = null;
    this.logPath = "";
    this.logStream = null;
    this.pidFile = path.join(userDataDir, "backend.pid");
  }

  async start() {
    this._cleanupOrphan();
    this.port = await this._resolvePort();
    const url = `http://127.0.0.1:${this.port}`;

    if (await this._isHealthy(this.port)) {
      this.spawned = false;
      this._logLine(`复用已在 ${url} 运行的后端`);
      return url;
    }

    this._openLog();
    if (this.isDev) this._spawnDev();
    else this._spawnPackaged();

    await this._waitForHealth();
    this._writePid();
    return url;
  }

  stop() {
    if (this.stopped) return;
    this.stopped = true;
    if (this.spawned && this.child && this.child.exitCode === null) {
      if (process.platform === "win32") {
        spawnSync("taskkill", ["/PID", String(this.child.pid), "/T", "/F"], {
          windowsHide: true,
        });
      } else {
        this.child.kill("SIGTERM");
      }
    }
    this.child = null;
    this.spawned = false;
    try {
      fs.unlinkSync(this.pidFile);
    } catch {
      // ignore
    }
    if (this.logStream) {
      this.logStream.end();
      this.logStream = null;
    }
  }

  async _resolvePort() {
    const configured = Number(process.env.CONTRACTGUARD_BACKEND_PORT);
    if (Number.isInteger(configured) && configured > 0) {
      if (await this._isHealthy(configured)) return configured;
      if (await portAvailable(configured)) return configured;
      throw new Error(`CONTRACTGUARD_BACKEND_PORT=${configured} 已被其它进程占用`);
    }
    if (this.isDev) {
      if (process.env.CONTRACTGUARD_RENDERER_URL) {
        // 热更新模式必须让 Vite proxy 指向 8000。
        if (await this._isHealthy(DEV_PORT)) return DEV_PORT;
        if (await portAvailable(DEV_PORT)) return DEV_PORT;
        throw new Error(
          `端口 ${DEV_PORT} 被占用，Vite 热更新模式需要该端口。请关闭占用进程后重试。`,
        );
      }
      // 普通开发模式：优先 8000，但不要复用可能在运行的旧后端，
      // 否则新前端会连到过期代码。8000 被占用就换空闲端口并拉起当前后端。
      if (await portAvailable(DEV_PORT)) return DEV_PORT;
    }
    return findFreePort();
  }

  _spawnDev() {
    const backendDir = path.resolve(__dirname, "..", "..", "backend");
    this._logLine(`启动开发后端：uv run uvicorn（cwd=${backendDir}, port=${this.port}）`);
    this.spawned = true;
    this.child = spawn(
      "uv",
      [
        "run",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        String(this.port),
      ],
      {
        cwd: backendDir,
        env: {
          ...process.env,
          PYTHONUTF8: "1",
          PYTHONUNBUFFERED: "1",
        },
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    this._wireChild();
  }

  _spawnPackaged() {
    const executable = path.join(
      process.resourcesPath,
      "backend",
      process.platform === "win32" ? "contractguard-backend.exe" : "contractguard-backend",
    );
    if (!fs.existsSync(executable)) {
      throw new Error(`安装包缺少后端可执行文件：${executable}`);
    }

    const dataDir = path.join(this.userDataDir, "data");
    ensureSeedData(path.join(process.resourcesPath, "seed-data"), dataDir);

    const env = {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONUNBUFFERED: "1",
      CONTRACTGUARD_DATA_DIR: dataDir,
      CONTRACTGUARD_FRONTEND_DIST: path.join(
        process.resourcesPath,
        "frontend",
        "dist",
      ),
    };
    const fileEnv = readEnvFile(path.join(this.userDataDir, ".env"));
    for (const [key, value] of Object.entries(fileEnv)) {
      if (!process.env[key]) env[key] = value;
    }

    this._logLine(`启动打包后端：${executable}（data=${dataDir}, port=${this.port}）`);
    this.spawned = true;
    this.child = spawn(
      executable,
      ["--host", "127.0.0.1", "--port", String(this.port)],
      {
        cwd: path.dirname(executable),
        env,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    this._wireChild();
  }

  _wireChild() {
    this.child.on("error", (error) => {
      this.spawnError = error;
      this._logLine(`后端进程启动失败：${error.message}`);
    });
    this.child.on("exit", (code, signal) => {
      this._logLine(`后端进程退出：code=${code} signal=${signal}`);
    });
    if (this.child.stdout) {
      this.child.stdout.setEncoding("utf8");
      this.child.stdout.on("data", (chunk) => this.logStream?.write(chunk));
    }
    if (this.child.stderr) {
      this.child.stderr.setEncoding("utf8");
      this.child.stderr.on("data", (chunk) => {
        this.logStream?.write(chunk);
        if (this.isDev) process.stderr.write(chunk);
      });
    }
  }

  async _waitForHealth() {
    const deadline = Date.now() + HEALTH_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (this.spawnError) {
        throw new Error(`无法启动后端：${this.spawnError.message}`);
      }
      if (this.child && this.child.exitCode !== null) {
        throw new Error(
          `后端进程提前退出（code ${this.child.exitCode}），请查看日志：${this.logPath}`,
        );
      }
      if (await this._isHealthy(this.port)) return;
      await delay(HEALTH_INTERVAL_MS);
    }
    throw new Error(`后端启动超时，请查看日志：${this.logPath}`);
  }

  async _isHealthy(port) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/health`, {
        signal: AbortSignal.timeout(1500),
      });
      if (!response.ok) return false;
      const data = await response.json();
      return data?.status === "ok";
    } catch {
      return false;
    }
  }

  _openLog() {
    const dir = path.join(this.userDataDir, "logs");
    fs.mkdirSync(dir, { recursive: true });
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    this.logPath = path.join(dir, `backend-${stamp}.log`);
    this.logStream = fs.createWriteStream(this.logPath, {
      flags: "a",
      encoding: "utf8",
    });
    this._logLine(
      `桌面端启动后端：dev=${this.isDev} port=${this.port} pid=${process.pid}`,
    );
  }

  _logLine(line) {
    const text = `[${new Date().toISOString()}] [desktop] ${line}\n`;
    this.logStream?.write(text);
    if (this.isDev) process.stdout.write(text);
  }

  _writePid() {
    if (!this.spawned || !this.child) return;
    try {
      fs.writeFileSync(this.pidFile, String(this.child.pid), "utf8");
    } catch (error) {
      this._logLine(`写入 pid 文件失败：${error.message}`);
    }
  }

  _cleanupOrphan() {
    try {
      if (!fs.existsSync(this.pidFile)) return;
      const pid = Number(fs.readFileSync(this.pidFile, "utf8").trim());
      if (Number.isInteger(pid) && pid > 0 && process.platform === "win32") {
        const check = spawnSync(
          "tasklist",
          ["/FI", `PID eq ${pid}`, "/FO", "CSV", "/NH"],
          { encoding: "utf8", windowsHide: true },
        );
        const output = (check.stdout || "").toLowerCase();
        if (
          output.includes("contractguard-backend") ||
          output.includes("uv.exe")
        ) {
          spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], {
            windowsHide: true,
          });
        }
      }
    } catch {
      // 清理失败不阻塞启动
    } finally {
      try {
        fs.unlinkSync(this.pidFile);
      } catch {
        // ignore
      }
    }
  }
}

module.exports = { BackendProcess, ensureSeedData };
