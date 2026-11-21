# 观测表（由 `acceptance/harness/report.py` 从原始 JSON 生成）

参考模型校验：linear 空间模型对真实 PNG 的最大差 **1**（容差 2，故 PNG 可作基准）；同一结构换成 sRGB 画布合成后最大差 **61**，这就是后面反复引用的格式固有差。两个模型都只用了契约里的公式，没有调用服务代码。

## 1. 每种机制在各查看器下的最好结果

Δ 为与 Prism PNG 的最大通道差（在 0–255 上）。`模型` 列说明该查看器精确落在哪个独立合成模型上：`线性` = Prism 记录的模型，`sRGB` = 直接对 sRGB 值做source-over/混合。`固有差` 是两种模型在同一探针上的差——任何只做 sRGB 画布合成的查看器都不可能比它更接近 Prism。

| 机制 | 判定 | 最好 Δ | 固有差 | 能表现该机制的查看器 | 达到保真度的查看器 | 模型归属 |
|---|---|---:|---:|---|---|---|
| 实色填充 | SVG（有条件） | 0 | 0 | cairosvg, chromium, firefox, librsvg, resvg | cairosvg, chromium, firefox, librsvg, resvg | — |
| 仿射实色 | SVG（有条件） | 0 | 0 | cairosvg, chromium, firefox, librsvg, resvg | cairosvg, chromium, firefox, librsvg, resvg | — |
| multiply 叠在已合成图层上 | SVG（有条件） | 3 | 2 | chromium, firefox, librsvg, resvg | chromium, firefox, librsvg, resvg | follows-srgb-canvas=chromium,firefox,librsvg,resvg |
| multiply 叠在文档底色上 | SVG（有条件） | 1 | 1 | chromium, firefox, librsvg, resvg | chromium, firefox, librsvg, resvg | follows-srgb-canvas=chromium,firefox,librsvg,resvg |
| screen 叠在文档底色上 | 结构看 SVG，颜色仅 PNG | 25 | 25 | chromium, firefox, librsvg, resvg | — | follows-srgb-canvas=chromium,firefox,librsvg,resvg |
| 组隔离不变量 | SVG（有条件） | 0 | - | cairosvg, chromium, firefox, librsvg, resvg | cairosvg, chromium, firefox, librsvg, resvg | follows-srgb-canvas=chromium,firefox,librsvg,resvg; mixed=cairosvg |
| 组内 multiply 展开 | SVG（有条件） | 4 | 4 | chromium, firefox, librsvg, resvg | chromium, firefox, librsvg, resvg | follows-srgb-canvas=chromium,firefox,librsvg,resvg |
| 组内半透明叠加 | 结构看 SVG，颜色仅 PNG | 15 | 16 | cairosvg, chromium, firefox, librsvg, resvg | — | follows-srgb-canvas=chromium,firefox,librsvg,resvg; mixed=cairosvg |
| 不透明渐变线性光插值 | SVG（有条件） | 1 | 61 | chromium, firefox | chromium, firefox | follows-prism-linear=chromium,firefox |
| 透明渐变预乘插值 | SVG（有条件） | 1 | 0 | firefox | firefox | — |
| 透明底上的 multiply/screen | SVG（有条件） | 0 | 0 | cairosvg, chromium, firefox, librsvg, resvg | cairosvg, chromium, firefox, librsvg, resvg | follows-srgb-canvas=chromium,firefox,librsvg,resvg; mixed=cairosvg |
| 镂空孔洞内部 | SVG（有条件） | 0 | 0 | cairosvg, chromium, firefox, librsvg, resvg | cairosvg, chromium, firefox, librsvg, resvg | — |
| 镂空环体 | SVG（有条件） | 0 | 0 | cairosvg, chromium, firefox, librsvg, resvg | cairosvg, chromium, firefox, librsvg, resvg | — |
| 镂空边缘扫描 | SVG（有条件） | 7 | - | cairosvg, chromium, firefox, librsvg, resvg | cairosvg, chromium, firefox, librsvg, resvg | — |

## 2. 各查看器 × 场景：语义（S）与保真度（F）

S = 该查看器表现出该机制；F = 绝对颜色也在阈值内。`own`/`img`/`doc`/`post` 是查看方式，见第 4 节。

