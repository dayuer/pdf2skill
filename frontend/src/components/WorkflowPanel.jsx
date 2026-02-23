import { useState, useCallback, useRef, useEffect } from 'react';
import {
  ReactFlow, Background, Controls, MiniMap,
  useNodesState, useEdgesState, addEdge,
  Handle, Position, Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

/* ── 默认管线定义 ── */
const PIPELINE_DEFS = [
  { id: 'load', icon: '📄', label: '文档加载', desc: '解析 PDF/TXT/EPUB', type: 'document_loader', auto: true },
  { id: 'chunk', icon: '✂️', label: '智能切分', desc: '标题层次 + 语义边界', type: 'chunker', auto: true },
  { id: 'filter', icon: '🔬', label: '语义密度筛', desc: '三维密度评分', type: 'semantic_filter', auto: true },
  { id: 'schema', icon: '📐', label: 'Schema 生成', desc: 'R1 分析结构', type: 'schema_gen', promptKey: 'system_prompt' },
  { id: 'extract', icon: '⚡', label: '技能提取', desc: '按 Schema 提取 Skill', type: 'extractor', promptKey: 'prompt_hint' },
  { id: 'validate', icon: '✅', label: '校验', desc: '完整性 + 幻觉检测', type: 'validator' },
  { id: 'reduce', icon: '🔗', label: '聚类去重', desc: 'Tag 归一化 → 聚类', type: 'reducer' },
  { id: 'classify', icon: '🏷️', label: 'SKU 分类', desc: '事实/程序/关系', type: 'classifier' },
  { id: 'package', icon: '📦', label: '打包输出', desc: 'mapping + 依赖图', type: 'packager' },
];

function makeDefaultNodes() {
  return PIPELINE_DEFS.map((d, i) => ({
    id: d.id,
    type: 'pipeline',
    position: { x: 250, y: i * 120 },
    data: { ...d, status: d.auto ? 'done' : 'idle', config: {} },
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

/* ── 自定义 Pipeline 节点 ── */
function PipelineNode({ data, selected }) {
  const statusMap = {
    idle: { cls: 'idle', text: '待执行' },
    running: { cls: 'running', text: '执行中…' },
    done: { cls: 'done', text: '✓ 完成' },
    error: { cls: 'error', text: '✗ 失败' },
  };
  const s = statusMap[data.status] || statusMap.idle;

  return (
    <div className={`rf-node${selected ? ' selected' : ''}${data.status === 'running' ? ' running' : ''}`}>
      <Handle type="target" position={Position.Top} className="rf-handle" />
      <div className="rf-node-header">
        <span className="rf-node-icon">{data.icon}</span>
        <div className="rf-node-info">
          <div className="rf-node-label">{data.label}</div>
          <div className="rf-node-desc">{data.desc}</div>
        </div>
        <span className={`node-status ${s.cls}`}>{s.text}</span>
      </div>
      <Handle type="source" position={Position.Bottom} className="rf-handle" />
    </div>
  );
}

const nodeTypes = { pipeline: PipelineNode };

/* ── 主组件 ── */
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
  const [selectedNode, setSelectedNode] = useState(null);
  const reactFlowWrapper = useRef(null);

  // 连线
  const onConnect = useCallback((params) => {
    setEdges((eds) => addEdge({
      ...params,
      type: 'smoothstep',
      animated: false,
      style: { stroke: '#d5cdc4', strokeWidth: 2 },
    }, eds));
  }, [setEdges]);

  // 选中节点
  const onNodeClick = useCallback((_, node) => {
    setSelectedNode(node);
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedNode(null);
  }, []);

  // 更新节点状态
  const updateNodeStatus = useCallback((nodeId, status) => {
    setNodes(nds => nds.map(n => n.id === nodeId ? { ...n, data: { ...n.data, status } } : n));
  }, [setNodes]);

  // 根据上传和执行状态同步
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
        config: {
          ...n.data.config,
          ...(n.data.promptKey === 'system_prompt' ? { system_prompt: systemPrompt } : {}),
          ...(n.data.promptKey === 'prompt_hint' ? { prompt_hint: promptHint } : {}),
        },
      })),
      edges: edges.map(e => ({ source: e.source, target: e.target })),
    };
    const blob = new Blob([JSON.stringify(workflow, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `workflow-${workflow.id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [nodes, edges, meta, systemPrompt, promptHint]);

  // 执行工作流
  const handleExecute = useCallback(() => {
    const workflow = {
      nodes: nodes.map(n => ({
        id: n.id,
        type: n.data.type,
        config: {
          ...n.data.config,
          ...(n.data.promptKey === 'system_prompt' ? { system_prompt: systemPrompt } : {}),
          ...(n.data.promptKey === 'prompt_hint' ? { prompt_hint: promptHint } : {}),
        },
      })),
      edges: edges.map(e => ({ source: e.source, target: e.target })),
    };
    console.log('📋 Workflow JSON:', JSON.stringify(workflow, null, 2));
    onExecuteAll?.();
  }, [nodes, edges, systemPrompt, promptHint, onExecuteAll]);

  // 拖入新节点
  const onDragOver = useCallback((e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback((e) => {
    e.preventDefault();
    const nodeType = e.dataTransfer.getData('application/reactflow');
    if (!nodeType) return;
    const def = PIPELINE_DEFS.find(d => d.id === nodeType);
    if (!def) return;
    const bounds = reactFlowWrapper.current?.getBoundingClientRect();
    const pos = {
      x: e.clientX - (bounds?.left || 0) - 80,
      y: e.clientY - (bounds?.top || 0) - 30,
    };
    const newNode = {
      id: `${def.id}-${Date.now()}`,
      type: 'pipeline',
      position: pos,
      data: { ...def, status: 'idle', config: {} },
    };
    setNodes(nds => [...nds, newNode]);
  }, [setNodes]);

  if (!meta) {
    return (
      <main className="panel-center">
        <div className="center-placeholder">
          <div className="placeholder-icon">🔄</div>
          <div className="placeholder-title">上传文档启动工作流</div>
          <div className="placeholder-sub">支持拖拽节点编排流程</div>
        </div>
      </main>
    );
  }

  const selData = selectedNode?.data;
  const selPromptVal = selData?.promptKey === 'system_prompt' ? systemPrompt
    : selData?.promptKey === 'prompt_hint' ? promptHint : null;

  return (
    <main className="panel-center" ref={reactFlowWrapper}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        onDragOver={onDragOver}
        onDrop={onDrop}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        defaultEdgeOptions={{ type: 'smoothstep' }}
        style={{ background: '#faf8f5' }}
      >
        <Background color="#e0d8cf" gap={20} size={1} />
        <Controls position="bottom-left" />
        <MiniMap
          nodeColor={(n) => {
            if (n.data?.status === 'done') return '#ceead6';
            if (n.data?.status === 'running') return '#d3e3fd';
            if (n.data?.status === 'error') return '#fce8e6';
            return '#f1f3f4';
          }}
          style={{ background: '#fff', border: '1px solid #e0d8cf' }}
        />

        {/* 顶部工具栏 */}
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

      {/* 节点属性面板 */}
      {selectedNode && selData && (
        <div className="rf-inspector">
          <div className="rf-inspector-header">
            <span>{selData.icon} {selData.label}</span>
            <button className="btn-icon" onClick={() => setSelectedNode(null)}>✕</button>
          </div>
          <div className="rf-inspector-body">
            <div className="rf-inspector-row">
              <span className="rf-inspector-label">类型</span>
              <span className="rf-inspector-value">{selData.type}</span>
            </div>
            <div className="rf-inspector-row">
              <span className="rf-inspector-label">状态</span>
              <span className={`node-status ${selData.status}`}>
                {selData.status === 'done' ? '✓ 完成' : selData.status === 'running' ? '执行中' : '待执行'}
              </span>
            </div>
            {selPromptVal !== null && (
              <>
                <div className="rf-inspector-label" style={{ marginTop: 8 }}>
                  {selData.promptKey === 'system_prompt' ? 'System Prompt' : '提取策略'}
                </div>
                <textarea className="wf-prompt-textarea" value={selPromptVal} rows={6}
                  onChange={e => {
                    if (selData.promptKey === 'system_prompt') onSystemPromptChange(e.target.value);
                    else onPromptHintChange(e.target.value);
                  }} />
              </>
            )}
            <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
              {!selData.auto && (
                <button className="btn btn-primary btn-sm"
                  onClick={() => onRunNode?.(selectedNode.id)}>
                  ▶ 执行
                </button>
              )}
              {selData.auto && (
                <span style={{ fontSize: 12, color: '#80868b', fontStyle: 'italic' }}>
                  自动执行节点
                </span>
              )}
            </div>

            {/* 结果区 */}
            {selectedNode.id === 'extract' && tuneResult && (
              <div className="wf-result" style={{ marginTop: 12 }}>
                <div className="wf-result-title">
                  试运行 · chunk #{tuneResult.chunk_index}
                  <span className="wf-result-stats">
                    {(tuneResult.extracted_skills || []).filter(s => s.status !== 'failed').length}✅
                  </span>
                </div>
                {(tuneResult.extracted_skills || []).slice(0, 5).map((s, i) => (
                  <div key={i} className={`wf-skill-card${s.status === 'failed' ? ' fail' : ''}`}>
                    <div className="wf-skill-name">{s.name}</div>
                    <div className="wf-skill-meta">
                      <span className="skill-domain">{s.domain}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {selectedNode.id === 'validate' && sampleResult && (
              <div className="wf-result" style={{ marginTop: 12 }}>
                <div className="wf-result-title">
                  通过率 {((sampleResult.passed / sampleResult.total) * 100).toFixed(0)}%
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </main>
  );
}
