# 拓扑案例册（棱版合法输入）

本册只用 `POST /v1/plates` 的合法稿本构成，未新增布尔运算或填充转换。
所有数字由 `tools/verify.py` 从稿本重算；图像由真实服务 `cmd/prismplate` 生成。

- 案例数：**35**（A 5，B 6，C 6，D 4，E 14）
- 同有向面积、不同拓扑的对照对：**23**
- 判定：determined **20**，determined-with-contact **13**，undetermined **2**
- 朴素 PNG 连通统计（4/8 两种约定）互相冲突的案例：**4**
- 几何精确但 512 像素校样必然看不见的案例（填充面积或间隙 < 1 像素）：**3**

每例给出：精确曲线有向面积、折线有向面积、填充面积、开/闭两种接触约定下的连通块与孔数、状态与原因、以及产物文件。

## A 有向面积与自交/复合

### case-a1-bowtie — A1 领结自交：有向面积 5760，两块实心

- 问题：有向面积的正负/大小能否说明孔数
- 主张：单环自交成两瓣，绕向相反：有向面积 7680-1920，填充 7680+1920；两瓣只在交点相接
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：5760
- 填充面积（折线精确排列）：9600
- 连通块：开 **2** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：component split depends on point/edge contact
- 折线误差界 0；最小非接触间距 —（全部接触）
- 区域与不确定标注：`annotations/case-a1-bowtie.svg`（2 个填充区域，1 个边界标注）
- 产物：`out/case-a1-bowtie.png`（1489 B，sha256 `e13bf48a53d928b3…`）、`out/case-a1-bowtie.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-a2-nested-opposite — A2 内环反向且完全包含：有向面积 5760，一个孔

- 问题：反向内环是否一定成孔
- 主张：外环 7680、内环 -1920；有向面积等于填充面积 5760，孔洞恰好 1 个
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：5760
- 填充面积（折线精确排列）：5760
- 孔面积合计（开集读法）：1920
- 连通块：开 **1** / 闭 **1**；孔：开 **1** / 闭 **1**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 20
- 区域与不确定标注：`annotations/case-a2-nested-opposite.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-a2-nested-opposite.png`（647 B，sha256 `fff412245732575c…`）、`out/case-a2-nested-opposite.svg`
- PNG 朴素计数：4/8 约定 1 块 / 1 孔，8/4 约定 1 块 / 1 孔

### case-a3-crossing-opposite — A3 反向内环穿出外环：有向面积 5760，孔消失

- 问题：同样的两环，平移是否改变孔数
- 主张：内环左边越出外环；有向面积仍 5760，填充 7040，孔与连通块随接触约定改变
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：5760
- 填充面积（折线精确排列）：7040
- 孔面积合计（开集读法）：1280
- 连通块：开 **2** / 闭 **1**；孔：开 **1** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：component split depends on point/edge contact；hole count depends on point/edge contact
- 折线误差界 0；最小非接触间距 20
- 区域与不确定标注：`annotations/case-a3-crossing-opposite.svg`（3 个填充区域，2 个边界标注）
- 产物：`out/case-a3-crossing-opposite.png`（654 B，sha256 `65908617e54320d7…`）、`out/case-a3-crossing-opposite.svg`
- PNG 朴素计数：4/8 约定 2 块 / 0 孔，8/4 约定 1 块 / 1 孔（两者不一致，不能作为结论）

### case-a4-zero-bowtie — A4 对称领结：有向面积 0，填充 6144

- 问题：有向面积为 0 是否等于没有填充
- 主张：两瓣等面积、反向绕，有向面积严格为 0；两瓣各 3072，只在交点相接
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：0
- 填充面积（折线精确排列）：6144
- 连通块：开 **2** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：component split depends on point/edge contact
- 折线误差界 0；最小非接触间距 —（全部接触）
- 区域与不确定标注：`annotations/case-a4-zero-bowtie.svg`（2 个填充区域，1 个边界标注）
- 产物：`out/case-a4-zero-bowtie.png`（1383 B，sha256 `0b65af9e0aef3891…`）、`out/case-a4-zero-bowtie.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-a5-zero-disjoint — A5 两个反向分离形状：有向面积 0，两块实心

- 问题：有向面积 0 能否说明是空图
- 主张：两块各 3200、绕向相反，和严格为 0；两块分离，孔 0
- 填充规则：`nonzero/nonzero`；形体数 2；镜像：是
- 有向面积（曲线精确，画布）：0
- 填充面积（折线精确排列）：6400
- 连通块：开 **2** / 闭 **2**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 34
- 区域与不确定标注：`annotations/case-a5-zero-disjoint.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-a5-zero-disjoint.png`（656 B，sha256 `67381cd103048cfe…`）、`out/case-a5-zero-disjoint.svg`
- PNG 朴素计数：4/8 约定 2 块 / 0 孔，8/4 约定 2 块 / 0 孔

