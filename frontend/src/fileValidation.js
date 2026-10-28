export const ACCEPT = ".pdf,.docx,.txt,.jpg,.jpeg,.png";
export const DEFAULT_MAX_MB = 50;

const ALLOWED = [".pdf", ".docx", ".txt", ".jpg", ".jpeg", ".png"];

export function validateContractFile(file, maxMb = DEFAULT_MAX_MB) {
  if (!file) return { ok: false, message: "没有选择文件。" };
  const name = file.name || "";
  const suffix = name.toLowerCase().slice(name.lastIndexOf("."));
  if (!ALLOWED.includes(suffix)) {
    return { ok: false, message: "只支持 PDF、DOCX、TXT、JPG、PNG 文件。" };
  }
  if (file.size > maxMb * 1024 * 1024) {
    return { ok: false, message: `文件超过 ${maxMb}MB 限制。` };
  }
  return { ok: true, message: "" };
}
