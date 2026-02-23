function SkillCard({ skill }) {
  const fail = skill.status === 'failed';
  return (
    <div className={`skill-card${fail ? ' fail' : ''}`}>
      <div className="skill-name">{skill.name || '(unnamed)'}</div>
      <div className="skill-trigger">{skill.trigger || ''}</div>
      <span className="skill-domain">{skill.domain || 'general'}</span>
      <div className="skill-body">{skill.body || ''}</div>
    </div>
  );
}

function SuggestedActions({ meta, onAction }) {
  if (!meta) return null;
  const questions = [
    `提取「${(meta.domains || [''])[0]}」领域的关键规则`,
    `分析文档中的操作流程`,
    `概览文档的核心知识结构`,
  ];
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: '0 28px 16px' }}>
      {questions.map((q, i) => (
        <button key={i} onClick={() => onAction?.(q)}
          style={{ padding: '8px 16px', borderRadius: 20, border: '1px solid #dadce0', background: '#fff', color: '#1f1f1f', fontSize: 13, cursor: 'pointer', transition: 'all .15s', fontFamily: 'inherit' }}
          onMouseEnter={e => e.target.style.background = '#f8f9fa'}
          onMouseLeave={e => e.target.style.background = '#fff'}>
          {q}
        </button>
      ))}
    </div>
  );
}