## B 朝向、包含、分离、镜像、并集

### case-b1-same-orientation — B1 内环同向且包含：有向面积 5760，零孔

- 问题：有向面积相同是否等于拓扑相同
- 主张：与 A2 同为 5760；同向内环不挖孔，填充 4800
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：5760
- 填充面积（折线精确排列）：4800
- 连通块：开 **1** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 18
- 区域与不确定标注：`annotations/case-b1-same-orientation.svg`（1 个填充区域，0 个边界标注）
- 产物：`out/case-b1-same-orientation.png`（627 B，sha256 `b556990aefa9a71a…`）、`out/case-b1-same-orientation.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-b2-mirror-disjoint — B2 镜像 + 外侧小环：有向面积 -3840，两块实心

- 问题：镜像是否改变拓扑
- 主张：镜像行列式 -1，画布有向面积 -3840；两环分离，连通块 2、孔 0
- 填充规则：`nonzero`；形体数 1；镜像：是
- 有向面积（曲线精确，画布）：-3840
- 填充面积（折线精确排列）：5760
- 连通块：开 **2** / 闭 **2**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 24
- 区域与不确定标注：`annotations/case-b2-mirror-disjoint.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-b2-mirror-disjoint.png`（648 B，sha256 `428dbd921639ed05…`）、`out/case-b2-mirror-disjoint.svg`
- PNG 朴素计数：4/8 约定 2 块 / 0 孔，8/4 约定 2 块 / 0 孔

### case-b3-mirror-nested — B3 镜像 + 内环包含：有向面积 -3840，一个孔

- 问题：同有向面积是否同孔数
- 主张：与 B2 同为 -3840；内环被包含，连通块 1、孔 1、填充 3840
- 填充规则：`nonzero`；形体数 1；镜像：是
- 有向面积（曲线精确，画布）：-3840
- 填充面积（折线精确排列）：3840
- 孔面积合计（开集读法）：960
- 连通块：开 **1** / 闭 **1**；孔：开 **1** / 闭 **1**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 18
- 区域与不确定标注：`annotations/case-b3-mirror-nested.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-b3-mirror-nested.png`（647 B，sha256 `f2bbe150fa846ba1…`）、`out/case-b3-mirror-nested.svg`
- PNG 朴素计数：4/8 约定 1 块 / 1 孔，8/4 约定 1 块 / 1 孔

### case-b4-direct-nested — B4 同稿不镜像：有向面积 +3840，一个孔

- 问题：镜像是否只改符号
- 主张：与 B3 同一本地轮廓；镜像只把有向面积翻成 -3840，连通块与孔数不变
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：3840
- 填充面积（折线精确排列）：3840
- 孔面积合计（开集读法）：960
- 连通块：开 **1** / 闭 **1**；孔：开 **1** / 闭 **1**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 18
- 区域与不确定标注：`annotations/case-b4-direct-nested.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-b4-direct-nested.png`（647 B，sha256 `cbf5e7b565763a4d…`）、`out/case-b4-direct-nested.svg`
- PNG 朴素计数：4/8 约定 1 块 / 1 孔，8/4 约定 1 块 / 1 孔

### case-b5-two-shapes — B5 两个独立形状：和 +4800，两块实心

- 问题：多形状的有向面积能否相加后判断孔数
- 主张：逐形状有向面积 2400+2400=4800；两块分离，连通块 2、孔 0
- 填充规则：`nonzero/nonzero`；形体数 2；镜像：否
- 有向面积（曲线精确，画布）：4800
- 填充面积（折线精确排列）：4800
- 连通块：开 **2** / 闭 **2**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 40
- 区域与不确定标注：`annotations/case-b5-two-shapes.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-b5-two-shapes.png`（656 B，sha256 `16b7281ca95ffa28…`）、`out/case-b5-two-shapes.svg`
- PNG 朴素计数：4/8 约定 2 块 / 0 孔，8/4 约定 2 块 / 0 孔

### case-b6-one-shape — B6 单个形状：有向面积 +4800，一块实心

- 问题：同有向面积是否同连通块数
- 主张：与 B5 同为 4800；这里一块实心，连通块 1、孔 0
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：4800
- 填充面积（折线精确排列）：4800
- 连通块：开 **1** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 60
- 区域与不确定标注：`annotations/case-b6-one-shape.svg`（1 个填充区域，0 个边界标注）
- 产物：`out/case-b6-one-shape.png`（627 B，sha256 `c4726bcff8147d8a…`）、`out/case-b6-one-shape.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

## C 曲线

### case-c1-oval-nested — C1 曲线复合轮廓：精确有向面积 17520，一个孔

