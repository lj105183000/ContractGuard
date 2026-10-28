import { useEffect } from "react";
import { AlertIcon, CheckIcon, XIcon } from "./Icons.jsx";

export default function Toast({ toast, onClose }) {
  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(onClose, 4200);
    return () => window.clearTimeout(timer);
  }, [toast, onClose]);

  if (!toast) return null;
  const Icon = toast.type === "error" ? AlertIcon : CheckIcon;
  return (
    <div className={`toast toast-${toast.type || "success"}`} role="status">
      <Icon size={18} />
      <div>
        <strong>{toast.message}</strong>
        {toast.detail ? <span>{toast.detail}</span> : null}
      </div>
      <button type="button" className="toast-close" onClick={onClose} aria-label="关闭提示">
        <XIcon size={14} />
      </button>
    </div>
  );
}
