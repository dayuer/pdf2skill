import { useState, useCallback, useRef } from 'react';
import { useNotebook } from './hooks/useNotebook';
import HomePage from './components/HomePage';
import TopBar from './components/TopBar';
import SourcePanel from './components/SourcePanel';
import WorkflowPanel from './components/WorkflowPanel';
import StudioPanel from './components/StudioPanel';
import Resizer from './components/Resizer';
import * as api from './api';

export default function App() {
  const s = useNotebook();
  const [page, setPage] = useState(s.notebookId || s.sessionId ? 'workspace' : 'home');

  const [leftW, setLeftW] = useState(260);
  const [rightW, setRightW] = useState(280);
  const leftBase = useRef(260);
  const rightBase = useRef(280);

  const handleLeftResize = useCallback((delta) => {
    setLeftW(Math.max(180, Math.min(450, leftBase.current + delta)));
  }, []);
  const handleRightResize = useCallback((delta) => {
    setRightW(Math.max(200, Math.min(450, rightBase.current - delta)));
  }, []);

  const handleOpen = useCallback((sessionId) => {
    localStorage.setItem('pdf2skill_session', sessionId);
    window.location.reload();
  }, []);

  const handleNew = useCallback(() => { s.reset(); setPage('workspace'); }, [s]);
  const handleBack = useCallback(() => { setPage('home'); }, []);

  const handleRunNode = useCallback(async (nodeId) => {
    if (!s.sessionId) { alert('请先上传文档'); return; }
    switch (nodeId) {
      case 'schema':
      case 'save_prompt':
        await api.saveSettings(s.sessionId, { system_prompt: s.systemPrompt });
        alert('Prompt 已保存');
        break;
      case 'extract':
        s.doTune();
        break;
      case 'validate':
        s.doSample();
        break;
      case 'reduce':
      case 'classify':
      case 'package':
        if (confirm('开始全量执行？将触发剩余管线节点。')) s.doExecute();
        break;
      case 'skills':
        s.loadSkills();
        break;
      default:
        break;
    }
  }, [s, s.sessionId, s.systemPrompt]);

  const handleExecuteAll = useCallback(() => {
    if (!s.sessionId) { alert('请先上传文档'); return; }
    if (confirm('开始全量执行？将使用当前策略处理所有 chunk。')) s.doExecute();
  }, [s]);

  const handleSaveSystemPrompt = useCallback(async () => {
    if (!s.sessionId) return;
    await api.saveSettings(s.sessionId, { system_prompt: s.systemPrompt });
  }, [s.sessionId, s.systemPrompt]);

  const handleUpload = useCallback(async (file) => {
    const data = await s.upload(file);
    if (data) setPage('workspace');
    return data;
  }, [s]);

  const handleBatchUpload = useCallback(async (files) => {
    const data = await s.batchUpload(files);
    if (data) setPage('workspace');
    return data;
  }, [s]);

  if (page === 'home') {
    return <HomePage onOpen={handleOpen} onNew={handleNew} />;
  }

  return (
    <>
      <TopBar meta={s.meta} onReset={s.reset} onBack={handleBack} />
      <div className="main">
        <div style={{ width: leftW, minWidth: 180, flexShrink: 0 }}>
          <SourcePanel
            meta={s.meta} chunks={s.chunks} selectedChunk={s.selectedChunk} loading={s.loading}
            onUpload={handleUpload} onBatchUpload={handleBatchUpload}
            onStartProcessing={s.startProcessing}
            uploadProgress={s.uploadProgress}
            onSearch={s.loadChunks} onSelectChunk={s.setSelectedChunk}
          />
        </div>
        <Resizer onResize={handleLeftResize} />
        <WorkflowPanel
          meta={s.meta}
          executeState={s.executeState}
          loading={s.loading}
          systemPrompt={s.systemPrompt}
          promptHint={s.promptHint}
          onSystemPromptChange={s.setSystemPrompt}
          onPromptHintChange={s.setPromptHint}
          onRunNode={handleRunNode}
          onExecuteAll={handleExecuteAll}
          tuneResult={s.tuneResult}
          sampleResult={s.sampleResult}
        />
        <Resizer onResize={handleRightResize} />
        <div style={{ width: rightW, minWidth: 200, flexShrink: 0 }}>
          <StudioPanel sessionId={s.sessionId} skills={s.skills} onAction={handleRunNode} />
        </div>
      </div>
    </>
  );
}