- 问题：曲线稿的有向面积怎样算才不依赖像素
- 主张：三次曲线精确积分 17520（外 20160、内 -2640）；16/24 折线拓扑一致但面积略小
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：17520
- 有向面积（16/24 折线）：17507.833333（差 +12.166667）
- 填充面积（折线精确排列）：`105047/6` = 17507.833333
- 孔面积合计（开集读法）：`15829/6` = 2638.166667
- 连通块：开 **1** / 闭 **1**；孔：开 **1** / 闭 **1**
- 判定：**determined**；可见性：visible
- 曲线证书：shape-ovals vs shape-ovals → 真实曲线 `separated`，折线传递 `faithful`
- 折线误差界 0.0498377；最小非接触间距 1.95917
- 区域与不确定标注：`annotations/case-c1-oval-nested.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-c1-oval-nested.png`（2330 B，sha256 `5e7545766d828198…`）、`out/case-c1-oval-nested.svg`
- PNG 朴素计数：4/8 约定 1 块 / 1 孔，8/4 约定 1 块 / 1 孔

### case-c2-oval-crossing — C2 曲线内环穿出外环：有向面积 17520，两次横穿

- 问题：曲线平移后面积是否变
- 主张：精确有向面积仍 17520；两环横向相交，填充集合随接触约定变化
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：17520
- 有向面积（16/24 折线）：17507.833333（差 +12.166667）
- 填充面积（折线精确排列）：`31421453963/1739880` = 18059.552362
- 孔面积合计（开集读法）：`8220261937/3479760` = 2362.307153
- 连通块：开 **2** / 闭 **1**；孔：开 **1** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：component split depends on point/edge contact；hole count depends on point/edge contact
- 曲线证书：shape-ovals vs shape-ovals → 真实曲线 `crossing`，折线传递 `faithful`
- 折线误差界 0.0498377；最小非接触间距 0.698512
- 区域与不确定标注：`annotations/case-c2-oval-crossing.svg`（3 个填充区域，2 个边界标注）
- 产物：`out/case-c2-oval-crossing.png`（2264 B，sha256 `ebf851ede841511b…`）、`out/case-c2-oval-crossing.svg`
- PNG 朴素计数：4/8 约定 2 块 / 0 孔，8/4 约定 1 块 / 1 孔（两者不一致，不能作为结论）

### case-c6-oval-shallow-cross — C6 曲线浅交：重叠 1e-4，有向面积 17520

- 问题：浅交与近切是否可判
- 主张：两曲线真实相交，交叠深度约 1e-4，小于折线误差界 b；证书无法在 b 内确认绕数翻转
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：17520
- 有向面积（16/24 折线）：17507.833333（差 +12.166667）
- 填充面积（折线精确排列）：`1382092332824899/78941250000` = 17507.859742
- 孔面积合计（开集读法）：`416518264050101/157882500000` = 2638.153463
- 连通块：开 **2** / 闭 **1**；孔：开 **1** / 闭 **0**
- 判定：**undetermined**；可见性：below-pixel
- 原因：component split depends on point/edge contact；hole count depends on point/edge contact；minimum non-contact clearance 9.99572e-05 canvas units is under 1 px, so adjacent regions merge in raster；curve pair shape-ovals/shape-ovals: true relation unresolved, transfer to flattened path not certified
- 曲线证书：shape-ovals vs shape-ovals → 真实曲线 `unresolved`，折线传递 `not_certified`
- 折线误差界 0.0498377；最小非接触间距 9.99572e-05
- 区域与不确定标注：`annotations/case-c6-oval-shallow-cross.svg`（3 个填充区域，3 个边界标注）
- 产物：`out/case-c6-oval-shallow-cross.png`（2302 B，sha256 `dee94182aac54ff0…`）、`out/case-c6-oval-shallow-cross.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-c3-oval-near-tangent — C3 曲线近切：间隙 1e-4，有向面积 17520

- 问题：曲线要近似到什么程度才够判定拓扑
- 主张：两曲线右端间隙约 1e-4，远小于 16/24 折线误差界；折线会造出交点，真实拓扑不能由折线判定
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：17520
- 有向面积（16/24 折线）：17507.833333（差 +12.166667）
- 填充面积（折线精确排列）：`86380762689972193/4933828125000` = 17507.858098
- 孔面积合计（开集读法）：`26032399614715307/9867656250000` = 2638.154285
- 连通块：开 **4** / 闭 **1**；孔：开 **1** / 闭 **0**
- 判定：**undetermined**；可见性：below-pixel
- 原因：component split depends on point/edge contact；hole count depends on point/edge contact；minimum non-contact clearance 9.99475e-05 canvas units is under 1 px, so adjacent regions merge in raster；curve pair shape-ovals/shape-ovals: true relation separated, transfer to flattened path not certified
- 曲线证书：shape-ovals vs shape-ovals → 真实曲线 `separated`，折线传递 `not_certified`
- 折线误差界 0.0498377；最小非接触间距 9.99475e-05
- 区域与不确定标注：`annotations/case-c3-oval-near-tangent.svg`（5 个填充区域，5 个边界标注）
- 产物：`out/case-c3-oval-near-tangent.png`（2302 B，sha256 `dee94182aac54ff0…`）、`out/case-c3-oval-near-tangent.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-c4-oval-pair — C4 两枚分离椭圆：有向面积 17520，两块实心、零孔

