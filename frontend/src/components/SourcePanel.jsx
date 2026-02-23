import { useState, useRef, useCallback, useMemo } from 'react';

const FILE_ACCEPT = '.pdf,.txt,.epub,.md,.docx,.doc,.xlsx,.xls,.csv';

const STATUS_ICON = {
  pending: '⏳', extracting: '📄', cleaning: '🔄', done: '✅', error: '❌',
};

const STATUS_LABEL = {
  pending: '等待处理', extracting: '提取文本', cleaning: 'LLM 格式整理', done: '完成', error: '处理失败',
};

export default function SourcePanel({
  meta, chunks, loading, onBatchUpload, onReprocess, onDeleteFile, onChunkFile,
  uploadProgress, uploadFiles, onSearch, onSelectChunk, selectedChunk,
}) {
  const fileRef = useRef();
  const [viewingChunk, setViewingChunk] = useState(null);
  const [viewingFile, setViewingFile] = useState(null);
  const [chunkResult, setChunkResult] = useState(null);
  const [chunking, setChunking] = useState(false);

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

  // ── 合并进度 + 文件列表为统一数据源 ──
  const mergedFiles = useMemo(() => {
    const map = new Map();

    // 1. 先放已有文件列表（含详细信息）
    (uploadFiles || []).forEach(f => {
      map.set(f.filename, { ...f, source: 'files' });
    });

    // 2. 用 SSE 进度覆盖状态（更实时）
    if (uploadProgress) {
      Object.entries(uploadProgress).forEach(([filename, info]) => {
        if (filename.startsWith('_')) return;
        const existing = map.get(filename);
        if (existing) {
          // 进度中的状态更实时，覆盖
          existing.status = info.status;
          existing.message = info.message || existing.message;
          if (info.chars) existing.chars = info.chars;
        } else {
          // 文件列表还没加载到，从进度数据构造临时条目
          map.set(filename, {
            filename,
            size: 0,
            status: info.status,
            message: info.message || '',
            chars: info.chars || 0,
            source: 'progress',
          });
        }
      });
    }

    return Array.from(map.values());
  }, [uploadFiles, uploadProgress]);

  const handleDelete = useCallback(async (filename) => {
    if (!confirm(`确定删除「${filename}」及其所有关联文件？`)) return;
    try {
      await onDeleteFile?.(filename);
      // 若正在查看该文件详情，返回列表
      if (viewingFile?.filename === filename) setViewingFile(null);
    } catch (e) {
      alert(`删除失败: ${e.message}`);
    }
  }, [onDeleteFile, viewingFile]);

  const handleReprocess = useCallback(async (filename) => {
    try {
      await onReprocess?.(filename);
    } catch (e) {
      alert(`重新处理失败: ${e.message}`);
    }
  }, [onReprocess]);

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

          {/* 操作按钮 */}
          <div className="file-detail-actions">
            <button
              className="btn-reprocess"
              onClick={() => handleReprocess(viewingFile.filename)}
              disabled={loading?.upload}
            >
              {loading?.upload ? '处理中…' : '🔄 重新处理'}
            </button>
            <button
              className="btn-reprocess"
              style={{ marginLeft: 8, background: '#e8f0fe', color: '#1a73e8', border: '1px solid #c2d9fc' }}
              onClick={async () => {
                setChunking(true);
                setChunkResult(null);
                try {
                  await onChunkFile?.(viewingFile.filename, { setChunkResult, setChunking });
                } catch (e) {
                  setChunkResult({ status: 'error', message: e.message });
                  setChunking(false);
                }
              }}
              disabled={chunking || !viewingFile.clean_text}
            >
              {chunking ? '分块中…' : '✂️ 分块'}
            </button>
            <button
              className="btn-delete-file"
              style={{ marginLeft: 8 }}
              onClick={() => handleDelete(viewingFile.filename)}
            >
              🗑️ 删除
            </button>
          </div>

          {/* 分块进度 */}
          {chunkResult && (
            <div className="file-detail-text" style={{ marginTop: 12 }}>
              <div className="file-detail-label">
                {chunkResult.status === 'error'
                  ? `❌ ${chunkResult.message}`
                  : chunkResult.status === 'done'
                    ? `✅ ${chunkResult.message}`
                    : `⏳ ${chunkResult.message}`}
              </div>
              {chunkResult.chunks > 0 && (
                <div style={{ fontSize: 12, color: '#5f6368', marginTop: 4 }}>
                  {chunkResult.segments_done}/{chunkResult.segments_total} 段已处理 · 累计 {chunkResult.chunks} 个片段
                  {chunkResult.jsonl_path && <><br/>输出: {chunkResult.jsonl_path}</>}
                </div>
              )}
            </div>
          )}
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

  // 统计处理中的数量
  const processingCount = mergedFiles.filter(f =>
    f.status === 'pending' || f.status === 'extracting' || f.status === 'cleaning'
  ).length;

  // ── 列表视图（统一进度 + 文件列表） ──
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

      {/* 处理中提示 */}
      {processingCount > 0 && (
        <div className="loading-text"><div className="spinner" /><span>正在处理 {processingCount} 个文档…</span></div>
      )}

      {/* 统一文件列表 */}
      {mergedFiles.length > 0 && (
        <div className="source-list">
          <div className="chunk-header-row">
            <span className="chunk-count">文件 ({mergedFiles.length})</span>
          </div>
          {mergedFiles.map(f => (
            <div key={f.filename} className={`source-file-item ${f.status === 'error' ? 'error' : ''}`}>
              <span className="source-file-icon">{STATUS_ICON[f.status] || '📄'}</span>
              <div className="source-file-info"
                onClick={() => f.status === 'done' || f.source === 'files' ? setViewingFile(f) : null}
                style={{ cursor: f.status === 'done' ? 'pointer' : 'default' }}
              >
                <div className="source-file-name">{f.filename}</div>
                <div className="source-file-meta">
                  {f.status === 'done'
                    ? <>{f.chars} 字符 · {(f.size / 1024).toFixed(1)} KB</>
                    : f.status === 'error'
                      ? <span className="source-file-error-msg">{f.message || '处理失败'}</span>
                      : <>{STATUS_LABEL[f.status] || f.message || f.status}{f.size > 0 ? ` · ${(f.size / 1024).toFixed(1)} KB` : ''}</>
                  }
                </div>
              </div>
              {/* 失败文件：删除 + 重做按钮 */}
              {f.status === 'error' && (
                <div className="source-file-actions">
                  <button className="btn-file-action btn-file-retry"
                    onClick={() => handleReprocess(f.filename)} title="重新处理">
                    🔄
                  </button>
                  <button className="btn-file-action btn-file-delete"
                    onClick={() => handleDelete(f.filename)} title="删除文件">
                    🗑️
                  </button>
                </div>
              )}
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
      {!meta && !loading?.upload && mergedFiles.length === 0 && (
        <div className="source-empty">
          <div className="source-empty-icon">📁</div>
          <div className="source-empty-text">上传 PDF / Word / Excel / TXT / EPUB 开始分析</div>
        </div>
      )}
    </aside>
  );
}
