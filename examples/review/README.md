# 区域批注审阅示例

本目录由 `python3 scripts/annotation_demo.py` 通过真实 HTTP 服务重新生成，演示退稿区域批注的完整旅程。所有图片与审阅单都来自实际渲染路径，不是手工构造的样例。

## 旅程

1. 稿本1 渲染 PNG（`proof-revision1.png`），开校样 `proof-r1`，记录三条区域批注：
   - `note-cyan-window`：区域中心在青色窗口形状上，锚定 `cyan-glaze/cyan-window`，捕获当时仿射矩阵与局部坐标；
   - `note-amber-body`：锚定 `amber-sheet/amber-contour`；
   - `note-corner`：中心在形状外，为无锚点自由批注。
2. 校样裁决为 rejected，批注冻结；原校样 PNG 与原始坐标永久保留，其成品不可撤销。
3. 稿本2（`draft-revision2.png`）修改 `cyan-window` 的仿射矩阵。投影结果：`note-cyan-window` → relocated（按新矩阵重算，虚线待确认），`note-amber-body` → anchored（实线），`note-corner` → absolute。
4. 稿本3（`draft-revision3.png`）画布改为 200×150 并删除青色图层。投影结果：`note-cyan-window` → orphaned（锚定对象消失，保留原坐标不重贴），其余 → rescaled（按画布比例重放）。

## 文件

| 文件 | 内容 |
|---|---|
| `proof-revision1.png` | 退稿校样的终局原始图片，192×144，不可变 |
| `draft-revision2.png` | 仿射修订后的新稿图片 |
| `draft-revision3.png` | 画布变更并删除图层后的新稿图片，200×150 |
| `review-sheet-revision2.svg` | 审阅单：左列原图+原始批注框，右列稿本2+投影框，附图例与台账 |
| `review-sheet-revision3.svg` | 审阅单：同上，对照稿本3，含 orphaned/rescaled 状态 |
| `review-notes.json` | 两次投影的完整 JSON 台账，含每条批注的 status、unverified、outside_canvas 与 reason |

SVG 可直接用浏览器打开。实线绿框表示 anchored；虚线按颜色区分 relocated（橙）、rescaled（青）、orphaned（红）；徽标数字与底部台账序号对应，台账保留完整批注 ID 与原文。