- 问题：有向面积与填充面积都相同是否拓扑相同
- 主张：精确面积 6720+10800=17520，填充同为 17520；与 C1 相比是 2 块、0 孔
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：17520
- 有向面积（16/24 折线）：17507.833333（差 +12.166667）
- 填充面积（折线精确排列）：`105047/6` = 17507.833333
- 连通块：开 **2** / 闭 **2**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 曲线证书：shape-ovals vs shape-ovals → 真实曲线 `separated`，折线传递 `faithful`
- 折线误差界 0.0350355；最小非接触间距 3.4281
- 区域与不确定标注：`annotations/case-c4-oval-pair.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-c4-oval-pair.png`（2231 B，sha256 `eb5e8817c6d66304…`）、`out/case-c4-oval-pair.svg`
- PNG 朴素计数：4/8 约定 2 块 / 0 孔，8/4 约定 2 块 / 0 孔

### case-c5-oval-mirror — C5 曲线稿镜像：有向面积 -17520，一个孔

- 问题：镜像曲线是否改变孔数
- 主张：与 C1 同一本地稿；有向面积变号 -17520，拓扑仍是 1 块 1 孔
- 填充规则：`nonzero`；形体数 1；镜像：是
- 有向面积（曲线精确，画布）：-17520
- 有向面积（16/24 折线）：-17507.833333（差 -12.166667）
- 填充面积（折线精确排列）：`105047/6` = 17507.833333
- 孔面积合计（开集读法）：`15829/6` = 2638.166667
- 连通块：开 **1** / 闭 **1**；孔：开 **1** / 闭 **1**
- 判定：**determined**；可见性：visible
- 曲线证书：shape-ovals-mirrored vs shape-ovals-mirrored → 真实曲线 `separated`，折线传递 `faithful`
- 折线误差界 0.0498377；最小非接触间距 1.95917
- 区域与不确定标注：`annotations/case-c5-oval-mirror.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-c5-oval-mirror.png`（2363 B，sha256 `addbd1acfbe2bc08…`）、`out/case-c5-oval-mirror.svg`
- PNG 朴素计数：4/8 约定 1 块 / 1 孔，8/4 约定 1 块 / 1 孔

## D 填充规则与重合边界

### case-d1-star-nonzero — D1 五角星 nonzero：有向面积 13697，一块实心

- 问题：有向面积能否区分填充规则
- 主张：中心五边形绕数 2；nonzero 仍填充，五角与中心连成 1 块、孔 0
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：13697
- 填充面积（折线精确排列）：`8898824447993/850120789` = 10467.717721
- 连通块：开 **1** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 —（全部接触）
- 区域与不确定标注：`annotations/case-d1-star-nonzero.svg`（1 个填充区域，5 个边界标注）
- 产物：`out/case-d1-star-nonzero.png`（2517 B，sha256 `1c2fa1269f973e42…`）、`out/case-d1-star-nonzero.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-d2-star-evenodd — D2 五角星 evenodd：有向面积 13697，五角加中心孔

- 问题：同一路径换规则是否改孔数
- 主张：有向面积同为 13697；中心被挖空，五角只在交点相接
- 填充规则：`evenodd`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：13697
- 填充面积（折线精确排列）：`6153544449053/850120789` = 7238.435442
- 孔面积合计（开集读法）：`2745279998940/850120789` = 3229.282279
- 连通块：开 **5** / 闭 **1**；孔：开 **1** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：component split depends on point/edge contact；hole count depends on point/edge contact
- 折线误差界 0；最小非接触间距 —（全部接触）
- 区域与不确定标注：`annotations/case-d2-star-evenodd.svg`（6 个填充区域，5 个边界标注）
- 产物：`out/case-d2-star-evenodd.png`（3161 B，sha256 `84f8eef259677901…`）、`out/case-d2-star-evenodd.svg`
- PNG 朴素计数：4/8 约定 2 块 / 0 孔，8/4 约定 1 块 / 1 孔（两者不一致，不能作为结论）

### case-d3-duplicate-nonzero — D3 完全重合双环 nonzero：有向面积 15360，实心 7680

- 问题：重合边界的有向面积是否有意义
- 主张：两环完全重合，有向面积是单环两倍 15360，填充仍是 7680；边界处处重合，按重合约定计数
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：15360
- 填充面积（折线精确排列）：7680
- 连通块：开 **1** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：4 vertex contact(s) between distinct boundary elements；4 coincident edge overlap(s) between distinct shapes
- 折线误差界 0；最小非接触间距 80
- 区域与不确定标注：`annotations/case-d3-duplicate-nonzero.svg`（1 个填充区域，8 个边界标注）
- 产物：`out/case-d3-duplicate-nonzero.png`（628 B，sha256 `de38cdb8ab8fca7d…`）、`out/case-d3-duplicate-nonzero.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-d4-duplicate-evenodd — D4 完全重合双环 evenodd：有向面积 15360，填充为空

