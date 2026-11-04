# 快速开始

> 本文是 ContractGuard 的完整本地运行说明，命令默认在仓库根目录执行。返回 [README](../README.md)。

## 环境

- Windows 10/11（其它系统也可，启动脚本是 `.bat`，命令可手动执行）
- [uv](https://docs.astral.sh/uv/)（会用项目里的 `.python-version` 安装 Python 3.12）
- Node.js 24（Vite 8 要求 20.19+ 或 22.12+；桌面端 Electron 45 要求 22.12+；本项目 CI 与验证环境统一用 Node 24）
- 扫描件 OCR 由 RapidOCR + onnxruntime 提供，随 `uv sync` 安装；模型已打包，无需 Tesseract 或联网下载
- Docker Desktop（可选，用于 `docker-compose up --build` 容器化演示）

## 1. 启动后端（默认 mock，不需要 API Key）

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

或双击 `scripts\start_backend.bat`。

## 2. 启动前端（开发模式）

```bash
cd frontend
npm install
npm run dev
```

打开 <http://localhost:5173>。或双击 `scripts\start_frontend.bat`。

## 3. 单端口演示（可选）

```bash
cd frontend
npm run build
cd ../backend
uv run uvicorn app.main:app --port 8000
```

打开 <http://127.0.0.1:8000>。或直接双击 `scripts\start_demo.bat`。

## 4. 桌面端（可选）

```bash
cd desktop
npm install
npm run dev
```

或双击 `scripts\start_desktop.bat`。桌面端会自动启动后端并加载界面；支持整窗拖拽、本地历史、托盘与完成通知。关闭窗口只是隐藏到托盘，托盘菜单「退出」才会结束后端。

打包 Windows 安装包：

```bash
cd desktop
npm run dist
# 产物：desktop/release/ContractGuard-Setup-<version>.exe
```

细节、数据目录与 live 配置位置见 [`desktop/README.md`](../desktop/README.md)。

## 5. Docker（mock，一键启动）

```bash
docker-compose up --build
```

打开 <http://127.0.0.1:8000>。若 8000 被占用，可用 `CONTRACTGUARD_PORT=8001 docker-compose up --build`。镜像内自带法条、Playbook、示例合同和前端构建产物，默认 `LLM_MODE=mock`、`HITL_ENABLED=1`、`WORKER_ENABLED=1`，只绑定 localhost。容器 entrypoint 会修正 named volume 权限后以非 root `appuser` 运行 uvicorn；任务 SQLite、checkpoint SQLite 与上传源文件都保存在 named volume `contractguard-data`：`docker-compose down/up` 不丢暂停任务和已完成任务，`docker-compose down -v` 才会清空。若要 live，可在 `docker-compose.yml` 取消 `DASHSCOPE_API_KEY` 注释并设置 `LLM_MODE=live`。**如需从内网其它机器访问，必须把端口绑定改为可访问地址，并同时设置 `CONTRACTGUARD_API_TOKEN`、`CONTRACTGUARD_REQUIRE_AUTH=1`、`CONTRACTGUARD_ALLOWED_HOSTS`；否则后端会拒绝启动或拒绝 Host。** 公网部署前还必须自行加 TLS/反代和共享限流。

## 6. 切换到 live（调用真实模型）

1. 注册[阿里云百炼](https://bailian.console.aliyun.com/)，开通 `qwen-plus` 与 `text-embedding-v3`（也可以换成其它 OpenAI 兼容 Provider）。
2. 复制配置：

   ```bash
   cd backend
   copy .env.example .env        # Windows cmd
   # 或 cp .env.example .env    # Git Bash
   ```

3. 编辑 `backend/.env`：

   ```dotenv
   LLM_MODE=live
   DASHSCOPE_API_KEY=sk-你的Key
   # 可选：其它 Provider
   DEEPSEEK_API_KEY=sk-...
   OPENAI_API_KEY=sk-...
   # 可选：指定默认路由
   LLM_ROUTE=balanced
   ```

4. 查看/编辑 [`backend/data/llm_profiles.yaml`](../backend/data/llm_profiles.yaml)：providers 只写环境变量名，routes 定义 `default` 回退链和 `tasks` 任务优先链。默认 `balanced` 已验证可用；`deepseek-first`、`quality`、`local` 等路由按需启用。

5. 重启后端。首次请求会自动构建向量缓存；也可以预构建：

   ```bash
   uv run python scripts/build_kb.py --embeddings
   ```

6. 页面右上角应从 `MOCK` 变为 `LIVE`，左侧「审查模型」下拉可选择路由；结果头部会显示实际路由和模型。8 份合同全量评测预计花费几元人民币以内，以百炼账单为准。

> 不要把 `.env` 提交到 git。没有可用 Provider Key 时系统会明确降级为 mock，不会静默伪造 LLM 结果。
>
> 聊天模型失败时按 profiles 的顺序自动回退；每个 profile 最多尝试 2 次，`LLM_TIMEOUT_S`/`LLM_DEADLINE_S` 控制单次请求与总时限。Embedding 仍单独配置：可用 `EMBEDDING_API_KEY`/`EMBEDDING_BASE_URL` 指向另一个 OpenAI 兼容服务，未配置时回退 BM25。

## 7. 可靠性与静态加密（可选）

后端默认启用 SQLite 租约 worker：上传完成后任务入队，worker 用 DB CAS 领取，同一任务只会被一个进程执行；执行期间心跳续租，进程崩溃后过期租约由 reaper 回收。`resuming` 任务最多重试 3 次；`running` 任务不会自动重放 LLM（避免重复计费），会标记为失败。

```dotenv
WORKER_ENABLED=1
WORKER_CONCURRENCY=2
WORKER_POLL_INTERVAL_S=1.0
LEASE_SECONDS=60
HEARTBEAT_SECONDS=15
```

需要多进程时可以 `uv run uvicorn app.main:app --workers 2`：每个进程都会启动自己的 worker/reaper，靠 SQLite 租约协调。SQLite 适合单机低并发，不是高吞吐队列；解析/OCR 在线程池执行，但大 PDF/OCR 仍会消耗 CPU/内存并拉长任务时间，必要时调大 `LEASE_SECONDS`。

静态加密默认关闭，开启后 `jobs.state_json`（含合同全文）与 LangGraph checkpoint 使用 AES-GCM 加密：

```dotenv
ENCRYPTION_ENABLED=1
CONTRACTGUARD_MASTER_KEY=<至少 16 字符的随机值>
```

生成密钥：`python -c "import secrets; print(secrets.token_urlsafe(32))"`。旧的明文记录仍可读；主密钥丢失后无法解密，不支持在线轮换。关闭加密不会解密已有数据，旧库需要保持密钥或清空重建。开启后 `jobs.state_json`、checkpoint、feedback JSONL 和 run trace 加密；桌面历史在 Electron `safeStorage` 可用时加密；上传源文件、任务列表 filename/summary/error 仍是明文，建议同时使用磁盘/卷加密。启动时 `ENCRYPTION_ENABLED=0` 会记录警告。完整边界与保留策略见 [`docs/DATA_GOVERNANCE.md`](DATA_GOVERNANCE.md)。

可选单租户鉴权、Host 校验与资源配额：

```dotenv
CONTRACTGUARD_API_TOKEN=<随机 Token>   # 留空关闭鉴权
CONTRACTGUARD_REQUIRE_AUTH=0          # 内网多机访问必须设为 1
CONTRACTGUARD_ALLOWED_HOSTS=          # 默认 127.0.0.1/localhost；内网 IP/域名逗号分隔
MAX_ACTIVE_JOBS=20
RATE_LIMIT_PER_MINUTE=0                # 0 关闭；进程内滑动窗口
MAX_UPLOAD_MB=50
MAX_JSON_BODY_MB=2
PDF_MAX_PAGES=200
DOCX_MAX_UNCOMPRESSED_MB=200
DOCX_MAX_ENTRIES=2000
DOCX_MAX_XML_MB=50
MAX_TEXT_CHARS=2000000
MAX_CLAUSES=1000
MAX_IMAGE_PIXELS=50000000
JOB_DEADLINE_S=900
```

设置 Token 后，除 `/api/health` 外的 API 都要求 `Authorization: Bearer <token>`；前端会弹出 Token 登录页。**内网多机访问必须同时设置 Token、`CONTRACTGUARD_REQUIRE_AUTH=1` 和 `CONTRACTGUARD_ALLOWED_HOSTS`（非本机地址）；否则后端拒绝启动。** 服务默认只接受本机 Host（防 DNS rebinding），`CORS_ORIGIN` 不允许 `*`。请求体在解析前按 `MAX_UPLOAD_MB`/`MAX_JSON_BODY_MB` 限制；解析阶段再限制文本、条款、图片像素、PDF 页面尺寸和 DOCX 实际解压体积。限流是单进程内存实现，多进程/公网部署仍应在反向代理层做共享限流。

备份/恢复（先停止服务再恢复）：

```bash
cd backend
uv run python scripts/backup_db.py --output-dir backups --include-uploads \
  --include-feedback --include-traces --include-eval --include-audit
uv run python scripts/restore_db.py --backup-dir backups/20261028_120000 --yes \
  --include-uploads --include-feedback --include-traces --include-eval --include-audit
```

备份使用 SQLite 在线 backup API；`--include-uploads` 复制上传源文件，`--include-feedback`/`--include-traces` 复制反馈与运行 trace，`--include-eval` 复制公开评测产物。加密库恢复时必须提供原 `CONTRACTGUARD_MASTER_KEY`；备份 manifest 会记录加密状态，但不包含密钥。

## 8. 测试与评测

```bash
cd backend
export LLM_MODE=mock          # Windows cmd: set LLM_MODE=mock；避免 .env 的 live Key 被 eval/scripts 使用
uv run pytest -q --cov=app --cov-fail-under=80
uv run python scripts/validate_kb.py
uv run python scripts/validate_eval_assets.py
uv run python scripts/export_feedback.py --out-dir ../exports/feedback --formats jsonl,csv,sqlite
uv run python scripts/export_run_traces.py --out-dir ../exports/traces   # 解密导出 trace，含合同全文
uv run python scripts/scan_pii.py --path ../contractguard-private/docs  # 只输出 PII 计数
uv run python scripts/smoke_api.py             # 使用临时库，不写真实 data/
uv run python scripts/test_live_offline.py     # 本地假 OpenAI，验证 live + 模型回退，不需要 Key
uv run python eval/run_eval.py --mode rules   # 无需 Key
uv run python eval/run_eval.py --mode full    # 需要 live Key；先 export LLM_MODE=live
uv run python eval/run_retrieval_eval.py --no-rerank  # mock 下 hybrid 会退化为 BM25
uv run python eval/run_perturbation_eval.py   # 无需 Key
uv run python eval/run_cost_report.py         # 读取已落盘 full 结果
uv run python eval/run_ablation.py            # 汇总 rules/full/no-RAG/检索消融
uv run python eval/run_quality_eval.py --scores <quality_scores.jsonl>  # 需要人工评分
```
