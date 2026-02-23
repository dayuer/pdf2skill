import { useState, useRef, useCallback } from 'react';

const FILE_ACCEPT = '.pdf,.txt,.epub,.md,.docx,.doc,.xlsx,.xls,.csv';

export default function SourcePanel({ meta, chunks, loading, onUpload, onBatchUpload, onStartProcessing, uploadProgress, onSearch, onSelectChunk, selectedChunk }) {
  const fileRef = useRef();
  const [viewingChunk, setViewingChunk] = useState(null);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 1 && onBatchUpload) {
      onBatchUpload(files);
    } else if (files[0]) {
      onUpload(files[0]);
    }
  }, [onUpload, onBatchUpload]);

  const handleFileChange = useCallback((e) => {
    const files = Array.from(e.target.files);
    if (files.length > 1 && onBatchUpload) {
      onBatchUpload(files);
    } else if (files[0]) {
      onUpload(files[0]);
    }
    e.target.value = ''; // 允许重复选同一文件
  }, [onUpload, onBatchUpload]);

  const handleChunkClick = (chunk) => {
    setViewingChunk(chunk);
    onSelectChunk?.(chunk.index);
  };

  const handleBack = () => setViewingChunk(null);

  // ── 详情视图 ──
  if (viewingChunk) {
    return (
      <aside className="panel-left">
        <div className="panel-header">
          <button className="btn-icon" onClick={handleBack} title="返回列表">←</button>
          <span className="panel-title" style={{ flex: 1 }}>
            {(viewingChunk.heading_path || []).join(' > ') || `chunk #${viewingChunk.index}`}
          </span>
        </div>
        <div className="chunk-detail">
          <div className="chunk-detail-meta">
            <span className="chunk-detail-tag">chunk #{viewingChunk.index}</span>
            <span className="chunk-detail-tag">{viewingChunk.char_count} 字符</span>
          </div>
          <div className="chunk-detail-text">
            {viewingChunk.text || viewingChunk.preview || '(无内容)'}
          </div>
        </div>
      </aside>
    );
  }

  // ── 列表视图 ──
  return (
    <aside className="panel-left">
      <div className="panel-header">
        <span className="panel-title">来源</span>
        <button className="btn-icon" title="管理来源">☰</button>
      </div>

      {/* 添加来源（支持多文件） */}
      <div className="upload-zone" onClick={() => fileRef.current?.click()}
        onDragOver={e => e.preventDefault()} onDrop={handleDrop}>
        <span>＋</span>
        <span>添加来源（支持多文件）</span>
      </div>
      <input ref={fileRef} type="file" accept={FILE_ACCEPT} multiple style={{ display: 'none' }}
        onChange={handleFileChange} />

      {/* 上传进度 */}
      {uploadProgress && Object.keys(uploadProgress).length > 0 && (
        <div className="upload-progress-list">
          {Object.entries(uploadProgress).filter(([k]) => k !== '__overall__').map(([filename, info]) => (
            <div key={filename} className={`upload-progress-item ${info.status}`}>
              <span className="upload-progress-icon">
                {info.status === 'done' ? '✅' : info.status === 'skipped' ? '⏭' :
                 info.status === 'queued' ? '⏳' : '⚙️'}
              </span>
              <span className="upload-progress-name">{filename}</span>
              <span className="upload-progress-status">{info.message}</span>
            </div>
          ))}
          {uploadProgress?.__overall__?.status === 'done' && (
            <div className="upload-progress-summary">
              ✅ {uploadProgress.__overall__.total_files} 个文件处理完成
              · {uploadProgress.__overall__.filtered_chunks} 个有效分块
            </div>
          )}
        </div>
      )}

      {/* 开始处理按钮 — 上传完成但未处理时显示 */}
      {uploadProgress?.__upload__?.saved?.length > 0 && !uploadProgress?.__overall__ && !loading?.upload && (
        <div style={{ margin: '0 16px 12px' }}>
          <button className="btn-process" onClick={onStartProcessing}>
            ▶ 开始处理 ({uploadProgress.__upload__.saved.length} 个文件)
          </button>
        </div>
      )}

      {/* 搜索 */}
      <div className="search-box">
        <span className="search-icon">🔍</span>
        <input placeholder="在来源中搜索…" onChange={e => {
          const v = e.target.value;
          clearTimeout(window._srcSearchTimer);
          window._srcSearchTimer = setTimeout(() => onSearch?.(v || undefined), 300);
        }} />
      </div>

      {loading?.upload && (
        <div className="loading-text"><div className="spinner" /><span>正在分析文档…</span></div>
      )}

      {/* 来源文件列表 */}
      {meta && (
        <div className="source-list">
          <div className="chunk-header-row">
            <span className="chunk-count">选择所有来源</span>
            <span className="source-check">✔</span>
          </div>

          {/* 主文档 */}
          <div className="source-file-item active">
            <span className="source-file-icon">📄</span>
            <div className="source-file-info">
              <div className="source-file-name">{meta.doc_name || '未命名文档'}</div>
              <div className="source-file-meta">
                {meta.format?.toUpperCase()} · {meta.total_chunks} 个分块 · {(meta.domains || []).join(', ')}
              </div>
            </div>
            <span className="source-check">✔</span>
          </div>

          {/* 分块列表 */}
          <div className="chunk-list">
            {(chunks?.items || []).map(c => (
              <div key={c.index}
                className={`chunk-item${c.index === selectedChunk ? ' selected' : ''}`}
                onClick={() => handleChunkClick(c)}>
                <div className="chunk-item-inner">
                  <div className="chunk-item-title">
                    {(c.heading_path || []).join(' > ') || `chunk #${c.index}`}
                  </div>
                  <div className="chunk-item-preview">
                    {c.preview?.substring(0, 80)}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 空状态 */}
      {!meta && !loading?.upload && (
        <div className="source-empty">
          <div className="source-empty-icon">📁</div>
          <div className="source-empty-text">上传 PDF / Word / Excel / TXT / EPUB 开始分析</div>
        </div>
      )}
    </aside>
  );
}
