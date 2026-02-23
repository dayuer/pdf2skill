import { useState, useCallback, useEffect, useRef } from 'react';
import * as api from '../api';

export function useNotebook() {
  const [notebookId, setNotebookId] = useState(() => localStorage.getItem('pdf2skill_notebook'));
  const [meta, setMeta] = useState(null);
  const [chunks, setChunks] = useState({ items: [], total: 0 });
  const [selectedChunk, setSelectedChunk] = useState(null);
  const [tuneResult, setTuneResult] = useState(null);
  const [tuneHistory, setTuneHistory] = useState([]);
  const [sampleResult, setSampleResult] = useState(null);
  const [executeState, setExecuteState] = useState(null);
  const [skills, setSkills] = useState([]);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [promptHint, setPromptHint] = useState('');
  const [loading, setLoading] = useState({ upload: false, tune: false, sample: false });

  const cleanupRef = useRef(null);

  // 向后兼容：同时暴露 sessionId
  const sessionId = notebookId;

  const persistNotebook = useCallback((id) => {
    setNotebookId(id);
    localStorage.setItem('pdf2skill_notebook', id);
    // 向后兼容
    localStorage.setItem('pdf2skill_session', id);
  }, []);

  const reset = useCallback(() => {
    localStorage.removeItem('pdf2skill_notebook');
    localStorage.removeItem('pdf2skill_session');
    setNotebookId(null); setMeta(null); setChunks({ items: [], total: 0 });
    setSelectedChunk(null); setTuneResult(null); setTuneHistory([]);
    setSampleResult(null); setExecuteState(null); setSkills([]);
  }, []);

  // 单文件上传（向后兼容）
  const upload = useCallback(async (file) => {
    setLoading(l => ({ ...l, upload: true }));
    try {
      const data = await api.uploadFile(file);
      persistNotebook(data.notebook_id || data.session_id);
      setMeta(data);
      if (data.baseline_hint) setPromptHint(data.baseline_hint);
      if (data.system_prompt) setSystemPrompt(data.system_prompt);
      return data;
    } finally { setLoading(l => ({ ...l, upload: false })); }
  }, [persistNotebook]);

  // 批量上传 + SSE 进度
  const [uploadProgress, setUploadProgress] = useState(null);
  const progressCleanupRef = useRef(null);

  const batchUpload = useCallback(async (files) => {
    setLoading(l => ({ ...l, upload: true }));
    setUploadProgress({});
    try {
      const data = await api.uploadFiles(files);
      persistNotebook(data.notebook_id);

      // 监听 SSE 进度
      const cleanup = api.watchUploadProgress(data.notebook_id, {
        onProgress: (status) => setUploadProgress(status),
        onDone: async (overall) => {
          setUploadProgress(prev => ({ ...prev, __overall__: overall }));
          setLoading(l => ({ ...l, upload: false }));
          // 加载最终状态
          const st = await api.getSessionState(data.notebook_id).catch(() => null);
          if (st?.meta) setMeta(st.meta);
          const cs = await api.loadChunks(data.notebook_id).catch(() => ({ items: [], total: 0 }));
          setChunks(cs);
          const pp = await api.getPromptPreview(data.notebook_id).catch(() => null);
          if (pp?.system_prompt) setSystemPrompt(pp.system_prompt);
          if (pp?.baseline_hint) setPromptHint(pp.baseline_hint);
        },
        onError: () => {
          setLoading(l => ({ ...l, upload: false }));
          setUploadProgress(prev => ({ ...prev, __overall__: { status: 'error', message: '连接中断' } }));
        },
      });
      progressCleanupRef.current = cleanup;
      return data;
    } catch (e) {
      setLoading(l => ({ ...l, upload: false }));
      throw e;
    }
  }, [persistNotebook]);

  // 加载 chunks
  const loadChunks = useCallback(async (q) => {
    if (!notebookId) return;
    const data = await api.loadChunks(notebookId, q);
    setChunks(data);
  }, [notebookId]);

  // 重切
  const doRechunk = useCallback(async () => {
    if (!notebookId) return;
    const d = await api.rechunk(notebookId);
    if (d.ok) loadChunks();
    return d;
  }, [notebookId, loadChunks]);

  // 调优
  const doTune = useCallback(async () => {
    if (!notebookId || selectedChunk === null) return;
    setLoading(l => ({ ...l, tune: true }));
    try {
      const d = await api.tune(notebookId, selectedChunk, promptHint, systemPrompt);
      setTuneResult(d);
      const h = await api.getTuneHistory(notebookId);
      setTuneHistory(h);
      return d;
    } finally { setLoading(l => ({ ...l, tune: false })); }
  }, [notebookId, selectedChunk, promptHint, systemPrompt]);

  // 抽样
  const doSample = useCallback(async () => {
    if (!notebookId) return;
    setLoading(l => ({ ...l, sample: true }));
    try {
      const d = await api.sampleCheck(notebookId);
      setSampleResult(d);
      return d;
    } finally { setLoading(l => ({ ...l, sample: false })); }
  }, [notebookId]);

  // 全量执行
  const doExecute = useCallback(() => {
    if (!notebookId) return;
    setExecuteState({ running: true, pct: 0, text: '准备中...' });
    const cleanup = api.startExecution(notebookId, {
      onPhase: d => setExecuteState(s => ({ ...s, text: d.message })),
      onProgress: d => {
        const pct = ((d.completed / d.total) * 100).toFixed(0);
        const eta = d.eta_s > 60 ? `${(d.eta_s / 60).toFixed(0)}m` : `${d.eta_s.toFixed(0)}s`;
        setExecuteState({ running: true, pct: +pct, text: `${d.completed}/${d.total} (${pct}%) | 💾 ${d.skills_on_disk || 0} Skills | ⏱${d.elapsed_s.toFixed(0)}s ETA ${eta}` });
      },
      onComplete: d => {
        setExecuteState({ running: false, pct: 100, text: `✅ 完成！${d.final_skills} Skills`, data: d });
        api.getSkills(notebookId).then(setSkills);
      },
      onError: () => setExecuteState(s => ({ ...s, running: false, text: '❌ 连接中断' })),
    });
    cleanupRef.current = cleanup;
  }, [notebookId]);

  // 加载技能
  const loadSkills = useCallback(async () => {
    if (!notebookId) return;
    const s = await api.getSkills(notebookId);
    setSkills(s || []);
  }, [notebookId]);

  // 保存设置
  const doSaveSettings = useCallback(async (settings) => {
    if (!notebookId) return;
    await api.saveSettings(notebookId, settings);
    const pp = await api.getPromptPreview(notebookId);
    if (pp.system_prompt) setSystemPrompt(pp.system_prompt);
    if (pp.baseline_hint) setPromptHint(pp.baseline_hint);
  }, [notebookId]);

  // 笔记本恢复
  useEffect(() => {
    if (!notebookId) return;
    (async () => {
      const st = await api.getSessionState(notebookId);
      if (!st) { reset(); return; }
      setMeta(st.meta);
      loadChunks();
      loadSkills();
      const pp = await api.getPromptPreview(notebookId).catch(() => null);
      if (pp?.system_prompt) setSystemPrompt(pp.system_prompt);
      if (pp?.baseline_hint) setPromptHint(pp.baseline_hint);
      const h = await api.getTuneHistory(notebookId).catch(() => []);
      setTuneHistory(h);
    })();
  }, []); // eslint-disable-line

  // 清理 SSE
  useEffect(() => () => cleanupRef.current?.(), []);

  return {
    // 新命名
    notebookId,
    // 向后兼容
    sessionId,
    meta, chunks, selectedChunk, setSelectedChunk,
    tuneResult, setTuneResult, tuneHistory,
    sampleResult, executeState, skills, loading,
    systemPrompt, setSystemPrompt, promptHint, setPromptHint,
    upload, batchUpload, uploadProgress, loadChunks, doRechunk, doTune, doSample, doExecute,
    loadSkills, doSaveSettings, reset,
  };
}

// 向后兼容别名
export const useSession = useNotebook;
