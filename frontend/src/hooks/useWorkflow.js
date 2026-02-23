import { useState, useCallback, useEffect, useRef } from 'react';
import * as api from '../api';

export function useWorkflow() {
  const [workflowId, setWorkflowId] = useState(() => localStorage.getItem('pdf2skill_workflow'));
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


  const persistWorkflow = useCallback((id) => {
    setWorkflowId(id);
    localStorage.setItem('pdf2skill_workflow', id);
  }, []);

  const reset = useCallback(() => {
    localStorage.removeItem('pdf2skill_workflow');
    setWorkflowId(null); setMeta(null); setChunks({ items: [], total: 0 });
    setSelectedChunk(null); setTuneResult(null); setTuneHistory([]);
    setSampleResult(null); setExecuteState(null); setSkills([]);
  }, []);


  // 批量上传（自动开始处理）
  const [uploadProgress, setUploadProgress] = useState(null);
  const [uploadFiles, setUploadFiles] = useState([]);
  const progressCleanupRef = useRef(null);

  const prevProgressRef = useRef({});

  const batchUpload = useCallback(async (files) => {
    setLoading(l => ({ ...l, upload: true }));
    setUploadProgress({});
    prevProgressRef.current = {};
    try {
      // 若还没有 workflowId，先创建工作流
      let wfId = workflowId;
      if (!wfId) {
        const createRes = await fetch('/api/workflow/create', { method: 'POST' });
        const createData = await createRes.json();
        wfId = createData.workflow_id;
        persistWorkflow(wfId);
      }

      const data = await api.uploadFiles(files, wfId);
      persistWorkflow(data.workflow_id);

      // 监听 SSE 处理进度
      const cleanup = api.watchUploadProgress(data.workflow_id, {
        onProgress: (status) => {
          setUploadProgress(status);
          // 检测新完成/失败的文件 → 立即刷新文件列表
          const prev = prevProgressRef.current;
          const hasNewDone = Object.entries(status).some(([k, v]) =>
            !k.startsWith('_') &&
            (v.status === 'done' || v.status === 'error') &&
            prev[k]?.status !== v.status
          );
          if (hasNewDone) {
            api.getUploadFiles(data.workflow_id)
              .then(uf => setUploadFiles(uf.files || []))
              .catch(() => {});
          }
          prevProgressRef.current = { ...status };
        },
        onDone: async () => {
          setLoading(l => ({ ...l, upload: false }));
          const st = await api.getWorkflowState(data.workflow_id).catch(() => null);
          if (st?.meta) setMeta(st.meta);
          const cs = await api.loadChunks(data.workflow_id).catch(() => ({ items: [], total: 0 }));
          setChunks(cs);
          const pp = await api.getPromptPreview(data.workflow_id).catch(() => null);
          if (pp?.system_prompt) setSystemPrompt(pp.system_prompt);
          if (pp?.baseline_hint) setPromptHint(pp.baseline_hint);
          const uf = await api.getUploadFiles(data.workflow_id).catch(() => ({ files: [] }));
          setUploadFiles(uf.files || []);
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
  }, [workflowId, persistWorkflow]);

  // 加载文件列表（含处理状态 + 文本）
  const loadUploadFiles = useCallback(async () => {
    if (!workflowId) return;
    const data = await api.getUploadFiles(workflowId);
    setUploadFiles(data.files || []);
  }, [workflowId]);

  // 重新处理单个文件
  const doReprocess = useCallback(async (filename) => {
    if (!workflowId) return;
    setLoading(l => ({ ...l, upload: true }));
    try {
      const result = await api.reprocessFile(workflowId, filename);
      await loadUploadFiles(); // 刷新文件列表
      return result;
    } finally {
      setLoading(l => ({ ...l, upload: false }));
    }
  }, [workflowId, loadUploadFiles]);

  // 删除单个文件（级联清理所有关联产物）
  const doDeleteFile = useCallback(async (filename) => {
    if (!workflowId) return;
    await api.deleteFile(workflowId, filename);
    // 从进度列表中移除
    setUploadProgress(prev => {
      if (!prev) return prev;
      const next = { ...prev };
      delete next[filename];
      return next;
    });
    await loadUploadFiles();
  }, [workflowId, loadUploadFiles]);

  // 加载 chunks
  const loadChunks = useCallback(async (q) => {
    if (!workflowId) return;
    const data = await api.loadChunks(workflowId, q);
    setChunks(data);
  }, [workflowId]);

  // 重切
  const doRechunk = useCallback(async () => {
    if (!workflowId) return;
    const d = await api.rechunk(workflowId);
    if (d.ok) loadChunks();
    return d;
  }, [workflowId, loadChunks]);

  // 调优
  const doTune = useCallback(async () => {
    if (!workflowId || selectedChunk === null) return;
    setLoading(l => ({ ...l, tune: true }));
    try {
      const d = await api.tune(workflowId, selectedChunk, promptHint, systemPrompt);
      setTuneResult(d);
      const h = await api.getTuneHistory(workflowId);
      setTuneHistory(h);
      return d;
    } finally { setLoading(l => ({ ...l, tune: false })); }
  }, [workflowId, selectedChunk, promptHint, systemPrompt]);

  // 抽样
  const doSample = useCallback(async () => {
    if (!workflowId) return;
    setLoading(l => ({ ...l, sample: true }));
    try {
      const d = await api.sampleCheck(workflowId);
      setSampleResult(d);
      return d;
    } finally { setLoading(l => ({ ...l, sample: false })); }
  }, [workflowId]);

  // 全量执行
  const doExecute = useCallback(() => {
    if (!workflowId) return;
    setExecuteState({ running: true, pct: 0, text: '准备中...' });
    const cleanup = api.startExecution(workflowId, {
      onPhase: d => setExecuteState(s => ({ ...s, text: d.message })),
      onProgress: d => {
        const pct = ((d.completed / d.total) * 100).toFixed(0);
        const eta = d.eta_s > 60 ? `${(d.eta_s / 60).toFixed(0)}m` : `${d.eta_s.toFixed(0)}s`;
        setExecuteState({ running: true, pct: +pct, text: `${d.completed}/${d.total} (${pct}%) | 💾 ${d.skills_on_disk || 0} Skills | ⏱${d.elapsed_s.toFixed(0)}s ETA ${eta}` });
      },
      onComplete: d => {
        setExecuteState({ running: false, pct: 100, text: `✅ 完成！${d.final_skills} Skills`, data: d });
        api.getSkills(workflowId).then(setSkills);
      },
      onError: () => setExecuteState(s => ({ ...s, running: false, text: '❌ 连接中断' })),
    });
    cleanupRef.current = cleanup;
  }, [workflowId]);

  // 加载技能
  const loadSkills = useCallback(async () => {
    if (!workflowId) return;
    const s = await api.getSkills(workflowId);
    setSkills(s || []);
  }, [workflowId]);

  // 保存设置
  const doSaveSettings = useCallback(async (settings) => {
    if (!workflowId) return;
    await api.saveSettings(workflowId, settings);
    const pp = await api.getPromptPreview(workflowId);
    if (pp.system_prompt) setSystemPrompt(pp.system_prompt);
    if (pp.baseline_hint) setPromptHint(pp.baseline_hint);
  }, [workflowId]);

  // 工作流恢复
  useEffect(() => {
    if (!workflowId) return;
    (async () => {
      const st = await api.getWorkflowState(workflowId).catch(() => null);
      if (st?.meta) {
        setMeta(st.meta);
        loadChunks();
        loadSkills();
        const pp = await api.getPromptPreview(workflowId).catch(() => null);
        if (pp?.system_prompt) setSystemPrompt(pp.system_prompt);
        if (pp?.baseline_hint) setPromptHint(pp.baseline_hint);
        const h = await api.getTuneHistory(workflowId).catch(() => []);
        setTuneHistory(h);
      }
      // 无论是否有 meta，都尝试加载文件列表
      const uf = await api.getUploadFiles(workflowId).catch(() => ({ files: [] }));
      setUploadFiles(uf.files || []);
    })();
  }, []); // eslint-disable-line

  // 清理 SSE
  useEffect(() => () => cleanupRef.current?.(), []);

  return {
    // 新命名
    workflowId,
    meta, chunks, selectedChunk, setSelectedChunk,
    tuneResult, setTuneResult, tuneHistory,
    sampleResult, executeState, skills, loading,
    systemPrompt, setSystemPrompt, promptHint, setPromptHint,
    batchUpload, uploadProgress, uploadFiles, loadUploadFiles, doReprocess, doDeleteFile,
    loadChunks, doRechunk, doTune, doSample, doExecute,
    loadSkills, doSaveSettings, reset,
  };
}

// 向后兼容别名
export const useSession = useWorkflow;
