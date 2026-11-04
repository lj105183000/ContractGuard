## 变更内容

<!-- 一两句话说明做了什么、为什么做。关联 Issue 请写 Closes #123。 -->

## 类型

- [ ] Bug 修复
- [ ] 新功能
- [ ] 规则 / 法条 / 评测数据
- [ ] 重构或性能
- [ ] 文档 / CI / 依赖

## 验证

<!-- 列出实际跑过的命令与结果，不要只写“已测试”。 -->

```bash
# 示例
cd backend && LLM_MODE=mock uv run pytest -q --cov=app --cov-fail-under=80
cd frontend && npm run build && npm run test:diff
python scripts/check_docs_links.py
```

## 风险与边界

<!-- 是否改变复核/导出门禁、数据保留、加密、API 兼容性或评测指标？ -->

## 检查清单

- [ ] 改动限定在中文货物采购合同场景内，或已说明扩展理由。
- [ ] 未提交 `.env`、API Key、真实合同、个人信息或未脱敏截图。
- [ ] 新增/修改规则、法条、评测标签时，同步更新校验脚本与文档。
- [ ] 相关文档已更新（README / docs / API 表）。
- [ ] 本地相关检查通过，CI 预期为绿。
- [ ] 如涉及安全漏洞，未在公开 PR 中披露可利用细节。
