# HTTP契约

所有写入场景的JSON入口拒绝未知字段和尾随内容，最大8MiB。服务返回JSON错误；404为对象不存在，409为版本或生命周期冲突，429为成像容量不足，400为不符合输入契约，504为超时。未知路由由HTTP框架返回404。

| 方法与路径 | 输入及结果 |
|---|---|
| GET /healthz | ready |
| POST /v1/plates | id,title,document →201版面 |
| GET /v1/plates/{id} | 完整稿本历史 |
| PUT /v1/plates/{id} | expected_revision,document →追加稿本 |
| POST /v1/plates/{id}/branch | revision,id,title →201独立版面 |
| POST /v1/plates/{id}/archive | 定稿选择 →200定稿移交清单；归档后禁止修订，历史仍可读取 |
| GET /v1/plates/{id}/handover | 读取已定稿版面的移交清单；未定稿404 |
| GET /v1/plates/{id}/handover/verify | 独立重算清单摘要与全部谱系，返回逐项核对结论 |
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

成品响应中的data为null，避免重复传输二进制；GET成品获得实际内容。If-None-Match精确匹配强ETag时返回304。成功校样可从持久化快照确认，当前版本没有校样列表/详情接口。GET版面返回完整历史，服务没有全局列表接口。

## 定稿归档与移交清单

归档不再是无参数的状态翻转。`POST /v1/plates/{id}/archive` 必须提交明确的定稿选择，服务在同一个原子快照内完成校验、归档和清单生成：

```json
{
  "revision": 1,
  "artifacts": ["<png-artifact-id>", "<svg-artifact-id>"],
  "proofs": ["<accepted-proof-id>"],
  "on_missing": "reject"
}
```

- `revision` 为必填，服务不默认取最大稿号。最新稿本若未通过审阅，可以且应当选择更早的稿本。
- `artifacts` 中每个成品必须属于该版面、该稿本且仍为available；格式按png/svg分别落位，SVG无法冒充像素承诺对应的PNG。属于其他稿本或已撤销的成品返回409，未知id返回404，重复选择或同一格式选多份返回400。
- `proofs` 只接受accepted终局校样；open或rejected校样返回409。清单记录校样到成品再到源稿摘要的完整链条。
- `on_missing` 选择缺少成品时的处理方式（见下），默认`reject`，只接受这两个值，其他值返回400。
- 重复归档或对已归档版面修订返回409；归档后历史稿本仍可渲染、仍可从任意历史稿本分支。

### 缺少成品的两种处理方式

| on_missing | 行为 | complete |
|---|---|---|
| `reject`（默认） | PNG、SVG、至少一条accepted校样任一缺失即拒绝（409），错误文本列出缺口，版面保持active，不产生清单 | 成功时恒为true |
| `exclude` | 允许归档；缺失行以显式`null`（PNG/SVG）或空数组（proofs）保留在清单中，缺口对接收方可见 | 按实际存在情况计算，可为false |

清单含自校摘要`digest`（对除digest外全部字段的规范JSON取SHA-256）。`GET .../handover/verify` 不信任清单自报内容，而是从当前版面、成品、校样事实独立重算：摘要是否有效、PNG/SVG字节与源稿摘要是否仍然吻合、accepted校样链条是否成立，逐项返回`digest_valid`、`lineage_valid`、`png_verified`、`svg_verified`、`proofs_verified`和`complete`。清单随状态快照持久化，重启恢复后核对结论保持不变；恢复时还会重跑全部谱系校验，篡改过的快照无法启动。

清单是定稿时刻的不可变记录：已列入的PNG/SVG若在归档后被撤销（accepted校样引用的成品本来就禁止撤销），清单行仍保留定稿时的身份与摘要，快照谱系继续成立、自校摘要不变，但verify会把对应`*_verified`置为false并令`lineage_valid`为false，使"成品后来失效"这一事实对接收方可见，而不是悄悄展示一份看似完好的清单。exclude清单中的显式null缺口不算失效行，不影响已存在行的`lineage_valid`。

封包（bundle）仍只含版面稿本历史，不含成品、校样和移交清单；清单引用的成品身份在原服务内通过verify核对，离线随包移交属于独立扩展。