- 问题：同一退化边界换规则会怎样
- 主张：有向面积仍 15360，绕数处处为偶，填充为空，连通块 0、孔 0；重合边界同样是退化
- 填充规则：`evenodd`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：15360
- 填充面积（折线精确排列）：0
- 连通块：开 **0** / 闭 **0**；孔：开 **0** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：4 vertex contact(s) between distinct boundary elements；4 coincident edge overlap(s) between distinct shapes
- 折线误差界 0；最小非接触间距 80
- 区域与不确定标注：`annotations/case-d4-duplicate-evenodd.svg`（0 个填充区域，8 个边界标注）
- 产物：`out/case-d4-duplicate-evenodd.png`（599 B，sha256 `6f225cdb0ddefdd6…`）、`out/case-d4-duplicate-evenodd.svg`
- PNG 朴素计数：4/8 约定 0 块 / 0 孔，8/4 约定 0 块 / 0 孔

## E 退化与接触家族

### case-e1-hole-inside — E1 孔完全在内：有向面积 6528，一个孔

- 问题：孔洞与边界接触时孔数是多少
- 主张：反向菱形环完全在外环内：孔洞 1，填充 6528
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：6528
- 填充面积（折线精确排列）：6528
- 孔面积合计（开集读法）：1152
- 连通块：开 **1** / 闭 **1**；孔：开 **1** / 闭 **1**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 16
- 区域与不确定标注：`annotations/case-e1-hole-inside.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-e1-hole-inside.png`（986 B，sha256 `4e79cdbb703fc111…`）、`out/case-e1-hole-inside.svg`
- PNG 朴素计数：4/8 约定 1 块 / 1 孔，8/4 约定 1 块 / 1 孔

### case-e2-hole-tangent — E2 孔与外边界单点相切：有向面积 6528

- 问题：孔洞与边界接触时孔数是多少
- 主张：菱形孔尖点落在矩形右边线上：孔数取决于开/闭约定
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：6528
- 填充面积（折线精确排列）：6528
- 孔面积合计（开集读法）：1152
- 连通块：开 **1** / 闭 **1**；孔：开 **1** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：hole count depends on point/edge contact；1 vertex contact(s) between distinct boundary elements
- 折线误差界 0；最小非接触间距 16
- 区域与不确定标注：`annotations/case-e2-hole-tangent.svg`（2 个填充区域，1 个边界标注）
- 产物：`out/case-e2-hole-tangent.png`（986 B，sha256 `86b2c5944c44b0dd…`）、`out/case-e2-hole-tangent.svg`
- PNG 朴素计数：4/8 约定 1 块 / 1 孔，8/4 约定 1 块 / 1 孔

### case-e3-hole-crossing — E3 孔环穿出外边界：有向面积 6528，孔消失

- 问题：孔洞与边界接触时孔数是多少
- 主张：内环越出外环：外凸部分按 nonzero 被填充，缺口与孔都随约定改变
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：6528
- 填充面积（折线精确排列）：7040
- 孔面积合计（开集读法）：896
- 连通块：开 **2** / 闭 **1**；孔：开 **1** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：component split depends on point/edge contact；hole count depends on point/edge contact
- 折线误差界 0；最小非接触间距 16
- 区域与不确定标注：`annotations/case-e3-hole-crossing.svg`（3 个填充区域，2 个边界标注）
- 产物：`out/case-e3-hole-crossing.png`（1029 B，sha256 `27165bdd07403306…`）、`out/case-e3-hole-crossing.svg`
- PNG 朴素计数：4/8 约定 2 块 / 0 孔，8/4 约定 1 块 / 1 孔（两者不一致，不能作为结论）

### case-e4-apex-apart — E4 两菱形分离：有向面积 6400，两块实心

- 问题：单点接触算不算同一边界
- 主张：两瓣间距 14，开/闭读法都给连通块 2、孔 0
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：6400
- 填充面积（折线精确排列）：6400
- 连通块：开 **2** / 闭 **2**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 14
- 区域与不确定标注：`annotations/case-e4-apex-apart.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-e4-apex-apart.png`（1620 B，sha256 `844a384e844cbfe1…`）、`out/case-e4-apex-apart.svg`
- PNG 朴素计数：4/8 约定 2 块 / 0 孔，8/4 约定 2 块 / 0 孔

### case-e5-apex-contact — E5 两菱形单点相接：有向面积 6400，两块还是一块

