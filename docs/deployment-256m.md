# 256 MiB 容器部署与班组收单方案

现场打样机只给棱版容器 256 MiB 内存。本方案给出：容器怎么配、班次历史怎么生成与重放、交接班按什么证据收单、两名操作员同时提交重稿时系统怎么排队或拒绝、以及实测容量矩阵。所有结论均由 `scripts/shift_history.py` 在真实 HTTP 流程上跑出，证据文件随快照一起留存。

## 1. 结论摘要

- 服务在 256 MiB 内存预算内可以承接：**约 32 MiB 的班次快照**（58 版面 / 116 稿本 / 232 件 512×512 PNG+SVG 成品 / 59 张签样），重启后继续接单，两名操作员同时提交细密镂空或多层透明稿。
- 必须设置 `GOMEMLIMIT=176MiB`（约硬限的 69%）。Go 运行时不读取容器内存限制；不设软限时同一工作负载峰值 RSS 实测 364–436 MiB（4 路并发探针 770 MiB），会被 256 MiB 硬限直接 OOM 杀掉。
- 快照 32 MiB 是该内存预算下的实际上限；README 所列 128 MiB 快照容量在 256 MiB 内存下不可达（读路径与提交路径都会整体复制快照）。需要更大历史时必须提高容器内存，而不是只靠调参。
- 接受的稿件与不限内存时出图**逐字节一致**（成品摘要逐项相等，见 §4 末的一致性证据）。

## 2. 部署

### 2.1 构建

```sh
go build -o bin/prismplate ./cmd/prismplate   # Go ≥ 1.24，仅标准库
```

### 2.2 容器运行（推荐）

```sh
docker build -t prismplate .
docker run --rm --memory=256m --memory-swap=256m \
  -e GOMEMLIMIT=176MiB \
  -v /var/lib/prism-plate:/data:rw \
  -p 127.0.0.1:7846:7846 prismplate
```

- `--memory=256m --memory-swap=256m`：内核硬限，超出即 OOM；禁止 swap 扩展掩盖真实余量。
- `GOMEMLIMIT=176MiB`：Go 软限，让 GC 在内核动手之前收紧堆。176 MiB 是实测值（§5），不要照抄常见的 90% 经验值——本服务读路径会整体复制快照，瞬时占用高。
- 数据目录挂载到 `/data`，属主给 UID 65532；单副本单写，同目录不能起第二台（`writer.lock` 会拒绝）。
- 裸机或 compose 部署时同样设置环境变量 `GOMEMLIMIT=176MiB`，并用 cgroup v2 `memory.max=256MiB` 施加硬限。

### 2.3 启动后自检

```sh
curl -s http://127.0.0.1:7846/healthz    # {"service":"prism-plate","state":"ready"}
```

## 3. 班次历史：生成、重放与交接材料

### 3.1 生成（正常制稿、出图、签样流程）

```sh
python3 scripts/shift_history.py generate \
  --home var/acceptance-256m --target-mib 31 --gomemlimit 176 --force
```

脚本以真实 HTTP 流程回放一个班次：3 张验收工单（普通单张 / 细密镂空 / 多层透明，各 2 稿本 × PNG+SVG，含 1 张驳回签样与 2 张超预算稿的 400 拒绝记录），随后按班轮替工单（veil/mosaic/lattice）持续制稿—修订—出图—签样，直到快照达到约 31–32 MiB 或触及对象容量护栏。产物即交接材料：

| 文件 | 内容 |
|---|---|
| `var/acceptance-256m/state/state.json` | 服务快照（约 32 MiB，含全部真实成品字节） |
| `var/acceptance-256m/manifest.json` | 每张工单的稿本摘要、成品 id/SHA-256/字节数、签样状态、快照 SHA-256、各阶段峰值 RSS |
| `var/acceptance-256m/service.log` | 服务日志 |

### 3.2 可重放性

渲染是确定性的：同一稿本必出同一摘要。同 seed 跑两次 `generate` 再 `compare`，即证明班次历史可重放：

```sh
python3 scripts/shift_history.py generate --home /tmp/replay-a --target-mib 31
python3 scripts/shift_history.py generate --home /tmp/replay-b --target-mib 31
python3 scripts/shift_history.py compare --a /tmp/replay-a --b /tmp/replay-b
# {"compare": "identical", "artifacts": 232, "proofs": 59, ...}
```

### 3.3 交接班验收（收单前必须全绿）

接班班组在快照所在机器上执行：

```sh
python3 scripts/shift_history.py verify --home var/acceptance-256m --gomemlimit 176
```