| 场景 | librsvg | resvg | cairosvg | chromium | firefox | imagemagick |
|---|---|---|---|---|---|---|
| a-solid-control | ownS✓F✓ postS✓F✓ | ownS✓F✓ postS✓F✓ | ownS✓F✓ postS✓F✓ | docS–F✓ imgS✓F✓ ownS✓F✓ postS✓F✓ | docS–F✓ imgS✓F✓ ownS✓F✓ | ownS✓F✗ postS✓F✗ |
| b-backdrop-blend | ownS✓F✗ postS✓F✗ | ownS✓F✗ postS✓F✗ | ownS✗F✗ postS✗F✗ | docS✓F✗ imgS✓F✗ ownS✓F✗ postS✓F✗ | docS✓F✗ imgS✓F✗ ownS✓F✗ | ownS✗F✗ postS✗F✗ |
| c-group-isolation | ownS✓F✗ postS✓F✗ | ownS✓F✗ postS✓F✗ | ownS✗F✗ postS✗F✗ | docS✓F✗ imgS✓F✗ ownS✓F✗ postS✓F✗ | docS✓F✗ imgS✓F✗ ownS✓F✗ | ownS✗F✗ postS✗F✗ |
| d-gradient-opaque | ownS✗F✗ postS✗F✗ | ownS✗F✗ postS✗F✗ | ownS✗F✗ postS✗F✗ | docS✓F✓ imgS✓F✓ ownS✓F✓ postS✓F✓ | docS✓F✓ imgS✓F✓ ownS✓F✓ | ownS✗F✗ postS✗F✗ |
| e-gradient-alpha | ownS✗F✗ postS✗F✗ | ownS✗F✗ postS✗F✗ | ownS✗F✗ postS✗F✗ | docS✗F✗ imgS✗F✗ ownS✗F✗ postS✗F✗ | docS✓F✓ imgS✓F✓ ownS✓F✓ | ownS✗F✗ postS✗F✗ |
| f-transparent-backdrop | ownS–F✓ postS–F✓ | ownS–F✗ postS–F✓ | ownS–F✓ postS–F✓ | docS–F✓ imgS–F✓ ownS–F✗ postS–F✓ | docS–F✓ imgS–F✓ ownS–F✓ | ownS–F✓ postS–F✓ |
| g-knockout-edge | ownS✓F✓ postS✓F✓ | ownS✓F✓ postS✓F✓ | ownS✓F✓ postS✓F✓ | docS–F✓ imgS✓F✓ ownS✓F✓ postS✓F✓ | docS–F✓ imgS✓F✓ ownS✓F✓ | ownS✓F✗ postS✓F✗ |

## 3. 三个关键机制的原始像素

### 3.1 组隔离不变量：同色两个不透明形状叠在同一个 0.5 不透明度层里

重叠处与单形状处输入完全相同，隔离正确时两者必须相等。

单形状像素还必须真的被 0.5 组不透明度压暗过；否则“三点相等”只是因为查看器整体忽略了组不透明度。Prism 此处为 181,106,116。

| 查看器 | 重叠 | 形状 C 单独 | 形状 D 单独 | 三点最大差 | 单形状被压暗 |
|---|---|---|---|---:|---|
| librsvg | [166, 103, 102] | [166, 103, 102] | [166, 103, 102] | 0 | 是 |
| resvg | [166, 102, 102] | [166, 102, 102] | [166, 102, 102] | 0 | 是 |
| cairosvg | [166, 102, 102] | [166, 102, 102] | [166, 102, 102] | 0 | 是 |
| chromium | [166, 102, 101] | [166, 102, 101] | [166, 102, 101] | 0 | 是 |
| firefox | [166, 102, 101] | [166, 102, 101] | [166, 102, 101] | 0 | 是 |
| imagemagick | [230, 76, 51] | [230, 76, 51] | [230, 76, 51] | 0 | 否（按全不透明画出） |

### 3.2 透明渐变：从两个页面颜色反解直线色与 alpha

洋红(1,0,1,1) → 绿(0,1,0,0)。预乘插值下直线色应始终是洋红，只有 alpha 下降；把颜色和 alpha 各自独立插值则直线色会走向灰绿。Firefox 没有 alpha 截图通道，下列数值由白/黑两次截图反解，因此仍然可用。

| 查看器 | 反解 alpha | 反解直线色 | 直线色仍是洋红 |
|---|---:|---|---|
| librsvg | 0.49 | [122, 128, 122] | 否 |
| resvg | 0.49 | [126, 131, 126] | 否 |
| cairosvg | 0.49 | [124, 131, 124] | 否 |
| chromium | 0.49 | [186, 188, 186] | 否 |
| firefox | 0.49 | [255, 0, 255] | 是 |
| imagemagick | 1.0 | [0, 0, 0] | 否 |

