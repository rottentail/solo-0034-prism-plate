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
| POST /v1/reviews | id,artifacts为同一稿的png与svg成品id →201 open审阅 |
| GET /v1/reviews/{id} | 审阅会话与两侧结论 |
| PUT /v1/reviews/{id} | artifact,state为accepted或returned,note →裁决一侧 |
| GET /v1/plates/{id}/bundle | 含完整稿本历史的封包JSON |
| POST /v1/bundles | 原样封包；201导入，重名409 |

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 双成品审阅

一次审阅把同一稿（同版面同稿号）的两个成品放入一个会话：png侧角色为pixel（权威像素结果），svg侧角色为geometry（几何可编辑性）。两侧各自从pending走向accepted或returned并附note；会话结论由两侧推导：两侧通过为accepted，两侧退回为returned，一通过一退回为partial，尚有pending侧为open。发现透明渐变或组混合差异时，可保留一方结论而退回另一方。不同稿号或不同版面的成品不能凑成一次审阅（409），同一成品不能占两侧（400），已裁决侧不可改判，会话关闭后不可再裁决。被accepted侧引用的成品不可撤销；returned侧不保护其成品。旧单成品校样流程不受影响，两种撤销保护并存。

真实示例（amber-plate第1稿，成品id由内容摘要确定，可复现）：

```sh
curl -X POST 'http://127.0.0.1:7846/v1/plates/amber-plate/render?revision=1&format=png'
# → 201 {"id":"b50933311873d5449c625c40713d46c02c5206dcfb1d376c012d37956a1c4c6a",...}
curl -X POST 'http://127.0.0.1:7846/v1/plates/amber-plate/render?revision=1&format=svg'
# → 201 {"id":"f020c0d9275c15a013537f6afca832631dab910f00d5d453d31cd3b0e01d482e",...}
curl -H 'Content-Type: application/json' -d '{"id":"review-dual","artifacts":["b50933311873d5449c625c40713d46c02c5206dcfb1d376c012d37956a1c4c6a","f020c0d9275c15a013537f6afca832631dab910f00d5d453d31cd3b0e01d482e"]}' http://127.0.0.1:7846/v1/reviews
# → 201 {"id":"review-dual","plate":"amber-plate","revision":1,"state":"open","sides":[
#      {"artifact":"b509…6c6a","role":"pixel","state":"pending","note":""},
#      {"artifact":"f020…482e","role":"geometry","state":"pending","note":""}]}
curl -X PUT -H 'Content-Type: application/json' -d '{"artifact":"f020c0d9275c15a013537f6afca832631dab910f00d5d453d31cd3b0e01d482e","state":"returned","note":"透明渐变与组混合在矢量侧与权威像素不一致，退回几何成品"}' http://127.0.0.1:7846/v1/reviews/review-dual
# → 200 会话仍为open
curl -X PUT -H 'Content-Type: application/json' -d '{"artifact":"b50933311873d5449c625c40713d46c02c5206dcfb1d376c012d37956a1c4c6a","state":"accepted","note":"像素结果权威，客户已确认PNG"}' http://127.0.0.1:7846/v1/reviews/review-dual
# → 200 {"id":"review-dual","state":"partial","sides":[
#      {"artifact":"b509…6c6a","role":"pixel","state":"accepted","note":"像素结果权威，客户已确认PNG"},
#      {"artifact":"f020…482e","role":"geometry","state":"returned","note":"透明渐变与组混合在矢量侧与权威像素不一致，退回几何成品"}]}
```

此后pixel侧成品撤销返回409，geometry侧成品可撤销；scripts/walkthrough.py的review流程覆盖open/partial/accepted/returned全部会话状态与重启后的保持。
