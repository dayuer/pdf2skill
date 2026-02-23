import { useState, useRef, useCallback } from 'react';

export default function SourcePanel({
  meta, chunks, selectedChunk, loading,
  systemPrompt, promptHint,
  onUpload, onSearch, onRechunk, onSelectChunk,
  onSystemPromptChange, onPromptHintChange, onSaveSettings, onSaveSystemPrompt,
}) {
  const fileRef = useRef();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [searchTimer, setSearchTimer] = useState(null);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    if (e.dataTransfer.files[0]) onUpload(e.dataTransfer.files[0]);
  }, [onUpload]);

  const handleSearch = (val) => {
    clearTimeout(searchTimer);
    setSearchTimer(setTimeout(() => onSearch(val || undefined), 300));
  };

  const allTypes = ['技术手册','叙事类','方法论','学术教材','操作规范','保险合同','行业报告','医学法律'];
  if (meta?.book_type && !allTypes.includes(meta.book_type)) allTypes.push(meta.book_type);

  return (
    <aside className="panel-left">
      <div className="panel-header">
        <span className="panel-title">来源</span>
        <button className="btn-icon" title="管理来源">☰</button>
      </div>

      {/* 添加来源 */}
      <div className="upload-zone" onClick={() => fileRef.current?.click()}
        onDragOver={e => e.preventDefault()} onDrop={handleDrop}>
        <span>＋</span>
        <span>{meta ? '添加来源' : '添加来源'}</span>
      </div>
      <input ref={fileRef} type="file" accept=".pdf,.txt,.epub,.md" style={{ display: 'none' }}
        onChange={e => e.target.files[0] && onUpload(e.target.files[0])} />

      {/* 搜索 */}
      <div className="search-box">
        <span className="search-icon">🔍</span>
        <input placeholder="搜索来源…" onChange={e => handleSearch(e.target.value)} />
      </div>

      {loading.upload && (
        <div className="loading-text"><div className="spinner" /><span>正在分析文档…</span></div>
      )}

      {/* 文档摘要 */}
      {meta && (
        <div className="doc-summary">
          <div className="row">
            <span className="label">类型</span><span className="val">{meta.format?.toUpperCase()}</span>
            <span className="label">领域</span><span className="val">{(meta.domains || []).join(', ')}</span>
          </div>
          <div className="row">
            <span className="label">块数</span><span className="val">{meta.filtered_chunks} / {meta.total_chunks}</span>
          </div>
          <div className="summary-tags">
            {(meta.core_components || []).map((c, i) => <span key={i} className="summary-tag">{c}</span>)}
            {(meta.skill_types || []).map((c, i) => <span key={`st-${i}`} className="summary-tag green">{c}</span>)}
          </div>
          <select className="setting-select" style={{ marginTop: 8 }} value={meta.book_type || ''}
            onChange={e => onSaveSettings({ book_type: e.target.value })}>
            {allTypes.map(t => <option key={t}>{t}</option>)}
          </select>
        </div>
      )}

      {/* 来源列表 */}
      {meta && (
        <div className="source-list">
          <div className="chunk-header-row">
            <span className="chunk-count">选择所有来源</span>
            <button className="btn btn-ghost btn-sm" onClick={onRechunk} style={{ fontSize: 11, padding: '3px 8px' }}>🔄 重切</button>
          </div>
          <div className="chunk-list">
            {(chunks.items || []).map(c => (
              <div key={c.index} className={`chunk-item${c.index === selectedChunk ? ' selected' : ''}`}
                onClick={() => onSelectChunk(c.index)}>
                <div style={{ flex: 1, overflow: 'hidden' }}>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{(c.heading_path || []).join(' > ') || `chunk #${c.index}`}</div>
                  <div style={{ fontSize: 11, color: '#80868b', marginTop: 2 }}>{c.preview?.substring(0, 60)}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 折叠设置 */}
      {meta && (
        <div className="settings-area">
          <div className="collapsible-header" onClick={() => setSettingsOpen(!settingsOpen)}>
            <span>⚙️ 提取设置</span>
            <span className={`arrow${settingsOpen ? ' open' : ''}`}>▶</span>
          </div>
          <div className={`collapsible-body${settingsOpen ? ' open' : ''}`}>
            <div className="prompt-label">
              系统 Prompt
              <button className="btn btn-ghost btn-sm" onClick={onSaveSystemPrompt}
                style={{ padding: '2px 8px', fontSize: 10 }}>保存</button>
            </div>
            <textarea className="prompt-textarea" value={systemPrompt} style={{ minHeight: 80 }}
              onChange={e => onSystemPromptChange(e.target.value)} />
            <div className="prompt-label">提取策略</div>
            <textarea className="prompt-textarea" value={promptHint} placeholder="加载中..."
              onChange={e => onPromptHintChange(e.target.value)} />
          </div>
        </div>
      )}
    </aside>
  );
}
