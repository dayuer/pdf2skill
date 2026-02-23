import { useState, useCallback, useRef } from 'react';
import { useWorkflow } from './hooks/useWorkflow';
import HomePage from './components/HomePage';
import WorkflowGuide from './components/WorkflowGuide';
import TopBar from './components/TopBar';
import SourcePanel from './components/SourcePanel';
import WorkflowPanel from './components/WorkflowPanel';
import StudioPanel from './components/StudioPanel';
import Resizer from './components/Resizer';
import * as api from './api';

export default function App() {
  const s = useWorkflow();
  const [page, setPage] = useState(s.workflowId ? 'workspace' : 'home');

  const [leftW, setLeftW] = useState(260);
  const [rightW, setRightW] = useState(280);
  const leftBase = useRef(260);
  const rightBase = useRef(280);

  const handleLeftResize = useCallback((delta) => {
    setLeftW(Math.max(180, Math.min(window.innerWidth * 0.65, leftBase.current + delta)));
  }, []);
  const handleRightResize = useCallback((delta) => {
    setRightW(Math.max(200, Math.min(450, rightBase.current - delta)));
  }, []);

  const handleOpen = useCallback((wfId) => {
    localStorage.setItem('pdf2skill_workflow', wfId);
    window.location.reload();
  }, []);

  const handleNew = useCallback(() => { s.reset(); setPage('workspace'); }, [s]);
  const handleBack = useCallback(() => { setPage('home'); }, []);

  const handleRunNode = useCallback(async (nodeId) => {
    if (!s.workflowId) { alert('请先上传文档'); return; }
    switch (nodeId) {
      case 'schema':
      case 'save_prompt':
        await api.saveSettings(s.workflowId, { system_prompt: s.systemPrompt });
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
  }, [s, s.workflowId, s.systemPrompt]);

  const handleExecuteAll = useCallback(() => {
    if (!s.workflowId) { alert('请先上传文档'); return; }
    if (confirm('开始全量执行？将使用当前策略处理所有 chunk。')) s.doExecute();
  }, [s]);

  const handleSaveSystemPrompt = useCallback(async () => {
    if (!s.workflowId) return;
    await api.saveSettings(s.workflowId, { system_prompt: s.systemPrompt });
  }, [s.workflowId, s.systemPrompt]);


  const handleBatchUpload = useCallback(async (files) => {
    const data = await s.batchUpload(files);
    if (data) setPage('workspace');
    return data;
  }, [s]);

  if (page === 'home') {
    return <HomePage onOpen={handleOpen} onNew={handleNew} />;
  }

  // 新工作流（没有 meta 也没有 uploadFiles）→ 引导页
  const isNewWorkflow = s.workflowId && !s.meta && (!s.uploadFiles || s.uploadFiles.length === 0);

  if (isNewWorkflow) {
    return (
      <>
        <TopBar meta={s.meta} onReset={s.reset} onBack={handleBack} />
        <WorkflowGuide
          workflowName={s.meta?.name}
          onUploadFiles={handleBatchUpload}
          loading={s.loading?.upload}
        />
      </>
    );
  }

  return (
    <>
      <TopBar meta={s.meta} onReset={s.reset} onBack={handleBack} />
      <div className="main">
        <div style={{ width: leftW, minWidth: 180, flexShrink: 0 }}>
          <SourcePanel
            meta={s.meta} chunks={s.chunks} selectedChunk={s.selectedChunk} loading={s.loading}
            onBatchUpload={handleBatchUpload}
            onReprocess={s.doReprocess}
            uploadProgress={s.uploadProgress}
            uploadFiles={s.uploadFiles}
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
          <StudioPanel workflowId={s.workflowId} skills={s.skills} onAction={handleRunNode} />
        </div>
      </div>
    </>
  );
}