- 问题：单点接触算不算同一边界
- 主张：两瓣只在一点接触；开集两块，闭包一块
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：6400
- 填充面积（折线精确排列）：6400
- 连通块：开 **2** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：component split depends on point/edge contact；1 vertex contact(s) between distinct boundary elements
- 折线误差界 0；最小非接触间距 56.5685
- 区域与不确定标注：`annotations/case-e5-apex-contact.svg`（2 个填充区域，1 个边界标注）
- 产物：`out/case-e5-apex-contact.png`（1585 B，sha256 `2eb8cc3008fca28c…`）、`out/case-e5-apex-contact.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-e6-apex-overlap — E6 两菱形重叠：有向面积 6400，一块实心

- 问题：单点接触算不算同一边界
- 主张：重叠 16：并集一块，填充面积小于有向面积
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：6400
- 填充面积（折线精确排列）：6272
- 连通块：开 **1** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 11.3137
- 区域与不确定标注：`annotations/case-e6-apex-overlap.svg`（1 个填充区域，2 个边界标注）
- 产物：`out/case-e6-apex-overlap.png`（1495 B，sha256 `8485bd8807b1f86a…`）、`out/case-e6-apex-overlap.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-e7-edge-gap — E7 两矩形留缝：有向面积 11520，两块实心

- 问题：共边重叠算不算连通
- 主张：两矩形间距 4，连通块 2、孔 0
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：11520
- 填充面积（折线精确排列）：11520
- 连通块：开 **2** / 闭 **2**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 64
- 区域与不确定标注：`annotations/case-e7-edge-gap.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-e7-edge-gap.png`（643 B，sha256 `1268f6a8d0d9c5cd…`）、`out/case-e7-edge-gap.svg`
- PNG 朴素计数：4/8 约定 2 块 / 0 孔，8/4 约定 2 块 / 0 孔

### case-e8-edge-shared — E8 两矩形共边：有向面积 11520，两块还是一块

- 问题：共边重叠算不算连通
- 主张：共享一段边；开集两块，闭包一块
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：11520
- 填充面积（折线精确排列）：11520
- 连通块：开 **2** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：component split depends on point/edge contact；2 vertex contact(s) between distinct boundary elements；1 coincident edge overlap(s) between distinct shapes
- 折线误差界 0；最小非接触间距 16
- 区域与不确定标注：`annotations/case-e8-edge-shared.svg`（2 个填充区域，3 个边界标注）
- 产物：`out/case-e8-edge-shared.png`（649 B，sha256 `5eb6ca56a51b9f08…`）、`out/case-e8-edge-shared.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-e9-edge-overlap — E9 两矩形重叠：有向面积 11520，一块实心

- 问题：共边重叠算不算连通
- 主张：重叠 8：并集一块，填充 11136
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：11520
- 填充面积（折线精确排列）：11136
- 连通块：开 **1** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 折线误差界 0；最小非接触间距 16
- 区域与不确定标注：`annotations/case-e9-edge-overlap.svg`（1 个填充区域，2 个边界标注）
- 产物：`out/case-e9-edge-overlap.png`（655 B，sha256 `c00809cb71bd078b…`）、`out/case-e9-edge-overlap.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-e10-curve-gap — E10 两椭圆分离：有向面积 7800，两块实心

- 问题：曲线相切能否由折线判定
- 主张：两椭圆间隙 6，远大于折线误差界；连通块 2、孔 0
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：7800
- 有向面积（16/24 折线）：7794.583333（差 +5.416667）
- 填充面积（折线精确排列）：`93535/12` = 7794.583333
- 连通块：开 **2** / 闭 **2**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 曲线证书：shape-tangent-ovals vs shape-tangent-ovals → 真实曲线 `separated`，折线传递 `faithful`
- 折线误差界 0.0213558；最小非接触间距 2.44885
- 区域与不确定标注：`annotations/case-e10-curve-gap.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-e10-curve-gap.png`（1529 B，sha256 `f9c2b724448d1fbc…`）、`out/case-e10-curve-gap.svg`
- PNG 朴素计数：4/8 约定 2 块 / 0 孔，8/4 约定 2 块 / 0 孔

### case-e11-curve-tangent — E11 两椭圆外切：有向面积 7800，两块还是一块

