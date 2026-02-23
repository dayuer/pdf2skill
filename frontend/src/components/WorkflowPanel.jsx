import { useState, useCallback, useRef, useEffect, memo } from 'react';
import {
  ReactFlow, Background, Controls, MiniMap,
  useNodesState, useEdgesState, addEdge,
  Handle, Position, Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import NodeDrawer from './NodeDrawer';
import NodePalette, { CATEGORIES } from './NodePalette';

/* ══════ n8n 式管线节点属性定义 ══════ */
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
      outputSummary: null,
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

/* ══════ 自定义 Pipeline 节点（n8n 风格） ══════ */
const PipelineNode = memo(function PipelineNode({ id, data, selected }) {
  const statusMap = {
    idle: { cls: 'idle', text: '待执行' },
    running: { cls: 'running', text: '执行中…' },
    done: { cls: 'done', text: '✓ 完成' },
    success: { cls: 'done', text: '✓ 完成' },
    error: { cls: 'error', text: '✗ 失败' },
    skipped: { cls: 'idle', text: '跳过' },
  };
  const s = statusMap[data.status] || statusMap.idle;
  const isPinned = data.pinned;

  return (
    <div className={`rf-node${selected ? ' selected' : ''}${data.status === 'running' ? ' running' : ''}`}>
      <Handle type="target" position={Position.Top} className="rf-handle" />

      {/* 头部 */}
      <div className="rf-node-header">
        <span className="rf-node-icon">{data.icon}</span>
        <div className="rf-node-info">
          <div className="rf-node-label">
            {data.label}
            {isPinned && <span className="rf-pinned-badge" title="数据已固定">📌</span>}
          </div>
          <div className="rf-node-desc">{data.desc}</div>
        </div>
        <span className={`node-status ${s.cls}`}>{s.text}</span>
      </div>

      {/* 输出数据摘要标签 — n8n 风格 */}
      {data.outputSummary && (
        <div className="rf-node-output-badge">{data.outputSummary}</div>
      )}

      {/* 主输出 Handle */}
      <Handle type="source" position={Position.Bottom} id="main"
        className="rf-handle" />

      {/* 错误输出 Handle — 右侧 */}
      <Handle type="source" position={Position.Right} id="error"
        className="rf-handle rf-handle-error"
        style={{ top: '50%' }} />
    </div>
  );
});

const nodeTypes = { pipeline: PipelineNode };

/* ══════ 右键菜单 ══════ */
function ContextMenu({ x, y, nodeId, onClose, onAction }) {
  if (!nodeId) return null;
  const actions = [
    { key: 'run', label: '▶ 运行到此节点', icon: '▶' },
    { key: 'pin', label: '📌 固定数据', icon: '📌' },
    { key: 'disable', label: '⏸ 禁用/启用', icon: '⏸' },
    { key: 'delete', label: '🗑 删除', icon: '🗑', danger: true },
  ];
  return (
    <div className="rf-context-menu" style={{ left: x, top: y }}>
      {actions.map(a => (
        <button key={a.key}
          className={`rf-ctx-item${a.danger ? ' danger' : ''}`}
          onClick={() => { onAction(a.key, nodeId); onClose(); }}>
          {a.label}
        </button>
      ))}
    </div>
  );
}

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

  // NDV 状态
  const [selectedNode, setSelectedNode] = useState(null);
  // NodePalette 状态
  const [showPalette, setShowPalette] = useState(false);
  // 右键菜单状态
  const [contextMenu, setContextMenu] = useState({ show: false, x: 0, y: 0, nodeId: null });
  // 节点执行数据（从 SSE 收集）
  const [nodeOutputs, setNodeOutputs] = useState({});

  // 配置变更回调
  const handleConfigChange = useCallback((nodeId, paramName, value) => {
    setNodes(nds => nds.map(n => {
      if (n.id !== nodeId) return n;
      const newConfig = { ...n.data.config, [paramName]: value };
      if (paramName === 'system_prompt' && onSystemPromptChange) onSystemPromptChange(value);
      if (paramName === 'prompt_hint' && onPromptHintChange) onPromptHintChange(value);
      return { ...n, data: { ...n.data, config: newConfig } };
    }));
  }, [setNodes, onSystemPromptChange, onPromptHintChange]);

  // 同步外部 prompt 到节点
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

  // 同步节点状态 + 输出摘要
  useEffect(() => {
    if (meta) {
      setNodes(nds => nds.map(n => {
        const newData = { ...n.data };
        if (n.data.auto) newData.status = 'done';
        if (nodeStatuses[n.id]) {
          newData.status = nodeStatuses[n.id];
          if (nodeStatuses[n.id] === 'done' || nodeStatuses[n.id] === 'success') {
            newData.outputSummary = nodeOutputs[n.id]?.summary || null;
          }
        }
        return { ...n, data: newData };
      }));
    }
    if (executeState?.pct >= 100) {
      setNodes(nds => nds.map(n => ({ ...n, data: { ...n.data, status: 'done' } })));
      // 执行完成 → 连线动画停止
      setEdges(eds => eds.map(e => ({ ...e, animated: false })));
    }
  }, [meta, executeState, nodeStatuses, nodeOutputs, setNodes, setEdges]);

  // ★ 单击节点 → 打开 NDV 侧抽屉 ★
  const onNodeClick = useCallback((_, node) => {
    setSelectedNode(node);
    setContextMenu(prev => ({ ...prev, show: false }));
  }, []);

  // ★ 右键 → 上下文菜单 ★
  const onNodeContextMenu = useCallback((event, node) => {
    event.preventDefault();
    setContextMenu({
      show: true,
      x: event.clientX,
      y: event.clientY,
      nodeId: node.id,
    });
  }, []);

  // 右键菜单操作
  const handleContextAction = useCallback((action, nodeId) => {
    switch (action) {
      case 'run':
        onRunNode?.(nodeId);
        break;
      case 'pin':
        // TODO: 实现 pinData
        console.log('Pin data for', nodeId);
        break;
      case 'disable':
        setNodes(nds => nds.map(n =>
          n.id === nodeId ? { ...n, data: { ...n.data, disabled: !n.data.disabled } } : n
        ));
        break;
      case 'delete':
        setNodes(nds => nds.filter(n => n.id !== nodeId));
        setEdges(eds => eds.filter(e => e.source !== nodeId && e.target !== nodeId));
        break;
    }
  }, [onRunNode, setNodes, setEdges]);

  // 点击画布空白 → 关闭菜单
  const onPaneClick = useCallback(() => {
    setContextMenu(prev => ({ ...prev, show: false }));
    setSelectedNode(null);
  }, []);

  // 连线
  const onConnect = useCallback((params) => {
    setEdges(eds => addEdge({
      ...params, type: 'smoothstep', animated: false,
      style: { stroke: '#d5cdc4', strokeWidth: 2 },
    }, eds));
  }, [setEdges]);

  // NodePalette 添加节点
  const handleAddNode = useCallback((def) => {
    const newId = `${def.id}-${Date.now()}`;
    const allDefs = CATEGORIES.flatMap(c => c.nodes);
    const fullDef = PIPELINE_DEFS.find(d => d.id === def.id) || {
      ...def,
      properties: [],
    };
    setNodes(nds => [...nds, {
      id: newId,
      type: 'pipeline',
      position: { x: 300 + Math.random() * 100, y: 100 + nds.length * 80 },
      data: {
        ...fullDef,
        id: newId,
        status: 'idle',
        config: Object.fromEntries((fullDef.properties || []).map(p => [p.name, p.default])),
        outputSummary: null,
      },
    }]);
    setShowPalette(false);
  }, [setNodes]);

  // 导出 JSON（n8n connections 格式）
  const exportWorkflow = useCallback(() => {
    const workflow = {
      id: `wf-${Date.now()}`,
      name: meta?.doc_name || 'untitled',
      nodes: nodes.map(n => ({
        id: n.id,
        type: n.data.type,
        label: n.data.label,
        icon: n.data.icon,
        desc: n.data.desc,
        position: n.position,
        parameters: n.data.config,
      })),
      connections: edges.map(e => ({
        source: e.source,
        target: e.target,
        sourceOutputType: e.sourceHandle || 'main',
        targetInputType: e.targetHandle || 'main',
      })),
    };
    const blob = new Blob([JSON.stringify(workflow, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `workflow-${workflow.id}.json`; a.click();
    URL.revokeObjectURL(url);
  }, [nodes, edges, meta]);

  // 执行工作流 — 连线动画 + SSE
  const handleExecute = useCallback(() => {
    // 所有节点标记为 idle（auto 除外）
    setNodes(nds => nds.map(n => ({
      ...n,
      data: { ...n.data, status: n.data.auto ? 'done' : 'idle', outputSummary: null },
    })));
    // 连线开始动画
    setEdges(eds => eds.map(e => ({
      ...e, animated: true,
      style: { ...e.style, stroke: '#7b61ff' },
    })));

    const workflow = {
      nodes: nodes.map(n => ({
        id: n.id, type: n.data.type,
        label: n.data.label, icon: n.data.icon,
        config: n.data.config, parameters: n.data.config,
      })),
      connections: edges.map(e => ({
        source: e.source, target: e.target,
        sourceOutputType: e.sourceHandle || 'main',
      })),
    };
    console.log('📋 Workflow JSON:', JSON.stringify(workflow, null, 2));
    onExecuteAll?.();
  }, [nodes, edges, onExecuteAll, setNodes, setEdges]);

  // 拖放
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
        ...def, status: 'idle', outputSummary: null,
        config: Object.fromEntries((def.properties || []).map(p => [p.name, p.default])),
      },
    }]);
  }, [setNodes]);

  // 键盘快捷键
  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setShowPalette(prev => !prev);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  if (!meta) {
    return (
      <main className="panel-center">
        <div className="center-placeholder">
          <div className="placeholder-icon">🔄</div>
          <div className="placeholder-title">上传文档启动工作流</div>
          <div className="placeholder-sub">单击节点编辑参数 · 右键查看操作 · ⌘K 搜索节点</div>
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
        onNodeClick={onNodeClick}
        onNodeContextMenu={onNodeContextMenu}
        onPaneClick={onPaneClick}
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
            if (n.data?.status === 'done' || n.data?.status === 'success') return '#ceead6';
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
            <button className="btn btn-ghost btn-sm" onClick={() => setShowPalette(!showPalette)}
              title="添加节点 (⌘K)">
              ➕ 节点
            </button>
            <button className="btn btn-ghost btn-sm" onClick={exportWorkflow} title="导出 JSON">
              📋 导出
            </button>
            <button className="btn btn-primary btn-sm" onClick={handleExecute}>
              ▶ 执行工作流
            </button>
          </div>
        </Panel>
      </ReactFlow>

      {/* NodePalette — 左侧浮层 */}
      <NodePalette
        visible={showPalette}
        onClose={() => setShowPalette(false)}
        onAddNode={handleAddNode}
      />

      {/* NDV 侧抽屉 */}
      <NodeDrawer
        node={selectedNode}
        onClose={() => setSelectedNode(null)}
        onConfigChange={handleConfigChange}
        onRunNode={onRunNode}
        inputData={null}
        outputData={nodeOutputs[selectedNode?.id] || null}
      />

      {/* 右键菜单 */}
      {contextMenu.show && (
        <ContextMenu
          x={contextMenu.x} y={contextMenu.y}
          nodeId={contextMenu.nodeId}
          onClose={() => setContextMenu(prev => ({ ...prev, show: false }))}
          onAction={handleContextAction}
        />
      )}
    </main>
  );
}
