export default function StudioPanel({ workflowId, skills, onAction }) {
  const cards = [
    { key: 'tune', icon: '🔬', label: '提取对比', color: 'purple' },
    { key: 'sample', icon: '🎲', label: '抽样验证', color: 'green' },
    { key: 'execute', icon: '⚡', label: '全量执行', color: 'orange' },
    { key: 'skills', icon: '📖', label: 'Skill 预览', color: 'blue' },
    { key: 'graph', icon: '🕸️', label: '知识图谱', color: 'teal' },
    { key: 'report', icon: '📋', label: '报告导出', color: 'pink', disabled: true },
  ];

  return (
    <aside className="panel-right">
      <div className="panel-header">
        <span className="panel-title">Studio</span>
        <button className="btn-icon" title="全屏">⛶</button>
      </div>
      <div className="studio-grid">
        {cards.map(c => (
          <div key={c.key}
            className={`studio-card ${c.color}${c.disabled ? ' disabled' : ''}`}
            onClick={() => !c.disabled && onAction(c.key)}>
            <span className="studio-icon">{c.icon}</span>
            <span className="studio-label">{c.label}</span>
            {c.disabled && <span className="studio-status">即将推出</span>}
            <span className="studio-edit">✏️</span>
          </div>
        ))}
      </div>

      <div className="panel-header" style={{ marginTop: 8, borderTop: '1px solid #e8e0d8', paddingTop: 12 }}>
        <span className="panel-title">已提取技能 ({skills.length})</span>
        <span style={{ fontSize: 11, color: '#80868b' }}>⋮</span>
      </div>
      <div className="skill-list">
        {skills.length > 0
          ? skills.slice(0, 30).map((s, i) => (
            <div key={i} className="skill-list-item">
              <div>
                <div className="sname">{s.name || ''}</div>
                <div className="smeta">{s.domain || ''} · {s.sku_type || ''}</div>
              </div>
            </div>
          ))
          : <div className="empty-hint">尚未提取</div>
        }
      </div>

      <div className="studio-bottom">
        <button className="btn-add" onClick={() => onAction('execute')}>
          💬 添加笔记
        </button>
      </div>
    </aside>
  );
}