export default function WorkPanel({ meta, tuneResult, sampleResult, executeState, tuneHistory, loading, onReplayVersion }) {
  if (!meta) {
    return (
      <main className="panel-center">
        <div className="center-placeholder">
          <div className="placeholder-icon">📚</div>
          <div className="placeholder-title">上传文档开始提取知识</div>
          <div className="placeholder-sub">支持 PDF、TXT、EPUB、Markdown</div>
        </div>
      </main>
    );
  }

  return (
    <main className="panel-center">
      {/* 顶部区域标题 */}
      <div style={{ padding: '16px 28px 8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #f1f3f4' }}>
        <span style={{ fontSize: 14, fontWeight: 500, color: '#1f1f1f' }}>对话</span>
        <span style={{ fontSize: 16, color: '#5f6368', cursor: 'pointer' }}>⋮</span>
      </div>

      {/* AI 摘要 */}
      {meta && !tuneResult && !executeState && (
        <div style={{ padding: '24px 28px' }}>
          <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
            <div style={{ width: 36, height: 36, borderRadius: '50%', background: '#e8f0fe', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, flexShrink: 0 }}>📄</div>
            <div>
              <div style={{ fontSize: 18, fontWeight: 500, color: '#1f1f1f', marginBottom: 4 }}>{meta.doc_name}</div>
              <div style={{ fontSize: 12, color: '#80868b' }}>{meta.filtered_chunks || 0} 个来源</div>
            </div>
          </div>
          <div style={{ fontSize: 14, color: '#3c4043', lineHeight: 1.7, marginBottom: 16 }}>
            {meta.summary || `「${meta.doc_name}」已完成分析。领域：${(meta.domains || []).join('、')}。共 ${meta.filtered_chunks} 个有效文本块，${meta.total_chunks} 总块。点击右侧 Studio 卡片开始提取知识。`}
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button style={{ padding: '6px 14px', border: '1px solid #dadce0', borderRadius: 20, background: '#fff', color: '#1f1f1f', fontSize: 12, cursor: 'pointer', fontFamily: 'inherit', display: 'flex', alignItems: 'center', gap: 4 }}>
              📌 保存到笔记
            </button>
            <span style={{ cursor: 'pointer', fontSize: 16, color: '#5f6368' }}>📋</span>
            <span style={{ cursor: 'pointer', fontSize: 16, color: '#5f6368' }}>👍</span>
            <span style={{ cursor: 'pointer', fontSize: 16, color: '#5f6368' }}>👎</span>
          </div>
        </div>
      )}

      {/* 建议操作 */}
      {meta && !tuneResult && !executeState && (
        <SuggestedActions meta={meta} />
      )}

      {loading.tune && (
        <div className="loading-text"><div className="spinner" /><span>正在提取…</span></div>
      )}

      {/* 原文预览 */}
      {tuneResult && (
        <div className="content-section">
          <div className="section-title">📖 原文 · chunk #{tuneResult.chunk_index}</div>
          <div className="source-preview">{tuneResult.source_text || ''}</div>
        </div>
      )}

      {/* 提取结果 */}
      {tuneResult && (
        <div className="content-section" style={{ flex: 1, minHeight: 0, borderBottom: 'none' }}>
          <div className="section-title">
            🎯 提取结果
            <span style={{ color: '#80868b', fontSize: 11, marginLeft: 8, textTransform: 'none', letterSpacing: 0 }}>
              v{tuneResult.version || '?'} ·{' '}
              {(tuneResult.extracted_skills || []).filter(s => s.status !== 'failed').length}✅{' '}
              {(tuneResult.extracted_skills || []).filter(s => s.status === 'failed').length}❌
            </span>
          </div>
          <div className="result-pane">
            {(tuneResult.extracted_skills || []).length > 0
              ? (tuneResult.extracted_skills || []).map((s, i) => <SkillCard key={i} skill={s} />)
              : <div className="empty-hint">无可提取内容</div>}
          </div>
        </div>
      )}

      {/* 抽样验证 */}
      {sampleResult && (
        <div className="content-section">
          <div className="section-title">
            🎲 抽样验证
            <span style={{ fontSize: 11, marginLeft: 8, textTransform: 'none', letterSpacing: 0 }} className={
              sampleResult.total > 0 && (sampleResult.passed / sampleResult.total) >= 0.6 ? 'sample-pass' : 'sample-fail'
            }>
              通过率 {sampleResult.total > 0 ? ((sampleResult.passed / sampleResult.total) * 100).toFixed(0) : 0}%
              ({sampleResult.passed}/{sampleResult.total})
            </span>
          </div>
          {(sampleResult.details || sampleResult.results || []).map((item, i) => (
            <div key={i} className="sample-card">
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                <span>#{item.chunk_index}</span>
                <span>{(item.skills || []).length} skills</span>
              </div>
              <div style={{ color: '#80868b', fontSize: 11 }}>{(item.source_preview || '').substring(0, 100)}</div>
            </div>
          ))}
        </div>
      )}

      {/* 全量执行 */}
      {executeState && (
        <div className="content-section">
          <div className="section-title">⚡ 全量执行</div>
          <div className="progress-bar"><div className="progress-fill" style={{ width: `${executeState.pct}%` }} /></div>
          <div className="progress-text">{executeState.text}</div>
          {executeState.data && (
            <div style={{ marginTop: 10, fontSize: 13, color: '#1f1f1f' }}>
              <strong>{executeState.data.final_skills}</strong> SKUs · {executeState.data.elapsed_s}s
            </div>
          )}
        </div>
      )}

      {/* 版本历史 */}
      {tuneHistory.length > 0 && (
        <div className="content-section">
          <div className="section-title">🕐 版本历史</div>
          <div className="version-timeline">
            {tuneHistory.map((h, i) => (
              <div key={i} className={`version-dot${i === tuneHistory.length - 1 ? ' active' : ''}`}
                onClick={() => onReplayVersion(i)} title={`chunk#${h.chunk_index} ${h.timestamp}`}>
                v{h.version}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 底部输入栏 */}
      <div style={{ marginTop: 'auto' }} />
      <div className="chat-input-bar">
        <input type="text" placeholder="开始输入…" />
        <span style={{ fontSize: 12, color: '#80868b' }}>{meta?.filtered_chunks || 0} 个来源</span>
        <button className="send-btn">➤</button>
      </div>
    </main>
  );
}
