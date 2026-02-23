/* ══════ API 封装层 ══════ */

const BASE = '';

export async function uploadFile(file) {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch(`${BASE}/api/analyze`, { method: 'POST', body: fd });
  if (!r.ok) throw new Error((await r.json()).detail || '分析失败');
  return r.json();
}

export async function loadChunks(sessionId, q, pageSize = 50) {
  const params = new URLSearchParams({ page_size: pageSize });
  if (q) params.set('q', q);
  const r = await fetch(`${BASE}/api/chunks/${sessionId}?${params}`);
  return r.json();
}

export async function rechunk(sessionId, maxChars = 2000, minChars = 200) {
  const r = await fetch(`${BASE}/api/rechunk/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_chars: maxChars, min_chars: minChars }),
  });
  return r.json();
}

export async function saveSettings(sessionId, settings) {
  await fetch(`${BASE}/api/session/${sessionId}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
}

export async function getPromptPreview(sessionId) {
  const r = await fetch(`${BASE}/api/prompt-preview/${sessionId}`);
  return r.json();
}

export async function tune(sessionId, chunkIndex, promptHint, systemPrompt) {
  const r = await fetch(`${BASE}/api/tune/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chunk_index: chunkIndex, prompt_hint: promptHint, system_prompt: systemPrompt }),
  });
  return r.json();
}

export async function getTuneHistory(sessionId) {
  const r = await fetch(`${BASE}/api/tune-history/${sessionId}`);
  return r.json();
}

export async function sampleCheck(sessionId, sampleSize = 5) {
  const r = await fetch(`${BASE}/api/sample-check/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sample_size: sampleSize }),
  });
  return r.json();
}

export function startExecution(sessionId, { onPhase, onProgress, onComplete, onError }) {
  const src = new EventSource(`${BASE}/api/execute/${sessionId}`);
  src.addEventListener('phase', e => onPhase?.(JSON.parse(e.data)));
  src.addEventListener('progress', e => onProgress?.(JSON.parse(e.data)));
  src.addEventListener('complete', e => { src.close(); onComplete?.(JSON.parse(e.data)); });
  src.onerror = () => { src.close(); onError?.(); };
  return () => src.close();
}

export async function getSessionState(sessionId) {
  const r = await fetch(`${BASE}/api/session/${sessionId}/state`);
  if (!r.ok) return null;
  return r.json();
}

export async function getSkills(sessionId) {
  const r = await fetch(`${BASE}/api/session/${sessionId}/skills`);
  return r.json();
}

export async function generateSkills(sessionId) {
  const r = await fetch(`${BASE}/api/session/${sessionId}/generate-skills`, { method: 'POST' });
  return r.json();
}

export async function getManifest(sessionId) {
  const r = await fetch(`${BASE}/api/session/${sessionId}/manifest`);
  if (!r.ok) return null;
  return r.json();
}
