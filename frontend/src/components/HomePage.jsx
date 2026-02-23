import { useState, useEffect } from 'react';

const AVATARS = ['📄', '📑', '📋', '📊', '📈', '🏥', '🚗', '💼', '🎓', '📚'];

function getAvatar(name) {
  const idx = (name || '').length % AVATARS.length;
  return AVATARS[idx];
}

function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  if (isNaN(d)) return '';
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
}

export default function HomePage({ onOpen, onNew }) {
  const [workflows, setWorkflows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState('list');
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);

  const loadWorkflows = () => {
    setLoading(true);
    fetch('/api/workflows')
      .then(r => r.json())
      .then(data => { setWorkflows(data || []); setLoading(false); })
      .catch(() => setLoading(false));
  };

  useEffect(loadWorkflows, []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      const r = await fetch(`/api/workflow/create?name=${encodeURIComponent(newName.trim())}`, { method: 'POST' });
      const data = await r.json();
      setShowCreate(false);
      setNewName('');
      onOpen(data.workflow_id);
    } catch {
      alert('创建失败');
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="home">
      <header className="home-topbar">
        <div className="home-topbar-left">
          <span className="home-logo-icon">◉</span>
          <span className="home-logo-text">pdf2skill</span>
        </div>
        <div className="home-topbar-right">
          <span className="tag">ULTRA</span>
          <div className="home-avatar">👤</div>
        </div>
      </header>

      <div className="home-toolbar">
        <div className="home-tabs">
          <button className="home-tab active">我的工作流</button>
        </div>
        <div className="home-view-controls">
          <button className={`view-btn${view === 'grid' ? ' active' : ''}`}
            onClick={() => setView('grid')} title="网格视图">⊞</button>
          <button className={`view-btn${view === 'list' ? ' active' : ''}`}
            onClick={() => setView('list')} title="列表视图">☰</button>
          <button className="btn btn-primary" style={{ marginLeft: 8 }}
            onClick={() => setShowCreate(true)}>
            ＋ 新建工作流
          </button>
        </div>
      </div>

      {/* 新建工作流对话框 */}
      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal-card" onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: '0 0 16px' }}>新建工作流</h3>
            <input
              className="modal-input"
              placeholder="输入工作流名称…"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreate()}
              autoFocus
            />
            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setShowCreate(false)}>取消</button>
              <button className="btn btn-primary" onClick={handleCreate} disabled={creating || !newName.trim()}>
                {creating ? '创建中…' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="home-content">
        <h2 className="home-title">我的工作流</h2>

        {loading && (
          <div className="loading-text"><div className="spinner" /><span>加载中…</span></div>
        )}

        {/* 列表视图 */}
        {!loading && view === 'list' && (
          <div className="home-list">
            <div className="home-list-header">
              <span className="col-title">名称</span>
              <span className="col-source">文件 / 分块</span>
              <span className="col-date">创建日期</span>
              <span className="col-action"></span>
            </div>
            {workflows.length === 0 && (
              <div className="home-empty">
                <div style={{ fontSize: 48, marginBottom: 12 }}>📚</div>
                还没有工作流，点击「＋ 新建工作流」开始
              </div>
            )}
            {workflows.map(w => (
              <div key={w.workflow_id} className="home-list-row"
                onClick={() => onOpen(w.workflow_id)}>
                <span className="col-title">
                  <span className="row-avatar">{getAvatar(w.name || w.doc_name)}</span>
                  <span className="row-name">{w.name || w.doc_name || w.workflow_id}</span>
                </span>
                <span className="col-source">
                  {w.uploads?.length || 0} 个文件 · {w.filtered_chunks || 0} 块
                </span>
                <span className="col-date">{formatDate(w.created_at)}</span>
                <span className="col-action">›</span>
              </div>
            ))}
          </div>
        )}

        {/* 网格视图 */}
        {!loading && view === 'grid' && (
          <div className="home-grid">
            {workflows.length === 0 && (
              <div className="home-empty" style={{ gridColumn: '1 / -1' }}>
                <div style={{ fontSize: 48, marginBottom: 12 }}>📚</div>
                还没有工作流
              </div>
            )}
            {workflows.map(w => (
              <div key={w.workflow_id} className="home-grid-card"
                onClick={() => onOpen(w.workflow_id)}>
                <div className="grid-card-icon">{getAvatar(w.name || w.doc_name)}</div>
                <div className="grid-card-name">{w.name || w.doc_name || w.workflow_id}</div>
                <div className="grid-card-meta">
                  {w.uploads?.length || 0} 个文件 · {w.filtered_chunks || 0} 块
                  · {formatDate(w.created_at)}
                </div>
                <div className="grid-card-footer">
                  <span className="grid-card-status">
                    {w.skills_on_disk > 0 ? `${w.skills_on_disk} Skills` : '未提取'}
                  </span>
                  <span>›</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
