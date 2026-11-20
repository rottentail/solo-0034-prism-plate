# HTTP契约

所有写入场景的JSON入口拒绝未知字段和尾随内容，最大8MiB。服务返回JSON错误；404为对象不存在，409为版本或生命周期冲突，429为成像容量不足，400为不符合输入契约，504为超时。未知路由由HTTP框架返回404。

| 方法与路径 | 输入及结果 |
|---|---|
| GET /healthz | ready |
| POST /v1/plates | id,title,document →201版面 |
| GET /v1/plates/{id} | 完整稿本历史 |
| PUT /v1/plates/{id} | expected_revision,document →追加稿本 |
| POST /v1/plates/{id}/branch | revision,id,title →201独立版面 |
| POST /v1/plates/{id}/archive | 禁止修订；历史仍可读取 |
| POST /v1/plates/{id}/render?revision=1&format=png | 201成品元数据；format也可为svg |
| POST /v1/plates/{id}/exposures?revision=1&format=png | 202瞬态任务 |
| GET /v1/exposures/{id} | queued/running/succeeded/failed/cancelled |
| DELETE /v1/exposures/{id} | 202取消请求 |
| GET /v1/artifacts/{id} | 图片；ETag为成品字节摘要；X-Scene-Digest为稿本摘要 |
| DELETE /v1/artifacts/{id} | 撤销成品 |
| POST /v1/proofs | id,artifact →201 open校样 |
| PUT /v1/proofs/{id} | state为accepted或rejected，note为说明 |
| GET /v1/plates/{id}/bundle | 含完整稿本历史的封包JSON |
| POST /v1/bundles | 原样封包；201导入，重名409 |
| POST /v1/plates/{id}/registration/preview | bundle,source_revision,allow_reflection,pairs →200套准预览 |
| POST /v1/plates/{id}/registration/commit | 同上加expected_revision →201追加合并稿本 |

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 套准导入

外来稿本以封包随请求提交，只在内存校验，不落地为版面；本地版面保持只追加。流程分两阶段：preview返回拟合结果与合并后的完整稿本，commit以expected_revision把同一稿本追加为新稿本。同一输入由同一代码路径产生，两侧document_digest必然一致；调用方先查看对齐偏差再决定是否提交。基线稿本在提交期间被他人推进时得到409，需重新预览。

标记对至少3对、至多64对；source为外来画布坐标，target为本地画布坐标。拟合模型为最小二乘相似变换（平移+旋转+统一缩放），测量误差由最小二乘吸收并逐对给出残差。allow_reflection为真时同时拟合镜像解并取残差更小者，响应reflected标识是否入选；镜像可能是有意的，服务不替调用方裁决。source_revision缺省取封包最新稿本。

可信度按RMS残差占本地画布对角线比例分档：≤0.2%为high，≤1%为medium，其余为low；标记几乎共线（散布矩阵特征值比<1e-4）时降一档并在diagnostics说明——此时垂线方向的落位几乎未被观测。恰好三对、镜像入选、仿射复核不可用等也会写入diagnostics。

一次平面变换不能消除的偏差：非统一缩放与剪切需要完整仿射（预览以仿射复核对比报告）；透视（梯形）与局部/非线性畸变超出任何单一平面变换的能力（仿射复核仍有显著残差时报告）。这两类只能退回外方重新出稿或接受残差，服务不会静默修正。

空间约定：形状轮廓与渐变轴停留在形状局部坐标，拟合矩阵复合进形状transform；裁域与蒙版处于画布坐标，直接以拟合矩阵重写控制点（仿射下贝塞尔控制点变换与曲线变换等价；镜像翻转绕向，nonzero与evenodd判定均不受影响）。合并稿保留本地画布尺寸与背景，外来背景明确丢弃并在foreign.background回报原值；外来图层置于本地图层之后，节点标识加外来版面前缀并确定性去重。双方原稿均不变：封包不导入不修改，本地旧稿本不可改写。
