import { useState } from "react";
import { ShieldIcon } from "./Icons.jsx";

export default function TokenGate({ onAuthenticated }) {
  const [token, setToken] = useState("");

  const submit = (event) => {
    event.preventDefault();
    const value = token.trim();
    if (!value) return;
    window.localStorage.setItem("contractguard.apiToken", value);
    onAuthenticated?.();
  };

  return (
    <div className="token-gate">
      <form className="card token-card" onSubmit={submit}>
        <span className="brand-mark">
          <ShieldIcon size={26} />
        </span>
        <p className="eyebrow">AUTHENTICATION</p>
        <h1>需要 API Token</h1>
        <p className="hint">
          后端启用了单租户访问令牌。输入管理员提供的 Token 后继续；Token
          只保存在当前浏览器的 localStorage。
        </p>
        <label className="note-label">
          API Token
          <input
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="粘贴 API Token"
            autoFocus
          />
        </label>
        <button type="submit" className="btn btn-primary" disabled={!token.trim()}>
          进入 ContractGuard
        </button>
        <p className="hint">
          如果 Token 无效，页面会重新显示此提示。桌面端可在
          `%APPDATA%\ContractGuard\.env` 中配置后端 Token；本页输入仅影响当前浏览器。
        </p>
      </form>
    </div>
  );
}
