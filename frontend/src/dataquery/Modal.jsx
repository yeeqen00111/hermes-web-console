/** 通用弹窗外壳 —— 沿用项目既有的 `.modal-mask` / `.modal` / `.modal-actions` 样式。
 *
 *  抽出来是为了**阶段 5（知识库 / SQL 模板）复用**：三页都是「列表 + 弹窗编辑」，
 *  形状一样，只差列与表单字段。所以弹窗壳、列表、删除确认都做成可复用的。
 *
 *  点遮罩关闭；点卡片本身不关（stopPropagation）。
 */
export default function Modal({ title, wide, onClose, children, actions }) {
  return (
    <div className="modal-mask" onClick={onClose}>
      <div className={`modal${wide ? " wide" : ""}`} onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        {children}
        <div className="modal-actions">{actions}</div>
      </div>
    </div>
  );
}
