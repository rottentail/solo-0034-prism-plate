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
| POST /v1/plates/{id}/restore/preview | base_revision,source_revision,context,items →200可审阅范围 |
| POST /v1/plates/{id}/restore | expected_revision,plan_digest,request →201新稿及同一范围 |

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 选对象恢复

改稿后可以从本版面任一旧稿取回若干具名形状（当前版本只支持同一个版面内的历史），放进当前稿并追加一个正常新稿；当前稿的其余图层、形状与背景不变。流程分两步，目的是让调用方先审阅再提交。

```json
{"base_revision":3,"source_revision":1,"context":"destination",
 "items":[{"shape":"cyan-window","layer":"cyan-glaze","index":0}]}
```

- base_revision 必须是当前最新稿，source_revision 可指向任一旧稿；预览时 base 不是最新返回409，旧稿不存在返回404。
- items 选择 1..64 个形状，只接受源稿中存在的id；同一形状不能重复选择。
- context=destination：恢复到当前稿的指定图层。id 已存在时原位替换该形状（此时不接受 layer/index）；不存在时插入到 layer 的 index 处（从0起）或末尾。保留的是几何：path 全部轮廓及顺序、fill_rule、transform、brush（含局部坐标的渐变轴与色标）。
- context=source：按源图层重建一个顶层图层，复制该层的 opacity、blend、clip、mask 与所选的形状，图层id为源id加 `-restored` 后缀；未选中的源图层不复制。
- 两种方式都不复制：文档背景、画布尺寸、源图层在堆叠中的相对位置、未选中的源形状。destination 方式还使用当前目标图层的透明度、混合方式、裁域与蒙版；这些属性不会被源稿覆盖，因此与源稿不同时外观不会还原。
- 预览返回 items[].geometry（轮廓数、顶点数、绕向、是否有真正嵌套的镂空、变换、笔刷）、copied/adopted、not_preserved、context_differences（逐项 source/current/preserved）、cannot_preserve、warnings、scope（恢复了多少、未动多少、新增/替换了什么）与 plan_digest。
- 提交时传同一 request 与 plan_digest；重新计算的计划摘要不同（选择已变或基础稿已变）返回409，不会提交未经审阅的范围。提交产生的稿本记录 restored 来源（源稿号、源摘要、方式、形状对应）。
- 重叠与镂空只作提示，不是像素级等价承诺：重叠按目标图层顺序与新组的整体透明度合成；镂空内部透出当前目标合成而不是旧稿背景。bounds 相交只是审查提示，不声称实际轮廓相交。

PNG是外观事实：恢复后重新渲染得到的是当前稿整幅结果，不是旧成品的复制。SVG同样从新稿几何导出。
