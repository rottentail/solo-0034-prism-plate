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
| POST /v1/plates/{id}/render?revision=1&format=png | 201成品元数据；format也可为svg、spotsep |
| POST /v1/plates/{id}/exposures?revision=1&format=png | 202瞬态任务；format同样接受svg与spotsep |
| GET /v1/exposures/{id} | queued/running/succeeded/failed/cancelled |
| DELETE /v1/exposures/{id} | 202取消请求 |
| GET /v1/artifacts/{id} | 图片；ETag为成品字节摘要；X-Scene-Digest为稿本摘要 |
| DELETE /v1/artifacts/{id} | 撤销成品 |
| POST /v1/proofs | id,artifact →201 open校样 |
| PUT /v1/proofs/{id} | state为accepted或rejected，note为说明 |
| GET /v1/plates/{id}/bundle | 含完整稿本历史的封包JSON |
| POST /v1/bundles | 原样封包；201导入，重名409 |

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 专色分版（format=spotsep）

`document.spots` 声明0..8个命名专色：每个含`id`（与节点标识同一语法）、`name`（1..80字符）与`proof_color`（0..1 sRGB打样近似色，仅用于屏幕叠合，不进入覆盖版）。brush通过`spot`字段按id引用专色，颜色定义全局共享；同一专色在任何形状中都解析为同一打样色。专色brush的`color.a`（实色）或渐变stop的alpha承载墨量，专色渐变的色相不随stop变化，仅alpha变化。含专色引用的图层只允许`normal`混合；引用未声明专色、重复专色id、非normal专色图层均在准入阶段400拒绝。没有声明专色的稿本请求spotsep返回400。

叠印语义由`overprint`布尔值界定（图层级默认，形状级可覆盖）：

- 镂空 knockout（默认false）：后印专色落墨处按几何覆盖把先印专色的覆盖按`1-alpha`比例挖除，各版在镂空边界互斥；这是逐采样点几何求值的结果，不是对合成RGB做通道拆分。
- 套印 overprint（true）：保留先印专色的覆盖，两膜叠印；重新叠合时叠印区以线性光预乘multiply模拟透明油墨叠加，区别于参考PNG的source-over。

一次spotsep在同一原子快照提交点发布一组成品，任一计算失败不产生半成品：参考合成`png`、每个专色一张8-bit灰度`spotplate` PNG（像素值`round(coverage*255)`，0为无墨，含`X-Spot-Ink`头）、按印刷顺序重新叠合的`spotproof` PNG，以及`spotsep` JSON移交清单。清单`artifacts`给出每个成品的确定性id与摘要，`plates`给出每版覆盖统计，`overlaps`列出成对油墨的叠印样本数与是否套印，`recomposite`给出参考与重新叠合两张PNG的摘要、逐像素差异数、叠印像素数和差异解释。无套印时所有墨膜仍按source-over叠加，重新叠合与参考PNG字节一致（`byte_identical_without_overprint=true`、`different_pixels=0`）；存在套印时差异被限定在叠印像素内。重复提交返回同一组成品id。流程RGB形状不生成专色版，在清单`process_shapes`中计数。

一次分版的参考图、全部覆盖版、重新叠合图与清单构成不可分割的内容定址组：对组内任一成品建立accepted校样后，整组成品都不可撤销（DELETE返回409）。组内任一成品被撤销后，同稿本同内容的分版重跑会因不得重新激活被撤销成品而返回409；需要重新出图时追加稿本（内容摘要改变，产生全新成品id）。`spotplate`成品携带`ink`字段标识专色，谱系校验要求该字段只出现在覆盖版上且非空。
