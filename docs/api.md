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
| POST /v1/plates/{id}/render?revision=1&format=png&x=20&y=20&width=64&height=64 | 201局部PNG；区域为画布像素坐标 |
| POST /v1/plates/{id}/exposures?revision=1&format=png | 202瞬态任务 |
| GET /v1/exposures/{id} | queued/running/succeeded/failed/cancelled |
| DELETE /v1/exposures/{id} | 202取消请求 |
| GET /v1/artifacts/{id} | 图片；ETag为成品字节摘要；X-Scene-Digest为稿本摘要 |
| DELETE /v1/artifacts/{id} | 撤销成品 |
| POST /v1/proofs | id,artifact →201 open校样 |
| PUT /v1/proofs/{id} | state为accepted或rejected，note为说明 |
| GET /v1/plates/{id}/bundle | 含完整稿本历史的封包JSON |
| POST /v1/bundles | 原样封包；201导入，重名409 |

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 局部PNG区域

render和exposures都接受可选的`x`、`y`、`width`、`height`查询参数，四者必须同时出现且各自最多出现一次，缺省全部时渲染整幅画布。区域是原画布像素坐标的矩形：`x,y`是画布中成为输出首像素的整数像素，`width,height`是输出像素数；输出PNG尺寸即区域尺寸，pixel(x,y)与整幅PNG同位置像素逐字节一致。

- 原点只改变输出像素的落位，不改变采样。四个子像素采样位置、渐变轴与停止点、裁域/蒙版包含判定、四点覆盖仍按画布坐标求值，因此局部图不会因区域原点变化而平移渐变、镂空或覆盖。
- 区域必须完整落在画布内（`0≤x`、`0≤y`、`width≥1`、`height≥1`、`x+width≤画布宽`、`y+height≤画布高`）。不做静默裁切：越界、零或负尺寸、只给部分参数、非整数、参数重复均返回400。画布外内容不进入输出，因而没有“画布外透明边”语义——请求本身被拒绝，调用方需把窗口收进画布。
- `x=0&y=0&width=画布宽&height=画布高`等价于整幅，返回与无参数请求相同的成品标识，且元数据不带region。整幅请求的响应、像素、摘要与标识均保持原有兼容。
- 只有png支持区域；svg与区域同时出现返回400。同步render与异步exposures共用同一解析、同一区域归一化与同一成像路径，同一区域得到相同成品标识。
- 透明背景按场景`background.a`照常输出；区域只是窗口，不改变合成。局部图不是整幅图的裁剪副本，而是同一场景在同一画布坐标上的同点采样，因此只有与整幅对应像素比较才应相等。
- 成品元数据新增`region`对象（局部时出现，含x/y/width/height）；整体请求省略该字段，旧快照与旧客户端不受影响。局部成品同样进入谱系、校样、撤销与恢复校验。