`verify` 在现有快照上重启服务并跑 10 项验收（§4），结果写入 `verify-report.json`。注意：`verify` 会在快照里追加一张"交接班后首单"，属于一次性验收；需要重新验收时用相同 seed 重新 `generate` 再 `verify`（结果确定性一致）。

## 4. 收单证据清单

现场收单以 `verify-report.json` 全绿为准，每项检查对应的证据：

| # | 检查 | 证明 | 证据位置 |
|---|---|---|---|
| E1 | snapshot-untouched | 交接的快照自生成后未被改动 | state.json 的 SHA-256 == manifest.snapshot.sha256 |
| E2 | restart-ready | 重启后服务就绪，全部 116 个稿本重新通过编译准入 | healthz 200 + 启动日志 |
| E3 | old-proofs-downloadable | 31 张已签样成品全部可取回：字节 SHA-256 == 摘要、ETag 一致、If-None-Match 返回 304 | GET /v1/artifacts/{id} 三次握手 |
| E4 | new-order-flow | 重启后能连续处理普通单张校样：新建→修订→PNG+SVG→签样→下载，同一稿本连续 3 次出图摘要一致 | post-restart-order 工单 |
| E5 | deterministic-replay | 已持久化稿本重新出图，成品 id 与摘要逐字节不变（验收工单全量 + 班次工单抽样） | render 响应比对 |
| E6 | two-operator-storm | 两名操作员同时提交细密镂空+多层透明稿：同步出图 201（摘要正确）或 429；12 个异步任务在 2 工作者+8 队列下排队成功或明确拒绝（提交时 429 / 执行时 "exposure capacity reached"），无 5xx、无挂起 | 任务终局 + 响应摘要 |
| E7 | compute-slot-overflow | 4 路并发同步出图压 2 个计算名额：恰好部分 201（摘要一致）、其余 429，无 5xx | 响应状态分布 |
| E8 | no-phantom-artifacts | 快照成品集合 == 清单+新单，不多一件未完成成品；全部成品 available；59 张签样状态原样 | state.json 盘点 |
| E9 | proofs-survive-overflow | 429 风暴之后旧签样仍 200 可取、摘要一致 | 再次 GET 验证 |
| E10 | memory-envelope-256MiB | 服务进程峰值 RSS（VmHWM）全程 < 256 MiB，附余量 | /proc 采样，写入报告 |

跨机/跨限制一致性另有两条独立证据（`compare` 输出）：

- **可重放**：同 seed 两次 `generate`，232 件成品摘要与 59 张签样完全一致。
- **限内存 == 不限内存**：`--gomemlimit 176` 与 `--gomemlimit 0` 各跑一次 `generate`，`compare` 全部一致——接受的稿件出图与不限内存时逐字节相同。

## 5. 实测容量矩阵（历史 × 稿件 × 并发）

测试环境：Linux aarch64，Go 1.24.9，10 核。内存硬限无法在本验证环境用 cgroup 施加（只读 /sys/fs/cgroup），改用 `GOMEMLIMIT` 软限 + 实测进程峰值 RSS（VmHWM）作为硬限适配证据；现场用 §2.2 的容器硬限执行。稿件均为 512×512：普通单张 mosaic（148 段）、细密镂空 lattice（152 段，512² 下准入上限）、多层透明 veil（16 层 152 段）。

历史构建（空 → 31.5 MiB 快照：58 版面 / 116 稿本 / 232 成品 / 59 签样，55 班轮替工单，2 路出图并发）：

| GOMEMLIMIT | 结果 | 峰值 RSS | 判定 |
|---|---|---|---|
| 不限 | 220 秒完成，全部 201 | 425–436 MiB | 超 256 硬限，会 OOM |
| 192 MiB | 218 秒完成，全部 201 | 241 MiB | 余量仅 15 MiB，偏险 |
| **176 MiB** | **218 秒完成，全部 201** | **217–228 MiB** | **采用，余量 ≥ 28 MiB** |
| 192 MiB（出图并发 1） | 354 秒完成，全部 201 | 218 MiB | 更慢，无额外余量 |

重启后验收（31.5 MiB 快照，GOMEMLIMIT=176MiB，`verify` 全 10 项通过）：

