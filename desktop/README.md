# ContractGuard 桌面端

Windows 桌面壳：Electron 负责窗口、托盘、通知与本地历史，启动时自动拉起 FastAPI 后端，加载后端同源托管的前端。关闭窗口会隐藏到托盘，只有托盘「退出」才会结束后端。

## 开发运行

前置：Node.js 22.12+（Electron 45 的要求）、uv、Python 3.12（与 Web 版相同）。

```bash
cd desktop
npm install
npm run dev
```

`npm run dev` 会先构建 `frontend/dist`，再用 Electron 启动：

1. 选择空闲端口（开发模式优先 8000）；
2. 运行 `uv run uvicorn app.main:app --host 127.0.0.1 --port <port>`；
3. 轮询 `/api/health` 就绪后加载 `http://127.0.0.1:<port>/`。

也可以在仓库根目录双击 `scripts\start_desktop.bat`。

### 前端热更新

```bash
# 终端 1
cd frontend && npm run dev

# 终端 2
cd desktop && npm run dev:hot
```

热更新模式固定使用 8000 端口（Vite proxy 指向它）。如果 8000 被其它进程占用，桌面端会给出明确错误。

### 冒烟测试

```bash
cd desktop
npm run build:renderer
node scripts/smoke.mjs
```

脚本用 Playwright 启动 Electron（mock 模式、端口 8123），完成一次示例审查并打开本地历史，截图输出到 `desktop/.smoke/`。

## 打包

```bash
cd desktop
npm run dist
```

产物：`desktop/release/ContractGuard-Setup-<version>.exe`（NSIS，当前用户安装，无需管理员）。

OCR 模型（RapidOCR/PP-OCRv6）与 onnxruntime 会打进后端：解压后后端约增加 180MB，安装包约增加 60–80MB。

国内网络如果下载 Electron 或 NSIS 资源失败，先设置镜像（`scripts\build_desktop.bat` 已内置）：

```bat
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
set ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/
npm run dist
```

打包流程：

1. `frontend/dist`：Vite 构建；
2. `backend/dist/contractguard-backend/`：PyInstaller onedir 后端；
3. electron-builder 把两者与种子数据放入安装包。

调试未安装版本可以只生成解包目录：

```bash
npm run pack:dir
# desktop/release/win-unpacked/ContractGuard.exe
```

PyInstaller 构建较慢，且对动态导入敏感。若打包版启动失败，先直接运行后端排查：

```bash
cd backend
uv run pyinstaller --noconfirm --clean pyinstaller.spec
dist\contractguard-backend\contractguard-backend.exe --port 8765
# 访问 http://127.0.0.1:8765/api/health
```

## 数据与配置位置

