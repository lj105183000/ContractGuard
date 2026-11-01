import React from "react";

/**
 * 全局渲染错误兜底。React 只能通过 class 组件捕获子树渲染异常。
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
    this.handleReload = this.handleReload.bind(this);
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // 保留控制台堆栈，便于用户/开发者排查；不把内部细节渲染到页面上。
    console.error("[ContractGuard] render error", error, info);
  }

  handleReload() {
    window.location.reload();
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="error-boundary">
        <div className="card error-boundary-card">
          <p className="eyebrow">RENDER ERROR</p>
          <h1>页面渲染失败</h1>
          <p className="hint">
            当前页面遇到未处理的异常。可以刷新后重试；如果问题持续，请查看桌面端日志或联系维护者。
          </p>
          <button type="button" className="btn btn-primary" onClick={this.handleReload}>
            刷新页面
          </button>
        </div>
      </div>
    );
  }
}
