import { useState, memo } from 'react';

/* ══════ n8n NDV (Node Detail View) — 侧抽屉参数编辑器 ══════
 *
 * 对标 n8n 的 NDV: 点击节点后从右侧滑入的参数编辑面板。
 * 三个标签页: Parameters / Input / Output
 */

const TABS = [
  { key: 'params', label: '⚙ 参数', icon: '⚙' },
  { key: 'input', label: '↙ 输入', icon: '↙' },
  { key: 'output', label: '↗ 输出', icon: '↗' },
];

/* ── 参数渲染器（升级版） ── */
function ParamField({ prop, value, onChange }) {
  if (prop.type === 'code') {
    return (
      <div className="ndv-field">
        <label className="ndv-label">{prop.displayName}</label>
        <textarea className="ndv-code" value={value || ''} rows={6}
          placeholder={`输入 ${prop.displayName}...`}
          onChange={e => onChange(prop.name, e.target.value)} />
      </div>
    );
  }
  if (prop.type === 'options') {
    return (
      <div className="ndv-field">
        <label className="ndv-label">{prop.displayName}</label>
        <select className="ndv-select" value={value ?? prop.default}
          onChange={e => onChange(prop.name, e.target.value)}>
          {prop.options.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    );
  }
  if (prop.type === 'number') {
    return (
      <div className="ndv-field">
        <label className="ndv-label">{prop.displayName}</label>
        <input className="ndv-input" type="number" value={value ?? prop.default}
          onChange={e => onChange(prop.name, parseFloat(e.target.value) || 0)} />
      </div>
    );
  }
  if (prop.type === 'boolean') {
    return (
      <div className="ndv-field ndv-field-row">
        <label className="ndv-label">{prop.displayName}</label>
        <label className="ndv-toggle">
          <input type="checkbox" checked={!!value}
            onChange={e => onChange(prop.name, e.target.checked)} />
          <span className="ndv-toggle-slider" />
        </label>
      </div>
    );
  }
  // 默认: text
  return (
    <div className="ndv-field">
      <label className="ndv-label">{prop.displayName}</label>
      <input className="ndv-input" type="text" value={value || ''}
        placeholder={`输入 ${prop.displayName}...`}
        onChange={e => onChange(prop.name, e.target.value)} />
    </div>
  );
}

/* ── JSON 数据查看器 ── */
function DataViewer({ data, emptyText = '暂无数据' }) {
  if (!data) return <div className="ndv-empty">{emptyText}</div>;

  // 尝试友好展示
  const items = Array.isArray(data) ? data : [data];
  return (
    <div className="ndv-data-viewer">
      <div className="ndv-data-count">{items.length} 条记录</div>
      {items.map((item, i) => (
        <pre key={i} className="ndv-data-item">
          {typeof item === 'string' ? item : JSON.stringify(item, null, 2)}
        </pre>
      ))}
    </div>
  );
}

/* ══════ 主组件 ══════ */
export default memo(function NodeDrawer({
  node,         // 当前选中的节点
  onClose,      // 关闭回调
  onConfigChange, // 参数变更
  onRunNode,    // 执行单节点
  onPinData,    // 固定数据
  inputData,    // 上游输入数据
  outputData,   // 节点输出数据
}) {
  const [activeTab, setActiveTab] = useState('params');

  if (!node) return null;

  const { data } = node;
  const statusMap = {
    idle: { cls: 'idle', text: '待执行', color: '#80868b' },
    running: { cls: 'running', text: '执行中…', color: '#1a73e8' },
    done: { cls: 'done', text: '✓ 完成', color: '#137333' },
    success: { cls: 'done', text: '✓ 完成', color: '#137333' },
    error: { cls: 'error', text: '✗ 失败', color: '#c5221f' },
  };
  const s = statusMap[data.status] || statusMap.idle;

  return (
    <div className="ndv-overlay" onClick={onClose}>
      <div className="ndv-drawer" onClick={e => e.stopPropagation()}>
        {/* 头部 */}
        <div className="ndv-header">
          <div className="ndv-header-left">
            <span className="ndv-icon">{data.icon}</span>
            <div>
              <div className="ndv-title">{data.label}</div>
              <div className="ndv-subtitle">{data.desc}</div>
            </div>
          </div>
          <div className="ndv-header-right">
            <span className={`ndv-status ${s.cls}`}>{s.text}</span>
            <button className="ndv-close" onClick={onClose}>✕</button>
          </div>
        </div>

        {/* 标签栏 */}
        <div className="ndv-tabs">
          {TABS.map(t => (
            <button key={t.key}
              className={`ndv-tab${activeTab === t.key ? ' active' : ''}`}
              onClick={() => setActiveTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>

        {/* 内容区 */}
        <div className="ndv-body">
          {activeTab === 'params' && (
            <div className="ndv-params">
              <div className="ndv-type-badge">{data.type}</div>
              {(data.properties || []).map(prop => (
                <ParamField key={prop.name} prop={prop}
                  value={data.config?.[prop.name]}
                  onChange={(name, val) => onConfigChange?.(node.id, name, val)} />
              ))}
              <div className="ndv-actions">
                {onRunNode && (
                  <button className="btn btn-primary btn-sm ndv-run"
                    onClick={() => onRunNode(node.id)}>
                    ▶ 执行此节点
                  </button>
                )}
              </div>
            </div>
          )}

          {activeTab === 'input' && (
            <DataViewer data={inputData} emptyText="执行后可查看输入数据" />
          )}

          {activeTab === 'output' && (
            <div>
              <DataViewer data={outputData} emptyText="执行后可查看输出数据" />
              {onPinData && outputData && (
                <button className="btn btn-ghost btn-sm ndv-pin"
                  onClick={() => onPinData(node.id, outputData)}>
                  📌 固定数据 (Pin)
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
});
