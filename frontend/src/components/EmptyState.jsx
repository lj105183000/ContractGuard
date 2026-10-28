import {
  FileIcon,
  LockIcon,
  ScaleIcon,
  SearchIcon,
  ShieldIcon,
  SparkIcon,
} from "./Icons.jsx";

const STEPS = [
  {
    number: "01",
    title: "上传合同",
    text: "PDF / DOCX / TXT / 图片，扫描件自动 OCR。",
  },
  {
    number: "02",
    title: "多 Agent 初审",
    text: "规则引擎 + 3 个审查员并行工作。",
  },
  {
    number: "03",
    title: "人工复核",
    text: "高风险逐条确认，记录你的判断。",
  },
  {
    number: "04",
    title: "交付报告",
    text: "Word / Markdown / JSON，带法条附录。",
  },
];

export default function EmptyState({ meta }) {
  return (
    <div className="empty-state">
      <div className="hero">
        <p className="eyebrow">MULTI-AGENT · RAG · CONTRACT REVIEW</p>
        <h1>
          把一份采购合同，
          <br />
          变成一张<span>可执行的风险清单</span>。
        </h1>
        <p className="hero-sub">
          ContractGuard 面向没有专职法务的中小企业：自动识别付款、交付、验收、违约、
          管辖等条款风险，给出法条依据与修改建议，再交由你做最终判断。
        </p>
        <div className="hero-chips">
          <span>
            <SparkIcon size={15} /> {meta?.rule_count ?? 25} 条企业规则
          </span>
          <span>
            <ScaleIcon size={15} /> {meta?.legal_count ?? 32} 条法条
          </span>
          <span>
            <ShieldIcon size={15} /> 3 个并行审查 Agent
          </span>
          <span>
            <LockIcon size={15} /> 引用逐字校验
          </span>
        </div>
      </div>

      <div className="workflow-card">
        <div className="workflow-head">
          <span>审查流水线</span>
          <small>LangGraph · Async</small>
        </div>
        <div className="workflow-line" aria-hidden="true">
          <span />
        </div>
        <div className="workflow-steps">
          {STEPS.map((step) => (
            <div key={step.number} className="workflow-step">
              <span>{step.number}</span>
              <strong>{step.title}</strong>
              <small>{step.text}</small>
            </div>
          ))}
        </div>
        <div className="workflow-agents">
          <div>
            <SearchIcon size={16} />
            <span>商务条款审查员</span>
          </div>
          <div>
            <SearchIcon size={16} />
            <span>违约解除审查员</span>
          </div>
          <div>
            <SearchIcon size={16} />
            <span>争议合规审查员</span>
          </div>
        </div>
      </div>

      <div className="empty-note">
        <FileIcon size={17} />
        <span>
          从左侧上传合同，或选择一份内置合成示例开始。所有示例均为虚构数据，不构成法律意见。
        </span>
      </div>
    </div>
  );
}
