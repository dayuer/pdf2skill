/* ══════ API 封装层 ══════ */

const BASE = '';

export async function uploadFiles(files, workflowId) {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  const r = await fetch(`${BASE}/api/upload/${workflowId}`, { method: 'POST', body: fd });
  if (!r.ok) throw new Error((await r.json()).detail || '上传失败');
  return r.json();
}

export async function getUploadFiles(workflowId) {
  const r = await fetch(`${BASE}/api/upload/${workflowId}/files`);
  if (!r.ok) return { files: [], total: 0 };
  return r.json();
}

export async function reprocessFile(workflowId, filename) {
  const r = await fetch(`${BASE}/api/reprocess/${workflowId}/${encodeURIComponent(filename)}`, { method: 'POST' });
  if (!r.ok) throw new Error((await r.json()).detail || '处理失败');
  return r.json();
}

export function watchUploadProgress(workflowId, { onProgress, onDone, onError }) {
  const src = new EventSource(`${BASE}/api/upload/progress/${workflowId}`);
  src.onmessage = e => onProgress?.(JSON.parse(e.data));
  src.addEventListener('done', e => { src.close(); onDone?.(JSON.parse(e.data)); });
  src.onerror = () => { src.close(); onError?.(); };
  return () => src.close();
}

export async function loadChunks(workflowId, q, pageSize = 50) {
  const params = new URLSearchParams({ page_size: pageSize });
  if (q) params.set('q', q);
  const r = await fetch(`${BASE}/api/chunks/${workflowId}?${params}`);
  return r.json();
}

export async function rechunk(workflowId, maxChars = 2000, minChars = 200) {
  const r = await fetch(`${BASE}/api/rechunk/${workflowId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_chars: maxChars, min_chars: minChars }),
  });
  return r.json();
}

export async function saveSettings(workflowId, settings) {
  await fetch(`${BASE}/api/workflow/${workflowId}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
}

export async function getPromptPreview(workflowId) {
  const r = await fetch(`${BASE}/api/prompt-preview/${workflowId}`);
  return r.json();
}

export async function tune(workflowId, chunkIndex, promptHint, systemPrompt) {
  const r = await fetch(`${BASE}/api/tune/${workflowId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chunk_index: chunkIndex, prompt_hint: promptHint, system_prompt: systemPrompt }),
  });
  return r.json();
}

export async function getTuneHistory(workflowId) {
  const r = await fetch(`${BASE}/api/tune-history/${workflowId}`);
  return r.json();
}

export async function sampleCheck(workflowId, sampleSize = 5) {
  const r = await fetch(`${BASE}/api/sample-check/${workflowId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sample_size: sampleSize }),
  });
  return r.json();
}

export function startExecution(workflowId, { onPhase, onProgress, onComplete, onError }) {
  const src = new EventSource(`${BASE}/api/execute/${workflowId}`);
  src.addEventListener('phase', e => onPhase?.(JSON.parse(e.data)));
  src.addEventListener('progress', e => onProgress?.(JSON.parse(e.data)));
  src.addEventListener('complete', e => { src.close(); onComplete?.(JSON.parse(e.data)); });
  src.onerror = () => { src.close(); onError?.(); };
  return () => src.close();
}

export async function getWorkflowState(workflowId) {
  const r = await fetch(`${BASE}/api/workflow/${workflowId}/state`);
  if (!r.ok) return null;
  return r.json();
}

export async function getSkills(workflowId) {
  const r = await fetch(`${BASE}/api/workflow/${workflowId}/skills`);
  return r.json();
}

export async function generateSkills(workflowId) {
  const r = await fetch(`${BASE}/api/workflow/${workflowId}/generate-skills`, { method: 'POST' });
  return r.json();
}

export async function getManifest(workflowId) {
  const r = await fetch(`${BASE}/api/workflow/${workflowId}/manifest`);
  if (!r.ok) return null;
  return r.json();
}
