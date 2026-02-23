export default function TopBar({ meta, onReset, onBack }) {
  return (
    <header className="topbar">
      <div className="topbar-left">
        {onBack && (
          <button className="btn btn-ghost btn-sm" onClick={onBack}
            style={{ fontSize: 16, padding: '4px 8px', marginRight: 4 }}>←</button>
        )}
        <h1 className="logo" onClick={onBack} style={{ cursor: onBack ? 'pointer' : 'default' }}>pdf2skill</h1>
        {meta && <span className="doc-name">{meta.name || meta.doc_name}</span>}
      </div>
      <div className="topbar-right">
        {meta && (
          <>
            <button className="btn btn-ghost btn-sm">✨ 分析</button>
            <button className="btn btn-ghost btn-sm">↗ 分享</button>
            <button className="btn btn-ghost btn-sm">⚙ 设置</button>
          </>
        )}
        {meta && (
          <button className="btn btn-ghost btn-sm" onClick={onReset}>重新上传</button>
        )}
      </div>
    </header>
  );
}
