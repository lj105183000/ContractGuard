import { buildErrorGuidance } from "../src/errorGuidance.js";

const cases = [
  {
    name: "OCR page limit",
    info: {
      message:
        "扫描件中需要 OCR 的页面有 51 页，超过上限 50 页。请拆分文件，或调高 OCR_MAX_PAGES。",
      source: "job",
    },
    title: "扫描件页数超过上限",
  },
  {
    name: "OCR no text (image)",
    info: {
      message: "图片中没有识别到文字。请上传更清晰、端正的扫描件。",
      source: "job",
    },
    title: "扫描件未识别出文字",
    actionIncludes: "重新拍摄/扫描",
  },
  {
    name: "OCR no text (PDF)",
    info: {
      message:
        "扫描件 OCR 未识别出有效文字。请上传更清晰、端正的扫描件，或改用可复制文字的 PDF/DOCX。",
      source: "job",
    },
    title: "扫描件未识别出文字",
  },
  {
    name: "OCR unavailable",
    info: {
      message:
        "OCR 组件未安装或加载失败：No module named rapidocr。请运行 uv sync 或重新安装桌面版。",
      source: "job",
    },
    title: "OCR 组件不可用",
    actionIncludes: "uv sync",
  },
  {
    name: "OCR processing failure",
    info: {
      message: "扫描件 OCR 失败：cannot open document",
      source: "job",
    },
    title: "扫描件处理失败",
  },
  {
    name: "image OCR failure",
    info: {
      message: "图片读取或 OCR 失败：broken image",
      source: "job",
    },
    title: "扫描件处理失败",
  },
  {
    name: "encrypted PDF",
    info: { message: "PDF 已加密，无法读取。", source: "job" },
    title: "PDF 已加密",
  },
  {
    name: "upload 413",
    info: {
      message: "文件超过 50MB 限制。",
      source: "start",
      status: 413,
    },
    title: "文件超过上传上限",
    actionIncludes: "MAX_UPLOAD_MB",
  },
  {
    name: "unsupported type",
    info: {
      message: "不支持的文件类型 .exe；请上传 PDF、DOCX、TXT 或 JPG/PNG 图片。",
      source: "start",
      status: 400,
    },
    title: "文件类型不支持",
  },
  {
    name: "route unavailable",
    info: {
      message:
        "模型路由不可用：默认回退链没有可用 Provider：缺少 DEEPSEEK_API_KEY",
      source: "start",
      status: 400,
    },
    title: "所选模型路由不可用",
    actionIncludes: "审查模型",
  },
  {
    name: "route not found",
    info: {
      message: "模型路由不存在：missing-route",
      source: "start",
      status: 400,
    },
    title: "模型路由不存在",
  },
  {
    name: "backend offline",
    info: { message: "Failed to fetch", source: "network" },
    title: "无法连接本地后端",
  },
  {
    name: "vite proxy 500",
    info: {
      message: "请求失败（HTTP 500）",
      source: "start",
      status: 500,
    },
    title: "无法连接本地后端",
  },
  {
    name: "internal error",
    info: { message: "服务内部错误：boom", source: "job" },
    title: "本地服务出错",
  },
  {
    name: "job expired",
    info: { message: "任务不存在或已过期。", source: "job" },
    title: "任务已过期",
  },
  {
    name: "PDF read failure",
    info: { message: "PDF 读取失败：EOF", source: "job" },
    title: "无法读取合同内容",
  },
  {
    name: "PDF no pages",
    info: { message: "PDF 没有页面。", source: "job" },
    title: "无法读取合同内容",
  },
  {
    name: "no clauses",
    info: {
      message: "无法从合同中切分出条款，请检查文档内容。",
      source: "job",
    },
    title: "无法读取合同内容",
  },
  {
    name: "internal error with OCR text",
    info: {
      message: "服务内部错误：扫描件 OCR 未识别",
      source: "job",
    },
    title: "本地服务出错",
  },
  {
    name: "unknown 500",
    info: { message: "weird failure", source: "start", status: 500 },
    title: "后端返回异常",
  },
  {
    name: "unknown network",
    info: { message: "weird failure", source: "network" },
    title: "无法连接本地后端",
  },
  {
    name: "custom limits interpolation",
    info: {
      message: "图片中没有识别到文字。请上传更清晰、端正的扫描件。",
      source: "job",
    },
    context: { maxUploadMb: 20, ocrMaxPages: 12 },
    title: "扫描件未识别出文字",
    actionIncludes: "12",
  },
  {
    name: "unknown fallback",
    info: { message: "某种未知错误", source: "job" },
    title: "审查未能完成",
    generic: true,
  },
];

let failed = 0;
for (const testCase of cases) {
  const guidance = buildErrorGuidance(
    testCase.info,
    testCase.context ?? { maxUploadMb: 50, ocrMaxPages: 50 },
  );
  const generic = testCase.generic ?? false;
  const actionOk =
    !testCase.actionIncludes ||
    guidance.actions.some((action) => action.includes(testCase.actionIncludes));
  if (
    guidance.title !== testCase.title ||
    guidance.showGenericHint !== generic ||
    !actionOk
  ) {
    failed += 1;
    console.error(`FAIL ${testCase.name}:`, guidance);
  } else {
    console.log(`ok   ${testCase.name}: ${guidance.title}`);
  }
}

if (failed) {
  console.error(`${failed} error guidance case(s) failed`);
  process.exit(1);
}
console.log(`error guidance: ${cases.length} cases passed`);
