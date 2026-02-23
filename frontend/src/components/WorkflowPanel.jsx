import { useState, useCallback, useRef, useEffect, memo } from 'react';
import {
  ReactFlow, Background, Controls, MiniMap,
  useNodesState, useEdgesState, addEdge,
  Handle, Position, Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

/* ══════ 管线节点定义（n8n properties 模式）══════ */
const PIPELINE_DEFS = [
  {
    id: 'load', icon: '📄', label: '文档加载', desc: '解析 PDF/TXT/EPUB',
    type: 'document_loader', auto: true,
    properties: [
      { name: 'format', displayName: '文件格式', type: 'options', options: ['PDF', 'TXT', 'EPUB', 'DOCX'], default: 'PDF' },
    ],
  },
  {
    id: 'chunk', icon: '✂️', label: '智能切分', desc: '标题层次 + 语义边界',
    type: 'chunker', auto: true,
    properties: [
      { name: 'max_chars', displayName: '最大字符数', type: 'number', default: 2000 },
      { name: 'min_chars', displayName: '最小字符数', type: 'number', default: 200 },
      { name: 'strategy', displayName: '切分策略', type: 'options', options: ['semantic', 'fixed', 'paragraph'], default: 'semantic' },
    ],
  },
  {
    id: 'filter', icon: '🔬', label: '语义密度筛', desc: '三维密度评分',
    type: 'semantic_filter', auto: true,
    properties: [
      { name: 'threshold', displayName: '密度阈值', type: 'number', default: 0.3 },
      { name: 'dimensions', displayName: '评判维度', type: 'options', options: ['逻辑+实体+结构', '仅逻辑', '仅实体'], default: '逻辑+实体+结构' },
    ],
  },
  {
    id: 'schema', icon: '📐', label: 'Schema 生成', desc: 'R1 分析结构',
    type: 'schema_gen', promptKey: 'system_prompt',
    properties: [
      { name: 'model', displayName: '模型', type: 'options', options: ['deepseek-r1', 'gpt-4o', 'claude-3.5'], default: 'deepseek-r1' },
      { name: 'system_prompt', displayName: 'System Prompt', type: 'code', default: '' },
    ],
  },
  {
    id: 'extract', icon: '⚡', label: '技能提取', desc: '按 Schema 提取 Skill',
    type: 'extractor', promptKey: 'prompt_hint',
    properties: [
      { name: 'model', displayName: '模型', type: 'options', options: ['deepseek-v3', 'gpt-4o-mini', 'claude-3.5-haiku'], default: 'deepseek-v3' },
      { name: 'prompt_hint', displayName: '提取策略', type: 'code', default: '' },
      { name: 'temperature', displayName: '温度', type: 'number', default: 0.1 },
      { name: 'max_skills_per_chunk', displayName: '每块最大 Skills', type: 'number', default: 10 },
    ],
  },
  {
    id: 'validate', icon: '✅', label: '校验', desc: '完整性 + 幻觉检测',
    type: 'validator',
    properties: [
      { name: 'sample_size', displayName: '抽样量', type: 'number', default: 5 },
      { name: 'pass_threshold', displayName: '通过率阈值', type: 'number', default: 0.6 },
    ],
  },
  {
    id: 'reduce', icon: '🔗', label: '聚类去重', desc: 'Tag 归一化 → 聚类',
    type: 'reducer',
    properties: [
      { name: 'similarity_threshold', displayName: '相似度阈值', type: 'number', default: 0.85 },
      { name: 'method', displayName: '聚类方法', type: 'options', options: ['cosine', 'jaccard', 'hybrid'], default: 'cosine' },
    ],
  },
  {
    id: 'classify', icon: '🏷️', label: 'SKU 分类', desc: '事实/程序/关系',
    type: 'classifier',
    properties: [
      { name: 'categories', displayName: '分类体系', type: 'options', options: ['事实型/程序型/关系型', '自定义'], default: '事实型/程序型/关系型' },
    ],
  },
  {
    id: 'package', icon: '📦', label: '打包输出', desc: 'mapping + 依赖图',
    type: 'packager',
    properties: [
      { name: 'format', displayName: '输出格式', type: 'options', options: ['YAML', 'JSON', 'Markdown'], default: 'YAML' },
      { name: 'include_graph', displayName: '包含依赖图', type: 'boolean', default: true },
      { name: 'include_glossary', displayName: '包含术语表', type: 'boolean', default: true },
    ],
  },
];

function makeDefaultNodes() {
  return PIPELINE_DEFS.map((d, i) => ({
    id: d.id,
    type: 'pipeline',
    position: { x: 280, y: i * 120 },
    data: {
      ...d,
      status: d.auto ? 'done' : 'idle',
      config: Object.fromEntries((d.properties || []).map(p => [p.name, p.default])),
      expanded: false,
    },
  }));
}

function makeDefaultEdges() {
  return PIPELINE_DEFS.slice(1).map((d, i) => ({
    id: `e-${PIPELINE_DEFS[i].id}-${d.id}`,
    source: PIPELINE_DEFS[i].id,
    target: d.id,
    type: 'smoothstep',
    animated: false,
    style: { stroke: '#d5cdc4', strokeWidth: 2 },
  }));
}

/* ══════ n8n 风格参数渲染器 ══════ */
function ParamField({ prop, value, onChange }) {
  if (prop.type === 'code') {
    return (
      <div className="nd-field">
        <label className="nd-label">{prop.displayName}</label>
        <textarea className="nd-textarea" value={value || ''} rows={4}
          onChange={e => onChange(prop.name, e.target.value)} />
      </div>
    );
  }
  if (prop.type === 'options') {
    return (
      <div className="nd-field">
        <label className="nd-label">{prop.displayName}</label>
        <select className="nd-select" value={value ?? prop.default}
          onChange={e => onChange(prop.name, e.target.value)}>
          {prop.options.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    );
  }
  if (prop.type === 'number') {
    return (
      <div className="nd-field">
        <label className="nd-label">{prop.displayName}</label>
        <input className="nd-input" type="number" value={value ?? prop.default}
          onChange={e => onChange(prop.name, parseFloat(e.target.value) || 0)} />
      </div>
    );
  }
  if (prop.type === 'boolean') {
    return (
      <div className="nd-field nd-field-row">
        <label className="nd-label">{prop.displayName}</label>
        <input type="checkbox" checked={!!value}
          onChange={e => onChange(prop.name, e.target.checked)} />
      </div>
    );
  }
  return (
    <div className="nd-field">
      <label className="nd-label">{prop.displayName}</label>
      <input className="nd-input" type="text" value={value || ''}
        onChange={e => onChange(prop.name, e.target.value)} />
    </div>
  );
}

/* ══════ 自定义 Pipeline 节点 ══════ */
const PipelineNode = memo(function PipelineNode({ id, data, selected }) {
  const statusMap = {
    idle: { cls: 'idle', text: '待执行' },
    running: { cls: 'running', text: '执行中…' },
    done: { cls: 'done', text: '✓ 完成' },
    error: { cls: 'error', text: '✗ 失败' },
  };
  const s = statusMap[data.status] || statusMap.idle;

  return (
    <div className={`rf-node${selected ? ' selected' : ''}${data.status === 'running' ? ' running' : ''}${data.expanded ? ' expanded' : ''}`}>
      <Handle type="target" position={Position.Top} className="rf-handle" />

      {/* 头部 — 始终显示 */}
      <div className="rf-node-header">
        <span className="rf-node-icon">{data.icon}</span>
        <div className="rf-node-info">
          <div className="rf-node-label">{data.label}</div>
          <div className="rf-node-desc">{data.desc}</div>
        </div>
        <span className={`node-status ${s.cls}`}>{s.text}</span>
      </div>

      {/* 展开区域 — n8n 配置面板 */}
      {data.expanded && (
        <div className="rf-node-detail" onClick={e => e.stopPropagation()}>
          <div className="nd-divider" />
          <div className="nd-section-title">
            <span>⚙ 参数配置</span>
            <span className="nd-type-tag">{data.type}</span>
          </div>
          {(data.properties || []).map(prop => (
            <ParamField key={prop.name} prop={prop} value={data.config?.[prop.name]}
              onChange={(name, val) => {
                data._onConfigChange?.(id, name, val);
              }} />
          ))}
          {data._onRunNode && (
            <div className="nd-actions">
              <button className="btn btn-primary btn-sm" onClick={() => data._onRunNode(id)}>
                ▶ 执行此节点
              </button>
            </div>
          )}
        </div>
      )}

      <Handle type="source" position={Position.Bottom} className="rf-handle" />
    </div>
  );
});

const nodeTypes = { pipeline: PipelineNode };

/* ══════ 主组件 ══════ */
export default function WorkflowPanel({
  meta, executeState,
  systemPrompt, promptHint,
  onSystemPromptChange, onPromptHintChange,
  onRunNode, onExecuteAll,
  tuneResult, sampleResult,
  nodeStatuses = {},
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState(makeDefaultNodes());
  const [edges, setEdges, onEdgesChange] = useEdgesState(makeDefaultEdges());
  const reactFlowWrapper = useRef(null);

  // 配置变更回调（注入到节点 data 中）
  const handleConfigChange = useCallback((nodeId, paramName, value) => {
    setNodes(nds => nds.map(n => {
      if (n.id !== nodeId) return n;
      const newConfig = { ...n.data.config, [paramName]: value };
      // 同步 prompt 到外部状态
      if (paramName === 'system_prompt' && onSystemPromptChange) onSystemPromptChange(value);
      if (paramName === 'prompt_hint' && onPromptHintChange) onPromptHintChange(value);
      return { ...n, data: { ...n.data, config: newConfig } };
    }));
  }, [setNodes, onSystemPromptChange, onPromptHintChange]);

  // 将回调注入节点 data
  useEffect(() => {
    setNodes(nds => nds.map(n => ({
      ...n,
      data: { ...n.data, _onConfigChange: handleConfigChange, _onRunNode: onRunNode },
    })));
  }, [handleConfigChange, onRunNode, setNodes]);

  // 同步外部 prompt 到节点 config
  useEffect(() => {
    setNodes(nds => nds.map(n => {
      if (n.data.promptKey === 'system_prompt' && systemPrompt !== undefined) {
        return { ...n, data: { ...n.data, config: { ...n.data.config, system_prompt: systemPrompt } } };
      }
      if (n.data.promptKey === 'prompt_hint' && promptHint !== undefined) {
        return { ...n, data: { ...n.data, config: { ...n.data.config, prompt_hint: promptHint } } };
      }
      return n;
    }));
  }, [systemPrompt, promptHint, setNodes]);

  // 同步节点状态
  useEffect(() => {
    if (meta) {
      setNodes(nds => nds.map(n => {
        if (n.data.auto) return { ...n, data: { ...n.data, status: 'done' } };
        if (nodeStatuses[n.id]) return { ...n, data: { ...n.data, status: nodeStatuses[n.id] } };
        return n;
      }));
    }
    if (executeState?.pct >= 100) {
      setNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, status: 'done' } })));
    }
  }, [meta, executeState, nodeStatuses, setNodes]);

  // ★ 双击展开/收起 ★
  const onNodeDoubleClick = useCallback((_, node) => {
    setNodes(nds => nds.map(n => {
      if (n.id !== node.id) return n;
      return { ...n, data: { ...n.data, expanded: !n.data.expanded } };
    }));
  }, [setNodes]);

  // 连线
  const onConnect = useCallback((params) => {
    setEdges(eds => addEdge({
      ...params, type: 'smoothstep', animated: false,
      style: { stroke: '#d5cdc4', strokeWidth: 2 },
    }, eds));
  }, [setEdges]);

  // 导出 JSON DAG
  const exportWorkflow = useCallback(() => {
    const workflow = {
      id: `wf-${Date.now()}`,
      name: meta?.doc_name || 'untitled',
      nodes: nodes.map(n => ({
        id: n.id,
        type: n.data.type,
        label: n.data.label,
        position: n.position,
        config: n.data.config,
      })),
      edges: edges.map(e => ({ source: e.source, target: e.target })),
    };
    const blob = new Blob([JSON.stringify(workflow, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `workflow-${workflow.id}.json`; a.click();
    URL.revokeObjectURL(url);
  }, [nodes, edges, meta]);

  // 执行
  const handleExecute = useCallback(() => {
    const workflow = {
      nodes: nodes.map(n => ({ id: n.id, type: n.data.type, config: n.data.config })),
      edges: edges.map(e => ({ source: e.source, target: e.target })),
    };
    console.log('📋 Workflow JSON:', JSON.stringify(workflow, null, 2));
    onExecuteAll?.();
  }, [nodes, edges, onExecuteAll]);

  // 拖入新节点
  const onDragOver = useCallback(e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; }, []);
  const onDrop = useCallback(e => {
    e.preventDefault();
    const nodeType = e.dataTransfer.getData('application/reactflow');
    if (!nodeType) return;
    const def = PIPELINE_DEFS.find(d => d.id === nodeType);
    if (!def) return;
    const bounds = reactFlowWrapper.current?.getBoundingClientRect();
    setNodes(nds => [...nds, {
      id: `${def.id}-${Date.now()}`,
      type: 'pipeline',
      position: { x: e.clientX - (bounds?.left || 0) - 80, y: e.clientY - (bounds?.top || 0) - 30 },
      data: {
        ...def, status: 'idle', expanded: false,
        config: Object.fromEntries((def.properties || []).map(p => [p.name, p.default])),
        _onConfigChange: handleConfigChange, _onRunNode: onRunNode,
      },
    }]);
  }, [setNodes, handleConfigChange, onRunNode]);

  if (!meta) {
    return (
      <main className="panel-center">
        <div className="center-placeholder">
          <div className="placeholder-icon">🔄</div>
          <div className="placeholder-title">上传文档启动工作流</div>
          <div className="placeholder-sub">双击节点编辑参数 · 拖拽节点编排流程</div>
        </div>
      </main>
    );
  }

  return (
    <main className="panel-center" ref={reactFlowWrapper}>
      <ReactFlow
        nodes={nodes} edges={edges}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDoubleClick={onNodeDoubleClick}
        onDragOver={onDragOver} onDrop={onDrop}
        nodeTypes={nodeTypes}
        fitView fitViewOptions={{ padding: 0.3 }}
        defaultEdgeOptions={{ type: 'smoothstep' }}
        style={{ background: '#faf8f5' }}
      >
        <Background color="#e0d8cf" gap={20} size={1} />
        <Controls position="bottom-left" />
        <MiniMap
          nodeColor={n => {
            if (n.data?.status === 'done') return '#ceead6';
            if (n.data?.status === 'running') return '#d3e3fd';
            if (n.data?.status === 'error') return '#fce8e6';
            return '#f1f3f4';
          }}
          style={{ background: '#fff', border: '1px solid #e0d8cf' }}
        />
        <Panel position="top-right">
          <div className="rf-toolbar">
            {executeState && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginRight: 8 }}>
                <div className="progress-bar" style={{ width: 100, height: 3 }}>
                  <div className="progress-fill" style={{ width: `${executeState.pct}%` }} />
                </div>
                <span style={{ fontSize: 11, color: '#80868b' }}>{executeState.text}</span>
              </div>
            )}
            <button className="btn btn-ghost btn-sm" onClick={exportWorkflow} title="导出 JSON">
              📋 导出
            </button>
            <button className="btn btn-primary btn-sm" onClick={handleExecute}>
              ▶ 执行工作流
            </button>
          </div>
        </Panel>
      </ReactFlow>
    </main>
  );
}