所有运行时文件都在 `%APPDATA%\ContractGuard\`（开发模式使用独立的 `%APPDATA%\ContractGuard Dev\`，可与安装版同时运行）：

| 路径 | 内容 |
|---|---|
| `data\` | 首次启动从安装包复制的法条、Playbook、示例合同、`llm_profiles.yaml`；后续上传、反馈、索引缓存也写在这里 |
| `history\` | 本地审查快照（每条一个 JSON，最多 30 条、单条 ≤20MB，总预算 200MB） |
| `logs\` | 后端 stdout/stderr 日志 |
| `.env` | 可选，打包版 live 模式配置 |

打包版读取 `%APPDATA%\ContractGuard\.env`，支持以下键（不会覆盖已存在的进程环境变量）：

```dotenv
LLM_MODE=live
DASHSCOPE_API_KEY=sk-你的Key
# 可选：其它 Provider / 路由
DEEPSEEK_API_KEY=
OPENAI_API_KEY=
MOONSHOT_API_KEY=
OLLAMA_API_KEY=
LLM_ROUTE=balanced
LLM_TIMEOUT_S=90
LLM_DEADLINE_S=180
LLM_MODEL=qwen-plus          # 仅 profiles 文件缺失时的 legacy 模型
EMBEDDING_API_KEY=
EMBEDDING_BASE_URL=
EMBEDDING_MODEL=text-embedding-v3
MAX_UPLOAD_MB=50
# 扫描件 OCR
OCR_MAX_PAGES=50
OCR_RENDER_SCALE=2.0
OCR_MIN_SCORE=0.5
# 任务 worker / 静态加密（可选）
WORKER_ENABLED=1
WORKER_CONCURRENCY=2
LEASE_SECONDS=60
HEARTBEAT_SECONDS=15
ENCRYPTION_ENABLED=0
CONTRACTGUARD_MASTER_KEY=
# CONTRACTGUARD_UPLOAD_DIR=
# 可选单租户 Token；启用后界面会要求输入
# CONTRACTGUARD_API_TOKEN=
MAX_ACTIVE_JOBS=20
RATE_LIMIT_PER_MINUTE=0
PDF_MAX_PAGES=200
DOCX_MAX_UNCOMPRESSED_MB=200
```

模型路由在 `%APPDATA%\ContractGuard\data\llm_profiles.yaml` 中配置（providers 只写环境变量名，routes 定义任务优先链和回退链）。修改后需要重启应用。安装包首次启动会把默认 profiles 复制过去；已存在时不覆盖，想采用新版默认值可先备份/删除该文件。

开发模式仍读取 `backend\.env`。没有可用 Provider Key 时自动降级为 mock，不会静默失败。

## 桌面端行为

- **整窗拖拽**：拖 PDF/DOCX/TXT/JPG/PNG 到窗口任意位置即可上传；审查进行中会拒绝新文件并提示。
- **扫描件 OCR**：扫描版 PDF 与图片由本地 RapidOCR 识别（中英文、离线），默认最多 50 页；首次识别会加载模型，速度略慢，结果会提示人工核对。
- **模型路由**：上传卡片可选择「审查模型」路由；不同路由可按任务使用不同 Provider，并在失败时自动回退。结果头会显示实际路由和成功调用的模型。
- **本地历史**：审查完成后自动保存完整结果快照（单条 ≤20MB）。快照包含合同全文；超过 20MB 时会剥离 `full_text` 重试保存，此时历史里只能用条款视图查看。历史记录为只读，可查看摘要、风险、合同全文与详情，但不能继续复核、预览、导出报告或生成修订稿。桌面端顶部同时提供「任务历史」（后端 SQLite），可恢复暂停任务并继续复核。
- **托盘**：关闭窗口隐藏到托盘；托盘菜单可显示窗口、打开历史目录、退出。
- **通知**：审查完成时弹 Windows 通知，点击通知恢复窗口。
- **外链**：法条「查看官方来源」在系统浏览器打开。
- **下载**：报告/合同修订稿导出时弹出保存对话框；修订稿文件名含“修订稿”时对话框标题会相应变化。

## 常见问题

**脚本启动后窗口没出现**
先看控制台输出（`scripts\start_desktop.bat` 现在会在退出后暂停）。最常见原因是已有一个桌面端实例在系统托盘里：关闭窗口只是隐藏到托盘，新进程会因单实例锁直接退出。右键托盘图标显示/退出旧实例后再试。开发模式日志在 `%APPDATA%\ContractGuard Dev\logs`，安装版在 `%APPDATA%\ContractGuard\logs`。

**后端起不来 / 窗口报错**
查看对应 `logs\backend-*.log`。开发模式确认 `uv sync` 成功；打包版确认 `resources\backend\contractguard-backend.exe` 存在。

**8000 端口被占用**
普通 `npm run dev` 会自动换端口；`npm run dev:hot` 需要 8000，请先关闭占用进程，或设置 `CONTRACTGUARD_BACKEND_PORT`。

**关闭窗口后进程还在**
这是预期行为（隐藏到托盘）。通过托盘菜单「退出」结束；若 Electron 崩溃，下次启动会清理 `backend.pid` 记录的孤儿进程。

**扫描件 OCR 很慢 / 报页数超限**
OCR 需要逐页加载模型并识别；8 页扫描件通常需要十几到二十几秒。默认最多 50 页，可在 `.env` 调高 `OCR_MAX_PAGES` 或拆分文件。识别结果会带置信度提示，务必对照原件核对。

**模型路由不可用 / 一直是 MOCK**
查看 `%APPDATA%\ContractGuard\logs` 与 `/api/meta` 的 `mode_reason`、`llm_routes[].reason`。确认 `.env` 里对应 `api_key_env` 的 Key 已填写；本地模型还需要在 `data/llm_profiles.yaml` 把 `enabled` 改为 true 并启动服务。修改 profiles 或 `.env` 后重启应用。

**安装包被 SmartScreen 拦截**
当前构建未做代码签名，属于预期现象；仅用于本地演示。

**打包时报 `default_app.asar` EBUSY / EPERM**
通常是编辑器或杀毒软件正在扫描 `release` 目录（本机曾遇到 Typora 持有文件句柄）。关闭对应程序后删除 `release` 再重试。构建使用 `node_modules/electron/dist`，不会重新下载 Electron。