### 3.3 换底漂移：同一不透明像素在三张页面颜色下的取值

Prism 在自己的透明文档底上合成，不透明结果不应随查看器页面改变。下表是各查看器在**全部场景**的不透明探针上的最大换底漂移（0 = 不受影响）；非零只出现在 `f-transparent-backdrop` 上。

| 查看器 | own 内联/截图 | img 嵌入 | post 事后叠底 |
|---|---:|---:|---:|
| librsvg | 0 | — | 0 |
| resvg | 204 | — | 0 |
| cairosvg | 0 | — | 0 |
| chromium | 204 | — | 0 |
| firefox | 0 | — | 0 |
| imagemagick | 0 | — | 0 |

`doc`（直接打开文件）只有一个页面，换底不变量在定义上不可测，故不列。它的作用是另一件事：验证“双击打开”这种最自然的方式是否会把页面底色卷进混合；结果见 §2 的 `docS–F✓` 组合与下面的场景级结论。

## 3.4 场景级结论

| 场景 | 结论 | 逐机制 |
|---|---|---|
| a-solid-control | SVG（有条件） | solid-fill=SVG（有条件）; affine-transform=SVG（有条件） |
| b-backdrop-blend | 结构看 SVG，颜色仅 PNG | multiply-over-composited=SVG（有条件）; multiply-over-backdrop=SVG（有条件）; screen-over-backdrop=结构看 SVG，颜色仅 PNG |
| c-group-isolation | 结构看 SVG，颜色仅 PNG | group-isolation=SVG（有条件）; group-multiply=SVG（有条件）; group-alpha=结构看 SVG，颜色仅 PNG |
| d-gradient-opaque | SVG（有条件） | gradient-linear=SVG（有条件） |
| e-gradient-alpha | SVG（有条件） | gradient-alpha=SVG（有条件） |
| f-transparent-backdrop | SVG（有条件） | blend-over-transparent=SVG（有条件） |
| g-knockout-edge | SVG（有条件） | knockout-hole=SVG（有条件）; knockout-body=SVG（有条件）; knockout-edge=SVG（有条件） |

## 4. 格式能力实验：`color-interpolation` 能表达哪一部分线性光

Prism 的合成在线性光里；SVG 根节点带 `color-interpolation="linearRGB"`。下面只改这一个属性，其余字节不变。

| 浏览器 | 场景 | 原样 | 根改 sRGB | 删除该属性 | 只给渐变加 linearRGB | 只给渐变加 sRGB |
|---|---|---|---|---|---|---|
| chromium | gradient | (184, 190, 0) | (123, 132, 0) | (123, 132, 0) | (184, 190, 0) | (123, 132, 0) |
| chromium | multiply | (143, 112, 62) | (143, 112, 62) | (143, 112, 62) | (143, 112, 62) | (143, 112, 62) |
| chromium | screen | (153, 172, 204) | (153, 172, 204) | (153, 172, 204) | (153, 172, 204) | (153, 172, 204) |
| firefox | gradient | (185, 190, 0) | (123, 132, 0) | (123, 132, 0) | (185, 190, 0) | (123, 132, 0) |
| firefox | multiply | (143, 112, 62) | (143, 112, 62) | (143, 112, 62) | (143, 112, 62) | (143, 112, 62) |
| firefox | screen | (153, 172, 204) | (153, 172, 204) | (153, 172, 204) | (153, 172, 204) | (153, 172, 204) |

结论：渐变中点随该属性移动（184→123），multiply/screen 的像素完全不动。也就是说 SVG 只能把线性光用于色标插值，无法把图层混合放到线性光里。


## 5. 整色域固有差扫描

遍历全部 256×256 背底/源色对，比较同一公式在线性光与 sRGB 下的结果。阈值 8（0–255 通道差）。

| 运算 | 固有差最大值 | 最坏组合 | ≤8 的比例 | ≤16 的比例 |
|---|---:|---|---:|---:|
| multiply | 7.43 | 背底 62、源 62（线性 8 / sRGB 15） | 100.0% | 100.0% |
| screen | 26.73 | 背底 101、源 101（线性 135 / sRGB 162） | 41.4% | 69.3% |
| 半透明 over（α=25/50/75%） | 73.21 | 背底 0、源 255、α=0.25（线性 137 / sRGB 64） | 51.9% | 68.7% |

multiply 的固有差在整个色域内不超过阈值；screen 与半透明 over 的固有差明显超过阈值，只有颜色恰好接近时才可能通过，不能作为可依赖的性质。