- 问题：曲线相切能否由折线判定
- 主张：两椭圆在 (127,96) 有公切线；折线在切点相接，计数依赖约定
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：7800
- 有向面积（16/24 折线）：7794.583333（差 +5.416667）
- 填充面积（折线精确排列）：`93535/12` = 7794.583333
- 连通块：开 **2** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：component split depends on point/edge contact；1 vertex contact(s) between distinct boundary elements
- 曲线证书：shape-tangent-ovals vs shape-tangent-ovals → 真实曲线 `touch`，折线传递 `faithful`，共享点 [['127', '96']]
- 折线误差界 0.0213558；最小非接触间距 2.44885
- 区域与不确定标注：`annotations/case-e11-curve-tangent.svg`（2 个填充区域，1 个边界标注）
- 产物：`out/case-e11-curve-tangent.png`（1544 B，sha256 `81ae3d42f1f7742c…`）、`out/case-e11-curve-tangent.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-e12-curve-overlap — E12 两椭圆重叠：有向面积 7800，一块实心

- 问题：曲线相切能否由折线判定
- 主张：重叠 8：并集一块，填充面积小于有向面积
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：7800
- 有向面积（16/24 折线）：7794.583333（差 +5.416667）
- 填充面积（折线精确排列）：`7127743/936` = 7615.110043
- 连通块：开 **1** / 闭 **1**；孔：开 **0** / 闭 **0**
- 判定：**determined**；可见性：visible
- 曲线证书：shape-tangent-ovals vs shape-tangent-ovals → 真实曲线 `crossing`，折线传递 `faithful`
- 折线误差界 0.0213558；最小非接触间距 0.573951
- 区域与不确定标注：`annotations/case-e12-curve-overlap.svg`（1 个填充区域，2 个边界标注）
- 产物：`out/case-e12-curve-overlap.png`（1488 B，sha256 `78df11fda446e983…`）、`out/case-e12-curve-overlap.svg`
- PNG 朴素计数：4/8 约定 1 块 / 0 孔，8/4 约定 1 块 / 0 孔

### case-e13-cancelling-rings — E13 反向完全重合双环：有向面积 0，填充为空

- 问题：填充为空是否等于没有退化
- 主张：处处绕数 0，填充集为空；但两条边界完全重合，任何区域计数都建立在重合约定上
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：0
- 填充面积（折线精确排列）：0
- 连通块：开 **0** / 闭 **0**；孔：开 **0** / 闭 **0**
- 判定：**determined-with-contact**；可见性：visible
- 原因：4 vertex contact(s) between distinct boundary elements；4 coincident edge overlap(s) between distinct shapes
- 折线误差界 0；最小非接触间距 80
- 区域与不确定标注：`annotations/case-e13-cancelling-rings.svg`（0 个填充区域，8 个边界标注）
- 产物：`out/case-e13-cancelling-rings.png`（599 B，sha256 `6f225cdb0ddefdd6…`）、`out/case-e13-cancelling-rings.svg`
- PNG 朴素计数：4/8 约定 0 块 / 0 孔，8/4 约定 0 块 / 0 孔

### case-e14-subpixel-ring — E14 亚像素边框：线宽 0.02，有向面积 7.0384

- 问题：几何上存在的孔，像素校样看不见
- 主张：外环 7680、内环 -7672.9616，几何填充是宽 0.02 的边框，面积 7.0384；512 像素覆盖下边框必然消失，孔 1 个但肉眼不可见
- 填充规则：`nonzero`；形体数 1；镜像：否
- 有向面积（曲线精确，画布）：`4399/625` = 7.038400
- 填充面积（折线精确排列）：`4399/625` = 7.038400
- 孔面积合计（开集读法）：`4795601/625` = 7672.961600
- 连通块：开 **1** / 闭 **1**；孔：开 **1** / 闭 **1**
- 判定：**determined**；可见性：below-pixel
- 原因：minimum non-contact clearance 0.02 canvas units is under 1 px, so adjacent regions merge in raster
- 折线误差界 0；最小非接触间距 0.02
- 区域与不确定标注：`annotations/case-e14-subpixel-ring.svg`（2 个填充区域，0 个边界标注）
- 产物：`out/case-e14-subpixel-ring.png`（599 B，sha256 `6f225cdb0ddefdd6…`）、`out/case-e14-subpixel-ring.svg`
- PNG 朴素计数：4/8 约定 0 块 / 0 孔，8/4 约定 0 块 / 0 孔

## 手工核对样例

下面三例的数值可以直接用纸笔复核，用来检验工具链没有系统性偏差。

**A1 领结**：顶点 `(80,80) (80,160) (224,16) (224,176)`，鞋带公式 `½Σ(x_i y_{i+1} − y_i x_{i+1})` 得
`½[(80·160−80·80) + (80·16−160·224) + (224·176−16·224) + (224·80−176·80)] = ½[6400 − 34560 + 35840 + 3840] = 5760`。
两瓣面积分别为 `½|…| = 7680` 与 `1920`。

**C1 椭圆**：控制点系数 `k = 2/3` 的三次椭圆每段精确面积是 `5/6·a·b`，四段共 `10/3·a·b`。外椭圆 `a=96, b=63` → `20160`；内椭圆反向 `a=33, b=24` → `−2640`；合计 `17520`。

**E14 亚像素边框**：外环 `96×80 = 7680`；内环 `95.96×79.96 = 7672.9616`；差 `7.0384 = 4399/625`，与工具输出一致。

## 产物对照：SVG 与 PNG

`out/*.svg` 与 `out/*.png` 都来自同一份展开几何。SVG 是矢量，因此 E14 的 0.02 单位边框在 SVG 中仍然可见，在 512 像素 PNG 中必然消失；C3/C6 的亚像素间隙同理。这说明“PNG 看起来怎样”与“几何拓扑是什么”是两个不同的问题。

## 对照对

`contrast` = 有向面积相同、填充拓扑不同（本册要说明的现象）；`control` = 有向面积与拓扑都相同（接触前后的拓扑不变，用来固定其它变量）；`different-area` = 有向面积也不同（不属于对照，只作上下文）。

| A | B | 关系 | 有向面积 | A 块/孔 | B 块/孔 |
|---|---|---|---|---|---|
| case-a1-bowtie | case-a2-nested-opposite | contrast | 5760 | 2/0 | 1/1 |
| case-a1-bowtie | case-a4-zero-bowtie | different-area | 5760 | 2/0 | 2/0 |
| case-a1-bowtie | case-b1-same-orientation | contrast | 5760 | 2/0 | 1/0 |
| case-a2-nested-opposite | case-b1-same-orientation | contrast | 5760 | 1/1 | 1/0 |
| case-a3-crossing-opposite | case-a2-nested-opposite | contrast | 5760 | 2/1 | 1/1 |
| case-a4-zero-bowtie | case-a5-zero-disjoint | control | 0 | 2/0 | 2/0 |
| case-a4-zero-bowtie | case-e13-cancelling-rings | contrast | 0 | 2/0 | 0/0 |
| case-a5-zero-disjoint | case-e13-cancelling-rings | contrast | 0 | 2/0 | 0/0 |
| case-b2-mirror-disjoint | case-b3-mirror-nested | contrast | -3840 | 2/0 | 1/1 |
| case-b2-mirror-disjoint | case-b4-direct-nested | different-area | -3840 | 2/0 | 1/1 |
| case-b3-mirror-nested | case-b4-direct-nested | different-area | -3840 | 1/1 | 1/1 |
| case-b5-two-shapes | case-b6-one-shape | contrast | 4800 | 2/0 | 1/0 |
| case-c1-oval-nested | case-c2-oval-crossing | contrast | 17520 | 1/1 | 2/1 |
| case-c1-oval-nested | case-c4-oval-pair | contrast | 17520 | 1/1 | 2/0 |
| case-c1-oval-nested | case-c5-oval-mirror | different-area | 17520 | 1/1 | 1/1 |
| case-c2-oval-crossing | case-c3-oval-near-tangent | contrast | 17520 | 2/1 | 4/1 |
| case-c6-oval-shallow-cross | case-c2-oval-crossing | control | 17520 | 2/1 | 2/1 |
| case-c6-oval-shallow-cross | case-c3-oval-near-tangent | contrast | 17520 | 2/1 | 4/1 |
| case-c3-oval-near-tangent | case-c1-oval-nested | contrast | 17520 | 4/1 | 1/1 |
| case-c4-oval-pair | case-c5-oval-mirror | different-area | 17520 | 2/0 | 1/1 |
| case-d1-star-nonzero | case-d2-star-evenodd | contrast | 13697 | 1/0 | 5/1 |
| case-d3-duplicate-nonzero | case-d4-duplicate-evenodd | contrast | 15360 | 1/0 | 0/0 |
| case-e1-hole-inside | case-e3-hole-crossing | contrast | 6528 | 1/1 | 2/1 |
| case-e2-hole-tangent | case-e1-hole-inside | control | 6528 | 1/1 | 1/1 |
| case-e2-hole-tangent | case-e3-hole-crossing | contrast | 6528 | 1/1 | 2/1 |
| case-e4-apex-apart | case-e6-apex-overlap | contrast | 6400 | 2/0 | 1/0 |
| case-e5-apex-contact | case-e4-apex-apart | control | 6400 | 2/0 | 2/0 |
| case-e5-apex-contact | case-e6-apex-overlap | contrast | 6400 | 2/0 | 1/0 |
| case-e7-edge-gap | case-e9-edge-overlap | contrast | 11520 | 2/0 | 1/0 |
| case-e8-edge-shared | case-e7-edge-gap | control | 11520 | 2/0 | 2/0 |
| case-e8-edge-shared | case-e9-edge-overlap | contrast | 11520 | 2/0 | 1/0 |
| case-e10-curve-gap | case-e12-curve-overlap | contrast | 7800 | 2/0 | 1/0 |
| case-e11-curve-tangent | case-e10-curve-gap | control | 7800 | 2/0 | 2/0 |
| case-e11-curve-tangent | case-e12-curve-overlap | contrast | 7800 | 2/0 | 1/0 |
| case-e14-subpixel-ring | case-e1-hole-inside | different-area | 4399/625 | 1/1 | 1/1 |

