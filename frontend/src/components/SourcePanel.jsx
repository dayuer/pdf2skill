import { useState, useRef, useCallback } from 'react';

const FILE_ACCEPT = '.pdf,.txt,.epub,.md,.docx,.doc,.xlsx,.xls,.csv';

const STATUS_ICON = {
  pending: '⏳', extracting: '📄', cleaning: '🔄', done: '✅', error: '❌',
};

export default function SourcePanel({
  meta, chunks, loading, onBatchUpload, onReprocess,
  uploadProgress, uploadFiles, onSearch, onSelectChunk, selectedChunk,
}) {
  const fileRef = useRef();
  const [viewingChunk, setViewingChunk] = useState(null);
  const [viewingFile, setViewingFile] = useState(null);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0 && onBatchUpload) onBatchUpload(files);
  }, [onBatchUpload]);

  const handleFileChange = useCallback((e) => {
    const files = Array.from(e.target.files);
    if (files.length > 0 && onBatchUpload) onBatchUpload(files);
    e.target.value = '';
  }, [onBatchUpload]);

  const handleChunkClick = (chunk) => {
    setViewingChunk(chunk);
    onSelectChunk?.(chunk.index);
  };

  const handleBack = () => { setViewingChunk(null); setViewingFile(null); };

  // ── 文件详情视图（查看处理后的文本 + 重新处理按钮） ──
  if (viewingFile) {
    return (
      <aside className="panel-left">
        <div className="panel-header">
          <button className="btn-icon" onClick={handleBack} title="返回列表">←</button>
          <span className="panel-title" style={{ flex: 1 }}>
            {viewingFile.filename}
          </span>
          <span className={`file-status-badge ${viewingFile.status}`}>
            {STATUS_ICON[viewingFile.status] || '❓'} {viewingFile.status}
          </span>
        </div>

        <div className="file-detail">
          {viewingFile.chars > 0 && (
            <div className="file-detail-meta">
              <span className="chunk-detail-tag">{viewingFile.chars} 字符</span>
              <span className="chunk-detail-tag">{(viewingFile.size / 1024).toFixed(1)} KB</span>
            </div>
          )}

          {/* 处理后的文本 */}
          {viewingFile.clean_text ? (
            <div className="file-detail-text">
              <div className="file-detail-label">处理后文本：</div>
              <pre className="file-text-content">{viewingFile.clean_text}</pre>
            </div>
          ) : viewingFile.raw_text ? (
            <div className="file-detail-text">
              <div className="file-detail-label">原始文本：</div>
              <pre className="file-text-content">{viewingFile.raw_text}</pre>
            </div>
          ) : (
            <div className="file-detail-empty">暂无处理结果</div>
          )}

          {/* 重新处理按钮 */}
          <div className="file-detail-actions">
            <button
              className="btn-reprocess"
              onClick={() => onReprocess?.(viewingFile.filename)}
              disabled={loading?.upload}
            >
              {loading?.upload ? '处理中…' : '🔄 重新处理'}
            </button>
          </div>
        </div>
      </aside>
    );
  }

  // ── Chunk 详情视图 ──
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

      {/* 上传进度（SSE 实时） */}
      {uploadProgress && Object.keys(uploadProgress).length > 0 && (
        <div className="upload-progress-list">
          {Object.entries(uploadProgress).filter(([k]) => !k.startsWith('_')).map(([filename, info]) => (
            <div key={filename} className={`upload-progress-item ${info.status}`}>
              <span className="upload-progress-icon">{STATUS_ICON[info.status] || '❓'}</span>
              <span className="upload-progress-name">{filename}</span>
              <span className="upload-progress-status">{info.message}</span>
            </div>
          ))}
        </div>
      )}

      {loading?.upload && (
        <div className="loading-text"><div className="spinner" /><span>正在处理文档…</span></div>
      )}

      {/* 已上传文件列表（点击查看详情） */}
      {uploadFiles?.length > 0 && (
        <div className="source-list">
          <div className="chunk-header-row">
            <span className="chunk-count">文件 ({uploadFiles.length})</span>
          </div>
          {uploadFiles.map(f => (
            <div key={f.filename} className="source-file-item" onClick={() => setViewingFile(f)}
              style={{ cursor: 'pointer' }}>
              <span className="source-file-icon">{STATUS_ICON[f.status] || '📄'}</span>
              <div className="source-file-info">
                <div className="source-file-name">{f.filename}</div>
                <div className="source-file-meta">
                  {f.status === 'done' ? `${f.chars} 字符` : f.message || f.status}
                  {' · '}{(f.size / 1024).toFixed(1)} KB
                </div>
              </div>
            </div>
          ))}
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



      {/* 空状态 */}
      {!meta && !loading?.upload && (!uploadFiles || uploadFiles.length === 0) && (
        <div className="source-empty">
          <div className="source-empty-icon">📁</div>
          <div className="source-empty-text">上传 PDF / Word / Excel / TXT / EPUB 开始分析</div>
        </div>
      )}
    </aside>
  );
}
