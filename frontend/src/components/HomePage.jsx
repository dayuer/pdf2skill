import { useState, useEffect } from 'react';

const AVATARS = ['📄', '📑', '📋', '📊', '📈', '🏥', '🚗', '💼', '🎓', '📚'];

function getAvatar(name) {
  const idx = (name || '').length % AVATARS.length;
  return AVATARS[idx];
}

function formatDate(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d)) return ts;
  return d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
}

export default function HomePage({ onOpen, onNew }) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState('list'); // 'grid' | 'list'
  const [tab, setTab] = useState('mine');

  useEffect(() => {
    fetch('/api/sessions')
      .then(r => r.json())
      .then(data => { setSessions(data || []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const tabs = [
    { key: 'all', label: '全部' },
    { key: 'mine', label: '我的工作流' },
    { key: 'featured', label: '精选工作流' },
    { key: 'shared', label: '与我共享' },
  ];

  return (
    <div className="home">
      {/* 顶栏 */}
      <header className="home-topbar">
        <div className="home-topbar-left">
          <span className="home-logo-icon">◉</span>
          <span className="home-logo-text">pdf2skill</span>
        </div>
        <div className="home-topbar-right">
          <button className="btn btn-ghost btn-sm">⚙ 设置</button>
          <span className="tag">ULTRA</span>
          <div className="home-avatar">👤</div>
        </div>
      </header>

      {/* 标签栏 + 视图切换 */}
      <div className="home-toolbar">
        <div className="home-tabs">
          {tabs.map(t => (
            <button key={t.key}
              className={`home-tab${tab === t.key ? ' active' : ''}`}
              onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
        <div className="home-view-controls">
          <button className={`view-btn${view === 'grid' ? ' active' : ''}`}
            onClick={() => setView('grid')} title="网格视图">⊞</button>
          <button className={`view-btn${view === 'list' ? ' active' : ''}`}
            onClick={() => setView('list')} title="列表视图">☰</button>
          <button className="btn btn-ghost btn-sm" style={{ marginLeft: 8 }}>最近 ▾</button>
          <button className="btn btn-primary" style={{ marginLeft: 8 }} onClick={onNew}>
            ＋ 新建
          </button>
        </div>
      </div>

      {/* 页面标题 */}
      <div className="home-content">
        <h2 className="home-title">我的工作流</h2>

        {loading && (
          <div className="loading-text"><div className="spinner" /><span>加载中…</span></div>
        )}

        {/* 列表视图 */}
        {!loading && view === 'list' && (
          <div className="home-list">
            <div className="home-list-header">
              <span className="col-title">标题</span>
              <span className="col-source">来源</span>
              <span className="col-date">创建日期</span>
              <span className="col-role">角色</span>
              <span className="col-action"></span>
            </div>
            {sessions.length === 0 && (
              <div className="home-empty">
                <div style={{ fontSize: 48, marginBottom: 12 }}>📚</div>
                还没有工作流，点击右上角「＋ 新建」开始
              </div>
            )}
            {sessions.map(s => (
              <div key={s.session_id} className="home-list-row"
                onClick={() => onOpen(s.session_id)}>
                <span className="col-title">
                  <span className="row-avatar">{getAvatar(s.doc_name)}</span>
                  <span className="row-name">{s.doc_name || s.session_id}</span>
                </span>
                <span className="col-source">{s.filtered_chunks || s.total_chunks || 0} 个来源</span>
                <span className="col-date">{formatDate(s.created_at)}</span>
                <span className="col-role">Owner</span>
                <span className="col-action">⋮</span>
              </div>
            ))}
          </div>
        )}

        {/* 网格视图 */}
        {!loading && view === 'grid' && (
          <div className="home-grid">
            {sessions.length === 0 && (
              <div className="home-empty" style={{ gridColumn: '1 / -1' }}>
                <div style={{ fontSize: 48, marginBottom: 12 }}>📚</div>
                还没有工作流
              </div>
            )}
            {sessions.map(s => (
              <div key={s.session_id} className="home-grid-card"
                onClick={() => onOpen(s.session_id)}>
                <div className="grid-card-icon">{getAvatar(s.doc_name)}</div>
                <div className="grid-card-name">{s.doc_name || s.session_id}</div>
                <div className="grid-card-meta">
                  {s.filtered_chunks || 0} 个来源 · {formatDate(s.created_at)}
                </div>
                <div className="grid-card-footer">
                  <span className="grid-card-status">
                    {s.skills_on_disk > 0 ? `${s.skills_on_disk} Skills` : '未提取'}
                  </span>
                  <span>⋮</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