| 阶段 | 并发 | 结果 | 峰值 RSS |
|---|---|---|---|
| 重启就绪（116 稿本重新准入） | — | healthz 200 | 150 MiB |
| 31 张旧签样下载 + ETag/304 | 1 | 全部 200，摘要逐字节一致 | 198 MiB |
| 交接班后首单（建单/修订/出图/签样） | 1 | 全通；同稿连续 3 次出图摘要一致 | 202 MiB |
| 持久化稿本重放出图（抽样 40 件） | 1 | id+摘要逐字节一致 | 204 MiB |
| 两操作员风暴：镂空+透明，4 同步 + 12 异步突发 | 2 | 同步 4×201（摘要正确）；异步 11 成功 + 1 执行时容量拒绝；无 5xx | 204 MiB |
| **支持包络合计** | **≤2** | **全部通过** | **204 MiB（余量 52 MiB）** |
| 4 路并发同步出图（边界探针） | 4 | 2×201（摘要一致）+ 2×429，无 5xx | 264–296 MiB，**超 256 硬限** |
| 对照：同一 verify 不设 GOMEMLIMIT | ≤2 / 4 | 功能 9/10 通过，但内存超限 | 包络 364 MiB / 探针 770 MiB，**远超硬限** |

准入与一致性：

| 项目 | 结果 |
|---|---|
| 200 段镂空稿（7×7）/ 513px 画布 | POST 即 400，不留历史、不占成品（manifest.admission_rejections） |
| 限内存（176 MiB）vs 不限内存同 seed 各跑一次 | `compare`：232 件成品摘要 + 59 张签样**完全一致** |
| 同 seed 两次不限内存重放 | `compare`：完全一致（班次历史可重放） |

> 精确数字以 `var/acceptance-256m/manifest.json` 与 `verify-report.json` 为准；上表为对应运行的摘录。

## 6. 并发与拒绝规则（收单口径）

- **同时在场调用者 ≤ 2**（正好两名操作员）。这是 32 MiB 快照下的实测安全包络：包络内峰值 204 MiB；4 路并发同步出图实测冲到 264–296 MiB，会顶破 256 硬限。现场如需更多并发，在容器前放限流反代（最大 2 连接）或抬高内存。
- **计算名额 2 个**，同步出图与异步任务共用。第三名同时到达的同步请求立即 429；异步任务先入队（队列 8，工作者 2），提交不下才 429。
- **202 ≠ 成功**。异步任务以轮询到的终局为准：`succeeded` 才有成品；`failed: exposure capacity reached` 是执行时的明确容量拒绝，可原样重投；两者都不会留下半成品。
- **同一（版面, 稿本, 格式）出图是幂等的**：重复提交返回同一成品 id 与摘要，不重复占容量。
- **准入预算**：512×512 画布下轮廓展开后总段数 ≤ 152（4 采样 × 段数 × 像素 ≤ 1.6 亿）。超预算稿在 POST /v1/plates 即 400，不占历史、不出成品。细密镂空稿（6×6 镂空 = 148 段 + 底衬 4 段 = 152 段）是可接单的上限样品；7×7（200 段）必须拒单。
- **任务记录 256 条/进程**：异步任务记录只在进程内保留，用尽后新任务 429，正常重启即清零；已持久化成品不受影响。
- **超限时数据安全**：任何 429/400 期间，已签样成品照常可下载（E9），快照不产生多余成品（E8）。

## 7. 运维边界

- 快照整体复制是本服务的设计取舍（README"持久化与范围"）。32 MiB 快照下，单次读/写的瞬时内存约为快照的 3 倍；这是 256 MiB 预算把历史上限压到约 32 MiB 的原因。
- 并发 API 调用者 ≤ 2 是实测包络（§5）：超出不会出错（429 兜底、无半成品），但内存峰值同步上升，4 路并发在 32 MiB 快照下实测 264–296 MiB，会被 256 硬限杀掉。批量补单走异步任务接口，不要在客户端并发刷同步出图。
- 备份：正常关停后复制 state.json；恢复：保留原件，在独立目录启动验证（docs/operations.md）。
- writer.lock：异常退出后确认无旧写入进程再删除；不能两台服务共享数据根。
- 本验证环境无法施加内核级内存硬限（cgroup 只读、无容器运行时）。结论以 GOMEMLIMIT 软限 + 实测 VmHWM 为证据；现场验收时以容器硬限 256 MiB 重跑 §3.3 的 verify 即完成最终确认，预期结果一致（包络余量 52 MiB）。

## 8. 复现命令清单

```sh
# 构建
go build -o bin/prismplate ./cmd/prismplate
# 生成交接班验收材料（约 4 分钟）
python3 scripts/shift_history.py generate --home var/acceptance-256m --target-mib 31 --gomemlimit 176 --force
# 接班验收（约 2 分钟，全绿才收单）
python3 scripts/shift_history.py verify --home var/acceptance-256m --gomemlimit 176
# 可重放性 / 限内存一致性
python3 scripts/shift_history.py generate --home /tmp/replay-a --target-mib 31
python3 scripts/shift_history.py compare --a /tmp/replay-a --b var/acceptance-256m
```
