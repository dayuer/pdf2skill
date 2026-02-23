import { useState, useRef, useCallback } from 'react';

const FILE_ACCEPT = '.pdf,.txt,.epub,.md,.docx,.doc,.xlsx,.xls,.csv';

const FORMATS = [
  { ext: 'PDF', icon: '📕' },
  { ext: 'Word', icon: '📘' },
  { ext: 'Excel', icon: '📗' },
  { ext: 'TXT', icon: '📄' },
  { ext: 'EPUB', icon: '📖' },
  { ext: 'Markdown', icon: '📝' },
  { ext: 'CSV', icon: '📊' },
];

export default function WorkflowGuide({ workflowName, onUploadFiles, loading }) {
  const fileRef = useRef();
  const [dragOver, setDragOver] = useState(false);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) onUploadFiles?.(files);
  }, [onUploadFiles]);

  const handleFileChange = useCallback((e) => {
    const files = Array.from(e.target.files);
    if (files.length > 0) onUploadFiles?.(files);
    e.target.value = '';
  }, [onUploadFiles]);

  return (
    <div className="guide-overlay">
      <div className="guide-card">
        <div className="guide-gradient-bar" />

        <div className="guide-header">
          <h2 className="guide-title">上传文档，提取技能知识</h2>
          <p className="guide-subtitle">{workflowName || '您的文档'}</p>
        </div>

        {/* 支持的格式标签 */}
        <div className="guide-formats">
          {FORMATS.map(f => (
            <span key={f.ext} className="guide-format-tag">{f.icon} {f.ext}</span>
          ))}
        </div>

        {/* 拖放上传区 */}
        <div
          className={`guide-dropzone${dragOver ? ' dragover' : ''}`}
          onDragOver={e => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileRef.current?.click()}
        >
          {loading ? (
            <>
              <div className="spinner" style={{ width: 28, height: 28, margin: '0 auto 8px' }} />
              <p className="guide-drop-title">正在上传…</p>
            </>
          ) : (
            <>
              <p className="guide-drop-title">拖放文件到这里，或点击上传</p>
              <p className="guide-drop-hint">支持多文件批量上传，自动去重</p>
            </>
          )}
        </div>

        <input
          ref={fileRef}
          type="file"
          accept={FILE_ACCEPT}
          multiple
          style={{ display: 'none' }}
          onChange={handleFileChange}
        />

        {/* 底部按钮 */}
        <div className="guide-actions">
          <button className="guide-action-btn primary" onClick={() => fileRef.current?.click()}>
            <span className="guide-action-icon">📄</span>
            <span>上传文件</span>
          </button>
          <button className="guide-action-btn" disabled title="即将支持">
            <span className="guide-action-icon">📋</span>
            <span>粘贴文字</span>
          </button>
        </div>
      </div>
    </div>
  );
}
