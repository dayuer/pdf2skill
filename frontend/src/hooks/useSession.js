import { useState, useCallback, useEffect, useRef } from 'react';
import * as api from '../api';

export function useSession() {
  const [sessionId, setSessionId] = useState(() => localStorage.getItem('pdf2skill_session'));
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

  const persistSession = useCallback((id) => {
    setSessionId(id);
    localStorage.setItem('pdf2skill_session', id);
  }, []);

  const reset = useCallback(() => {
    localStorage.removeItem('pdf2skill_session');
    setSessionId(null); setMeta(null); setChunks({ items: [], total: 0 });
    setSelectedChunk(null); setTuneResult(null); setTuneHistory([]);
    setSampleResult(null); setExecuteState(null); setSkills([]);
  }, []);

  // 上传
  const upload = useCallback(async (file) => {
    setLoading(l => ({ ...l, upload: true }));
    try {
      const data = await api.uploadFile(file);
      persistSession(data.session_id);
      setMeta(data);
      if (data.baseline_hint) setPromptHint(data.baseline_hint);
      if (data.system_prompt) setSystemPrompt(data.system_prompt);
      return data;
    } finally { setLoading(l => ({ ...l, upload: false })); }
  }, [persistSession]);

  // 加载 chunks
  const loadChunks = useCallback(async (q) => {
    if (!sessionId) return;
    const data = await api.loadChunks(sessionId, q);
    setChunks(data);
  }, [sessionId]);

  // 重切
  const doRechunk = useCallback(async () => {
    if (!sessionId) return;
    const d = await api.rechunk(sessionId);
    if (d.ok) loadChunks();
    return d;
  }, [sessionId, loadChunks]);

  // 调优
  const doTune = useCallback(async () => {
    if (!sessionId || selectedChunk === null) return;
    setLoading(l => ({ ...l, tune: true }));
    try {
      const d = await api.tune(sessionId, selectedChunk, promptHint, systemPrompt);
      setTuneResult(d);
      const h = await api.getTuneHistory(sessionId);
      setTuneHistory(h);
      return d;
    } finally { setLoading(l => ({ ...l, tune: false })); }
  }, [sessionId, selectedChunk, promptHint, systemPrompt]);

  // 抽样
  const doSample = useCallback(async () => {
    if (!sessionId) return;
    setLoading(l => ({ ...l, sample: true }));
    try {
      const d = await api.sampleCheck(sessionId);
      setSampleResult(d);
      return d;
    } finally { setLoading(l => ({ ...l, sample: false })); }
  }, [sessionId]);

  // 全量执行
  const doExecute = useCallback(() => {
    if (!sessionId) return;
    setExecuteState({ running: true, pct: 0, text: '准备中...' });
    const cleanup = api.startExecution(sessionId, {
      onPhase: d => setExecuteState(s => ({ ...s, text: d.message })),
      onProgress: d => {
        const pct = ((d.completed / d.total) * 100).toFixed(0);
        const eta = d.eta_s > 60 ? `${(d.eta_s / 60).toFixed(0)}m` : `${d.eta_s.toFixed(0)}s`;
        setExecuteState({ running: true, pct: +pct, text: `${d.completed}/${d.total} (${pct}%) | 💾 ${d.skills_on_disk || 0} Skills | ⏱${d.elapsed_s.toFixed(0)}s ETA ${eta}` });
      },
      onComplete: d => {
        setExecuteState({ running: false, pct: 100, text: `✅ 完成！${d.final_skills} Skills`, data: d });
        api.getSkills(sessionId).then(setSkills);
      },
      onError: () => setExecuteState(s => ({ ...s, running: false, text: '❌ 连接中断' })),
    });
    cleanupRef.current = cleanup;
  }, [sessionId]);

  // 加载技能
  const loadSkills = useCallback(async () => {
    if (!sessionId) return;
    const s = await api.getSkills(sessionId);
    setSkills(s || []);
  }, [sessionId]);

  // 保存设置
  const doSaveSettings = useCallback(async (settings) => {
    if (!sessionId) return;
    await api.saveSettings(sessionId, settings);
    const pp = await api.getPromptPreview(sessionId);
    if (pp.system_prompt) setSystemPrompt(pp.system_prompt);
    if (pp.baseline_hint) setPromptHint(pp.baseline_hint);
  }, [sessionId]);

  // 会话恢复
  useEffect(() => {
    if (!sessionId) return;
    (async () => {
      const st = await api.getSessionState(sessionId);
      if (!st) { reset(); return; }
      setMeta(st.meta);
      loadChunks();
      loadSkills();
      const pp = await api.getPromptPreview(sessionId).catch(() => null);
      if (pp?.system_prompt) setSystemPrompt(pp.system_prompt);
      if (pp?.baseline_hint) setPromptHint(pp.baseline_hint);
      const h = await api.getTuneHistory(sessionId).catch(() => []);
      setTuneHistory(h);
    })();
  }, []); // eslint-disable-line

  // 清理 SSE
  useEffect(() => () => cleanupRef.current?.(), []);

  return {
    sessionId, meta, chunks, selectedChunk, setSelectedChunk,
    tuneResult, setTuneResult, tuneHistory,
    sampleResult, executeState, skills, loading,
    systemPrompt, setSystemPrompt, promptHint, setPromptHint,
    upload, loadChunks, doRechunk, doTune, doSample, doExecute,
    loadSkills, doSaveSettings, reset,
  };
}
