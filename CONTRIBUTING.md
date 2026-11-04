# 贡献指南

感谢关注 ContractGuard。这是一个用于软件工程与 AI 应用演示的合同初审 MVP，范围刻意收窄：**只做中文货物采购合同**，示例数据全部为合成合同。提交前请先确认改动落在这个边界内。

- 项目总览与设计取舍：[README.md](README.md)
- 初学者代码导读：[docs/CODE_GUIDE.md](docs/CODE_GUIDE.md)
- 完整运行说明：[docs/QUICKSTART.md](docs/QUICKSTART.md)
- 安全漏洞报告：[SECURITY.md](SECURITY.md)

## 环境

- Python 3.12（由 `backend/.python-version` 固定，`uv` 会自动安装）
- Node.js 24（Vite 7 最低要求 20.19+ / 22.12+；桌面端 Electron 45 要求 22.12+）
- Docker Desktop（可选，用于容器化 smoke）
- Windows 10/11（桌面端打包；后端与前端可在其它系统运行）

```bash
# 后端（默认 mock，不需要 API Key）
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm install
npm run dev
```

打开 <http://localhost:5173>。单端口模式、Docker、桌面端和 live 模式见 [docs/QUICKSTART.md](docs/QUICKSTART.md)。

## 提交前检查

至少跑通与改动相关的检查；CI 会在 PR 上跑完整门禁。

```bash
# 后端：258 个离线测试 + 覆盖率门禁
cd backend
LLM_MODE=mock uv run pytest -q --cov=app --cov-report=term --cov-fail-under=80

# 评测门禁（规则 / 检索 / 扰动 / 成本），与 CI 一致
uv run python eval/run_eval.py --mode rules \
  --min-precision 0.99 --min-recall 0.93 --min-f1 0.96
uv run python eval/run_retrieval_eval.py --no-rerank \
  --threshold-config bm25 --min-recall-at-10 0.45 --min-ndcg-at-10 0.35
uv run python eval/run_perturbation_eval.py --min-baseline-f1 0.95 --max-f1-drop-pp 5
uv run python eval/run_cost_report.py \
  --max-cost-cny 1.0 --max-p95-s 120 --max-failed-calls 10 --max-rate-limited-calls 10

# 前端
cd frontend
npm ci && npm run build && npm run test:diff

# 文档内链（仓库根目录）
python scripts/check_docs_links.py
```

桌面端改动请额外执行 `cd desktop && npm ci && npm run pack:dir`，并按 [desktop/README.md](desktop/README.md) 运行打包 smoke。

## 代码与提交约定

- 使用 Conventional Commits：`feat: ...`、`fix: ...`、`docs: ...`、`test: ...`、`chore: ...`、`ci: ...`。
- 一个提交只做一件事；格式化/重命名与行为改动分开。
- 新增规则、法条或评测标签时，同步更新对应校验脚本与文档：
  - `backend/scripts/validate_kb.py`、`validate_eval_assets.py`
  - [docs/EVALUATION.md](docs/EVALUATION.md)、[docs/DATA_ASSETS.md](docs/DATA_ASSETS.md)
- 新增 API 或改变导出/复核门禁时，更新 README 的 API 表和 `docs/LAUNCH_ACCEPTANCE.md`。
- 前端改动保持现有手写 CSS 方案，不引入路由/状态库/Tailwind。

## 分支与 Pull Request

1. 从 `main` 切分支：`git switch -c feat/short-description`。
2. 提交并推送后开 PR，填写 PR 模板中的变更、测试与风险说明。
3. 等 CI 全绿；桌面 packaging smoke 在 Windows runner 上耗时较长，属正常现象。
4. 合并后删除分支（仓库已开启自动删除 head branch）。

## 数据与隐私红线

- **不要提交真实合同、真实公司名、个人信息或任何 API Key。** `backend/.env`、`docs/*.pdf`、`local-assets/`、`.eval-backup/` 已被忽略，但请自行确认 `git status`。
- 示例合同只能使用合成数据；截图和录屏同样需要脱敏。
- 新增日志、trace、feedback 或导出功能时，默认最小化记录，并考虑 `ENCRYPTION_ENABLED=1` 与保留期清理。
- 发现凭据泄露时不要只删提交，请同时轮换密钥并联系维护者。

## 许可证

提交即表示同意以 [MIT License](LICENSE) 授权你的贡献。第三方素材（法律文本、字体、截图、视频等）必须确认可再分发。
