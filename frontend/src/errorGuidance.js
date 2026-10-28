const OCR_PAGE_LIMIT = /需要 OCR 的页面|OCR.*超过上限|超过上限.*OCR/;
const OCR_UNAVAILABLE =
  /OCR 组件未安装|OCR 组件加载失败|OCR 模型初始化失败|OCR 组件不可用|运行 uv sync/;
const OCR_NO_TEXT =
  /OCR 未识别出有效文字|没有识别到文字|没有可提取的文字|未识别出文字/;
const PDF_ENCRYPTED = /已加密/;
const FILE_TOO_LARGE = /文件超过\s*\d+\s*MB|超过\s*\d+\s*MB\s*限制/;
const UNSUPPORTED_TYPE = /不支持的文件类型|只支持\s*PDF/;
const EMPTY_FILE = /上传文件为空|内容为空或过短|中没有可读取的文字/;
const ROUTE_UNAVAILABLE = /模型路由不可用/;
const ROUTE_NOT_FOUND = /模型路由不存在/;
const JOB_GONE = /任务不存在或已过期/;
const NETWORK_ERROR =
  /Failed to fetch|NetworkError|Load failed|请求失败（HTTP 500）|无法获取任务状态|无法连接后端/;

const LOG_PATHS = {
  dev: "%APPDATA%\\ContractGuard Dev\\logs",
  packaged: "%APPDATA%\\ContractGuard\\logs",
};

function result(title, cause, actions, showGenericHint = false) {
  return { title, cause, actions, showGenericHint };
}

/**
 * 把真实错误归一化成错误页的标题、原因和操作建议。
 * 已知错误不再显示固定「常见原因」列表；只有未知错误才使用兜底提示。
 */
export function buildErrorGuidance(
  errorInfo,
  { maxUploadMb = 50, ocrMaxPages = 50 } = {},
) {
  const message = String(errorInfo?.message || "").trim();
  const source = errorInfo?.source || "";
  const status = errorInfo?.status ?? null;

  // 真实内部异常的正文可能包含 OCR/PDF 字样，必须先于业务文案匹配。
  if (/^服务内部错误/.test(message) || /文件保存失败/.test(message)) {
    return result(
      "本地服务出错",
      message,
      [
        "查看后端日志后重试。",
        "仍失败请把日志中的错误信息反馈给开发者。",
      ],
    );
  }

  if (OCR_PAGE_LIMIT.test(message)) {
    return result(
      "扫描件页数超过上限",
      "需要 OCR 的页面超过了当前上限。",
      [
        "拆分文件后重新上传。",
        `或调高 OCR_MAX_PAGES（当前 ${ocrMaxPages}）后重启后端。`,
      ],
    );
  }

  if (OCR_UNAVAILABLE.test(message)) {
    return result(
      "OCR 组件不可用",
      "本地 OCR 依赖或模型没有正确加载。",
      [
        "在 backend 目录运行 uv sync 安装 OCR 依赖后重启后端。",
        "桌面版请重新安装安装包。",
        `仍失败请查看后端日志（开发版 ${LOG_PATHS.dev}，安装版 ${LOG_PATHS.packaged}）。`,
      ],
    );
  }

  if (OCR_NO_TEXT.test(message)) {
    return result(
      "扫描件未识别出文字",
      "OCR 没有从文件中提取到有效文字，常见于模糊、倾斜、手写、阴影或光照不足。",
      [
        "重新拍摄/扫描：文件放正、光线均匀、避免阴影和反光，尽量使用 200 DPI 以上。",
        "如果只有部分页面是扫描件，改用可复制文字的 PDF 或 DOCX。",
        `单次最多 OCR ${ocrMaxPages} 页；页数较多时请拆分文件。`,
      ],
    );
  }

  if (/扫描件 OCR 失败|图片读取或 OCR 失败/.test(message)) {
    return result(
      "扫描件处理失败",
      "文件渲染或 OCR 过程出错，文件可能损坏或格式异常。",
      [
        "重试一次；或改用可复制文字的 PDF/DOCX。",
        `仍失败请查看后端日志（开发版 ${LOG_PATHS.dev}，安装版 ${LOG_PATHS.packaged}）。`,
      ],
    );
  }

  if (PDF_ENCRYPTED.test(message)) {
    return result(
      "PDF 已加密",
      "文件需要密码才能读取。",
      ["解除 PDF 密码后重新上传。"],
    );
  }

  if (status === 413 || FILE_TOO_LARGE.test(message)) {
    const matched = message.match(/文件超过\s*(\d+)\s*MB/);
    const limit = matched ? Number(matched[1]) : maxUploadMb;
    return result(
      "文件超过上传上限",
      `文件大小超过 ${limit}MB 限制。`,
      [
        "压缩文件或拆分为多份后重新上传。",
        `或调高 MAX_UPLOAD_MB（当前 ${maxUploadMb}MB）后重启后端。`,
      ],
    );
  }

  if (UNSUPPORTED_TYPE.test(message)) {
    return result(
      "文件类型不支持",
      "只接受 PDF、DOCX、TXT、JPG、PNG。",
      ["请转换文件格式后重新上传。"],
    );
  }

  if (EMPTY_FILE.test(message)) {
    return result(
      "文件内容为空",
      "文件没有可读取的文字。",
      ["重新选择包含内容的文件。"],
    );
  }

  if (ROUTE_UNAVAILABLE.test(message)) {
    return result(
      "所选模型路由不可用",
      message,
      [
        "在「审查模型」下拉中换一个可用路由。",
        "补齐对应 Provider 的 API Key，或在 llm_profiles.yaml 启用本地模型后重启。",
      ],
    );
  }

  if (ROUTE_NOT_FOUND.test(message)) {
    return result(
      "模型路由不存在",
      message,
      ["刷新页面后重新选择有效路由。"],
    );
  }

  if (JOB_GONE.test(message)) {
    return result(
      "任务已过期",
      "后端重启或任务超过 1 小时会清空内存中的任务。",
      ["请重新上传合同。"],
    );
  }

  if (/PDF 读取失败|PDF 没有页面|无法从合同中切分出条款|文件不存在/.test(message)) {
    return result(
      "无法读取合同内容",
      "文件可能损坏、为空，或没有可提取的条款。",
      [
        "用 Office/WPS 重新导出 PDF/DOCX 后重试。",
        "确认文件非空且不是只有图片的扫描件。",
      ],
    );
  }

  if (source === "network" || NETWORK_ERROR.test(message)) {
    return result(
      "无法连接本地后端",
      "页面无法访问本地 API；后端可能未启动、正在重启，或代理返回了没有详情的 500。",
      [
        "启动后端或桌面应用后重试。",
        `开发版日志：${LOG_PATHS.dev}；安装版：${LOG_PATHS.packaged}。`,
        "开发模式确认 uv sync 成功、Vite proxy 指向 127.0.0.1:8000；桌面端可从托盘退出后重新启动。",
      ],
    );
  }

  if (typeof status === "number" && status >= 500) {
    return result(
      "后端返回异常",
      "服务端处理请求时出错。",
      ["重试；若持续失败请查看后端日志。"],
    );
  }

  return result(
    "审查未能完成",
    message || "请重试。",
    [],
    true,
  );
}
