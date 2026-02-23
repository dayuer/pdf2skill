import { useState, useCallback, memo } from 'react';

/* ══════ NodePalette — n8n NodeCreator 简化版 ══════
 *
 * 对标 n8n 的节点选择面板:
 * - 分类 + 搜索过滤
 * - 点击或拖拽添加节点到画布
 */

// 节点分类
const CATEGORIES = [
  {
    name: '数据输入', icon: '📥',
    nodes: [
      { id: 'load', type: 'document_loader', icon: '📄', label: '文档加载', desc: '解析 PDF/TXT/EPUB' },
    ],
  },
  {
    name: '预处理', icon: '🔧',
    nodes: [
      { id: 'chunk', type: 'chunker', icon: '✂️', label: '智能切分', desc: '标题层次 + 语义边界' },
      { id: 'filter', type: 'semantic_filter', icon: '🔬', label: '语义密度筛', desc: '三维密度评分' },
      { id: 'schema', type: 'schema_gen', icon: '📐', label: 'Schema 生成', desc: 'R1 分析结构' },
    ],
  },
  {
    name: '提取 & 校验', icon: '⚡',
    nodes: [
      { id: 'extract', type: 'extractor', icon: '⚡', label: '技能提取', desc: '按 Schema 提取 Skill' },
      { id: 'validate', type: 'validator', icon: '✅', label: '校验', desc: '完整性 + 幻觉检测' },
    ],
  },
  {
    name: '后处理', icon: '📦',
    nodes: [
      { id: 'reduce', type: 'reducer', icon: '🔗', label: '聚类去重', desc: 'Tag 归一化 → 聚类' },
      { id: 'classify', type: 'classifier', icon: '🏷️', label: 'SKU 分类', desc: '事实/程序/关系' },
      { id: 'package', type: 'packager', icon: '📦', label: '打包输出', desc: 'mapping + 依赖图' },
    ],
  },
];

function NodeCard({ def, onAdd }) {
  const handleDragStart = useCallback((e) => {
    e.dataTransfer.setData('application/reactflow', def.id);
    e.dataTransfer.effectAllowed = 'move';
  }, [def.id]);

  return (
    <div className="np-card"
      draggable
      onDragStart={handleDragStart}
      onClick={() => onAdd(def)}>
      <span className="np-card-icon">{def.icon}</span>
      <div className="np-card-info">
        <div className="np-card-label">{def.label}</div>
        <div className="np-card-desc">{def.desc}</div>
      </div>
      <span className="np-card-add">+</span>
    </div>
  );
}

export default memo(function NodePalette({ onAddNode, visible, onClose }) {
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState(
    Object.fromEntries(CATEGORIES.map(c => [c.name, true]))
  );

  if (!visible) return null;

  const filtered = search.trim()
    ? CATEGORIES.map(cat => ({
        ...cat,
        nodes: cat.nodes.filter(n =>
          n.label.includes(search) || n.desc.includes(search) || n.type.includes(search)
        ),
      })).filter(cat => cat.nodes.length > 0)
    : CATEGORIES;

  return (
    <div className="np-panel">
      <div className="np-header">
        <span className="np-title">添加节点</span>
        <button className="np-close" onClick={onClose}>✕</button>
      </div>

      <div className="np-search">
        <span className="np-search-icon">🔍</span>
        <input
          type="text"
          placeholder="搜索节点..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          autoFocus
        />
      </div>

      <div className="np-list">
        {filtered.map(cat => (
          <div key={cat.name} className="np-category">
            <div className="np-cat-header"
              onClick={() => setExpanded(prev => ({ ...prev, [cat.name]: !prev[cat.name] }))}>
              <span>{cat.icon} {cat.name}</span>
              <span className={`np-cat-arrow${expanded[cat.name] ? ' open' : ''}`}>▸</span>
            </div>
            {expanded[cat.name] && (
              <div className="np-cat-nodes">
                {cat.nodes.map(def => (
                  <NodeCard key={def.id} def={def} onAdd={onAddNode} />
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
});

// 导出分类供其他组件使用
export { CATEGORIES };
