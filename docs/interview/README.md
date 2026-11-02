# ContractGuard 面试演示材料

[![60 秒男声中文配音 Demo](./ContractGuard-demo-poster-960.jpg)](./ContractGuard-demo-60s-1080p.mp4)

**[▶ 观看 60 秒 Demo（1080p，男声中文配音）](./ContractGuard-demo-60s-1080p.mp4)**

- 视频：`ContractGuard-demo-60s-1080p.mp4`，1920×1080 / H.264 + AAC / 60.0s / 约 8 MB（已压缩到 GitHub 免费账号 10 MB 视频附件上限内）/ 男声中文旁白
- 面试 PPT：`ContractGuard-面试演示.pptx`（14 页，第 13 页内嵌视频 + 男声中文旁白）
- PDF 预览：`ContractGuard-面试演示.pdf`（视频页为静态封面帧）
- 讲解提纲：`讲解提纲.md`（15 分钟节奏、60 秒旁白、Q&A 准备）

## 视频内容

选择内置示例合同 → 规则 + 3 个审查 Agent 并行审查 → 风险清单 → 原文证据/法条详情 → 逐条复核 → 合同修订稿 → 报告预览。

界面为 **mock 模式**录屏；live 指标（P 64.9% / 已核实层 P 100% / 模型建议层 P 7.1%）来自 2026-11-02 的落盘评测结果，两者不混用。

## 在 GitHub 中展示

### 方式一：README 顶部内联视频（当前采用）

根目录 README 顶部使用 GitHub 的 user-attachments 视频附件，打开页面即可看到原生播放器并点击播放。

- GitHub 只会把**单独一行、前后空行**的 `https://github.com/user-attachments/assets/...` 裸链接渲染成播放器；不要用 `<video>` 包装，也不要改成 Markdown 链接。
- 附件必须通过 GitHub 网页编辑器拖拽上传生成（没有 API/CLI）；提交后才会公开。
- 免费账号视频上限 10 MB，当前 MP4 已压缩到约 8 MB；付费账号上限 100 MB。
- 更新视频后需要重新拖拽上传，旧附件 URL 不会自动指向新文件。

可复现的压缩命令（保持 1080p/25fps，faststart）：

```bash
ffmpeg -y -i ContractGuard-demo-60s-1080p.mp4 \
  -c:v libx264 -preset slow -crf 21 -pix_fmt yuv420p -profile:v high \
  -c:a copy -movflags +faststart output.mp4
```

### 方式二：GitHub Pages 独立播放页（兜底）

本目录提供 `index.html` 播放页。GitHub Pages（Source 选择 `/docs`）启用后访问：

```text
https://<your-name>.github.io/<repo>/interview/
```

即可看到带播放器、封面、下载链接和一个简短说明的展示页；适合非 github.com 镜像或附件不可用时兜底。

### 方式三：封面 + 链接（其它平台）

`ContractGuard-demo-poster-960.jpg` 可作为封面图，链接到 MP4 或 Pages 播放页。GitHub 仓库内的 MP4 链接只会打开文件页/下载，不会内联播放。

## 文件体积

| 文件 | 大小 | 说明 |
|---|---:|---|
| `ContractGuard-demo-60s-1080p.mp4` | ~8 MB | 已压缩到 GitHub 免费账号 10 MB 视频附件上限内；页面/仓库下载均可用 |
| `ContractGuard-demo-poster-960.jpg` | ~90 KB | 文档/兜底封面 |
| `ContractGuard-demo-poster.png` | ~1.1 MB | PPT/PDF 视频封面帧 |
| `ContractGuard-面试演示.pptx` | ~14 MB | 14 页，含内嵌视频 |
| `ContractGuard-面试演示.pdf` | ~2.4 MB | 静态预览 |
