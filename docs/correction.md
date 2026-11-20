# 取样目标校色（sample-target gradient correction）

设计师在画布上指出若干“这里最终应该是什么颜色”，系统据此为一个**已有的线性渐变**给出候选色阶，而不是让人逐个猜停止点。本流程是无状态的计算接口；只有确认提交才追加稿本，旧稿本与旧成品保持不变。

## 入口

| 方法与路径 | 输入 | 结果 |
|---|---|---|
| POST /v1/plates/{id}/corrections/probe?revision=N | layer_id, shape_id, targets | 200 候选与偏差，不写盘 |
| POST /v1/plates/{id}/corrections/commit | expected_revision, layer_id, shape_id, mode, targets | 201 追加稿本+所用候选 |

`targets` 是 1..64 个 `{x, y, color{r,g,b,a}}`，坐标为画布整数像素位置，颜色为 0..1 的 sRGB 直通值。被选 shape 的笔刷必须是 `linear`，否则 400。

## 取样有效性：不是任何点都能当色刷

每个目标在渲染器同一套四点子采样偏移（见 `raster.Render`）上做命中分析。子样点会被归入下列拒绝原因，全部子样都无效的目标出现在 `rejected_targets`，不参与求解：

- `outside_canvas`：坐标越出画布。
- `layer_masked_or_clipped`：所在图层的 mask/clip 在该点不通过。
- `outside_shape`：点不在所选轮廓内。
- `hole`：点落在某条子轮廓内且被填充规则（evenodd/nonzero）扣除，即镂空处。
- `zero_coverage`：该点笔刷 alpha 为 0，没有可校的颜料。
- `occluded`：目标笔刷在该点对最终像素的影响小于 `influenceFloor`（被上方不透明颜料完全遮住）。影响量由“含目标 shape 的合成结果”与“跳过该 shape 的反事实合成结果”逐通道比较得到，因此对仿射变换、图层顺序和部分覆盖都成立。
- `unreachable_under_blend` / `unreachable_brush_color`：合成链可逆向，但目标可见色在该混合方式或笔刷色域内不可达（例如在只能变亮的 screen 层下要求更暗）。

## 从可见色反推笔刷色

渲染是“sRGB 直通值 → 线性光预乘 → 仿射定位 → 形状 normal-over → 图层 opacity → normal/multiply/screen 混合”。求解器在**同一线性光预乘空间**沿这条链逆向：

1. 自上而下逆向目标图层之上的每个图层（含其 blend 与 group opacity）；
2. 逆向目标图层自身的 blend 与 opacity；
3. 在目标图层内部逆向排在所选 shape 之后的 shape；
4. 对所选 shape 做一次 normal-over 逆运算，得到该点所需的笔刷预乘色。

alpha 是结构性量（覆盖率、opacity、停止点 alpha 固定），只逆向 RGB。逆运算在 premul 色域（`0 <= rgb <= alpha`）外即判不可达，绝不产生非法颜色。screen/multiply 的逆用与 `paint.Over` 完全对应的闭式公式：

- normal：`s = w - d*(1-sa)`
- multiply：`s = (w-d)/(1-da+d)`
- screen：`s = (w-d)/(1-d)`（分母趋零且 `w!=d` 时判无影响、不可达）

其中 `d`、`s`、`w` 均为预乘线性光通道值。

## 求解与误差判据

把每个有效目标映射到渐变轴参数 `t`（由 shape 逆变换后的局部坐标投影到固定的 from→to 轴，轴不允许改变）。变量是停止点的预乘 RGB；停止点数量、每个停止点 alpha、轴方向均保持，仅允许调整停止点颜色，`colors_and_positions` 模式还允许移动内部停止点位置（端点仍固定 0/1，保持最小间距）。

- 目标函数是各点**加权线性光 RMSE**，权重取该点有效笔刷 alpha，使低覆盖点不主导拟合。
- 颜色子问题是逐通道带箱约束（`0..alpha`）的凸二次规划，用 Nesterov 投影梯度求一个确定解；对原停止点加极弱（`1e-6`）正则，使目标不足、解不唯一时偏向最小改动。
- 位置搜索用确定性坐标下降网格（内部停止点，端点不动），每次试位重新解颜色问题，保留加权 SSE 最低者；并列时保留更靠近原位置的解。

候选同时给两套误差：

- `linear_rmse` / `srgb_*`：分析预测（用候选笔刷重走完整合成链）相对目标的线性光与 sRGB 偏差。
- `png_*`：把候选文档交给**真实 PNG 渲染器**后，从实际 PNG 字节解码出的像素与目标的 8-bit 偏差（均值与最大通道差）。这是权威校样口径。

## 不能同时满足的目标

- 同一画布点要求不同颜色，或不同点投影到同一 `t` 却要求不同笔刷色（差异超过线性光阈值）时，相关目标在每个候选的 `conflict_indexes` 中标记，并在逐点偏差里置 `conflict`。
- 当某点即便最优拟合仍超阈值（线性 RMSE > 0.08 或 PNG 通道差 > 12），也标记为冲突，表示该约束在当前停止点数量边界内无法达成。系统不静默平均，而是把矛盾显式交回设计师。

## 解不唯一时如何选择

- 始终返回两个候选：`colors_only`（位置不动）与 `colors_and_positions`（允许移动内部停止点）。
- 仅当移动位置能把加权线性光 RMSE 降低超过 `1e-5` 时才推荐 `colors_and_positions`，否则推荐更保守的 `colors_only`，并在 `selection_note` 说明理由。
- 颜色层面的多解由最小改动正则与确定性优化顺序收敛到唯一答案；位置并列时取离原位最近者。因此相同输入永远得到相同候选与相同稿本摘要。

## 提交与不变量

`commit` 在服务端用相同输入重新求解，不接受客户端直接给的颜色，避免绕过色域与约束。它复用 `Revise` 的 `expected_revision` 并发契约与不可变历史规则：旧稿本不改写、旧成品字节不变，新稿本经同一 `validate.Compile` 准入。归档版面、容量上限、稿本上限等限制与普通修订一致。
