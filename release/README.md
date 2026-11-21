# 棱版离线发布包与现场核查指南

本目录由 `release/build/build_release.py` 在中央工位一次性生成，面向**没有外网**的
arm64、amd64 打样机。全部产物带 sha256 摘要，现场无需安装 Go、无需联网，
核查工具只用 Python 3 标准库和随包静态二进制。

## 0. 两条不可逾越的判定纪律

1. **已签样的身份只认字节摘要（sha256）。** 恢复时签样必须下载到与中央完全相同的
   字节；任何“画面看起来一样”的文件（含同像素重编码 PNG）都不是原签样，不得覆盖、
   不得替换、不得改名顶替。
2. **现场重出的新成品单独判定能否用于打样。** 重出件与签样逐字节相同 → 确定性通过；
   不同 → 进入独立的像素/SVG 几何可用性检查并留档，结论即使为“可用”，它也只是新成品，
   原签样保持原样。本服务的稳定标识（版面+稿本+格式+内容摘要）也保证不同字节得到不同 id。

## 1. 发布包布局

```
release/out/
├── release-manifest.json          发布清单：摘要、镜像、签样、两类风险数据
├── SHA256SUMS                     全部文件（含清单自身）的 sha256
├── images/
│   ├── prismplate-linux-arm64.tar docker/podman load 布局，scratch，UID 65532
│   └── prismplate-linux-amd64.tar 同上，amd64
├── recovery/
│   ├── state.json                 中央正常关停后冻结的权威快照（含已签成品字节与校样）
│   ├── amber-plate-bundle.json    版面稿本封包（只含稿本，用于空实例重建）
│   └── signed/
│       ├── amber-proof-rev1.png   已签 PNG（proof-amber-png, accepted）
│       └── amber-proof-rev1.svg   已签 SVG（proof-amber-svg, accepted）
├── risks/
│   ├── riskA-equivalent/          风险A：字节不同但画面等价
│   └── riskB-local/               风险B：整图近似但关键局部偏差（含两份稿件与成品）
└── site/
    ├── verify_release.py          纯 Python 核查器（manifest/png/svg/state/render/risk）
    ├── restore_rehearsal.py       四阶段离线恢复演练
    └── tools/
        ├── prismplate-linux-arm64 / -amd64   随包静态服务二进制（免容器演练用）
        └── verify-linux-arm64 / -amd64       Go 侧谱系核查器（复用服务同一编译/谱系代码）
```

## 2. 离线装载

### 2.1 容器方式（推荐，与仓库 Dockerfile 同入口）

把整包经物理介质拷到现场后：

```sh
# arm64 打样机
docker load -i images/prismplate-linux-arm64.tar      # 或 podman load -i ...
mkdir -p /data/prism-plate-v1 && chown -R 65532:65532 /data
docker run -d --name prismplate -p 7846:7846 \
  -v /data/prism-plate-v1:/data/prism-plate-v1 prismplate:offline-arm64

# amd64 打样机把镜像名换成 prismplate:offline-amd64
```

镜像为 scratch 基础、单静态二进制、非 root（65532）、固定监听 7846、固定卷 `/data`。
装载后先 `curl http://127.0.0.1:7846/healthz`。

### 2.2 免容器回退方式

现场若没有容器运行时，直接运行 `site/tools/prismplate-linux-<arch>`，参数与容器入口一致：

```sh
./site/tools/prismplate-linux-arm64 -listen 127.0.0.1:7846 -data ./var/prism-plate-v1
```

## 3. 恢复流程（先保留原件，独立目录验证）

遵循 `docs/operations.md` 的纪律：**不要在服务写入时替换 state.json**。

```sh
# 3.1 校验整包字节完整性
python3 site/verify_release.py manifest --release .

# 3.2 Go 侧权威谱系核查（选与现场机器一致的架构；复用 repository.Check/bundle.Open/validate.Compile）
./site/tools/verify-linux-arm64 \
  -snapshot recovery/state.json \
  -bundle recovery/amber-plate-bundle.json \
  -manifest release-manifest.json

# 3.3 一键四阶段演练（临时数据根，不触碰真实根；amd64 实机把 --arch 换成 amd64）
python3 site/restore_rehearsal.py --release . --arch arm64
```

演练内容：① 快照恢复后 GET 已签 PNG/SVG 必须与随包签样逐字节相同；
② 空实例只导入 bundle 后重出，独立判定；③ 重名封包导入必须 409；
④ 两类放行风险回归。演练全部通过后，才把 `recovery/state.json` 放进正式数据根。
异常退出遗留 `writer.lock` 时，先确认没有旧进程再人工删除。

## 4. 现场分别核实三件事的检查流程

### 4.1 旧成品是否“原样恢复”——只看字节，不看画面

```sh
python3 site/verify_release.py state --release . --snapshot /正式数据根/state.json
```

- 快照字节与清单摘要一致；快照内成品 base64 解码后的 sha256 与成品自报 digest、
  清单登记摘要、随包签样文件三者全等；
- 受 accepted 校样保护的成品必须仍是 `available`（服务语义上不可撤销，DELETE 返回 409）；
- Go 核查器额外复算稿本 scene_digest、跑统一编译准入与封包内层摘要——
  这是 Python 无法逐字节复现 Go `encoding/json` 编码的部分，必须由 Go 侧工具下结论。

### 4.2 重出 PNG 的像素是否可用——字节身份与像素判定分开

```sh
# 对运行中的实例重出并比对（失败不会改动签样，只报告差异）
python3 site/verify_release.py render --release . --url http://127.0.0.1:7846
# 离线对两个 PNG 文件做像素级判定，ROI 针对边界行带
python3 site/verify_release.py png 已签.png 重出.png --roi 0 27 180 35
```

判定口径：字节摘要不同 → 非原签样（身份问题，一票否决“替换”）；
再看解码像素的差异字节占比、单通道最大差、ROI 局部差，决定重出件本身能否用于本次打样，
两个结论分别记录。

### 4.3 重出 SVG 的几何是否可用——看序列化几何，不看截图

```sh
python3 site/verify_release.py svg 已签.svg 重出.svg [--expect-geometry-equal]
```

检查项：SVG 结构完整、无脚本/事件/外链（仅允许命名空间 URI）、path/gradient/clip/matrix
计数、展开后的数值序列是否一致。SVG 是矢量校样，几何可用不等于与 PNG 逐像素相同。

### 4.4 两类放行风险的预期结论

```sh
python3 site/verify_release.py risk --release .
```

- **风险 A（字节不同但画面等价）**：`riskA-equivalent/amber-proof-reencoded.png` 与已签 PNG
  解码像素**逐字节相同**，但 sha256 不同（仅 zlib 容器不同）；
  `amber-proof-geometry-variant.svg` 多一条 2% 不透明的偏移轮廓。
  预期结论：像素等价成立，**但二者都不能替换签样**，只能登记为独立新成品。
- **风险 B（整图看着一样但关键局部有偏差）**：两份稿件都含靠近采样边界的细曲线
  （1px 宽、Q 曲线贴合像素边界）和低透明线性渐变（图层不透明度 0.18）；
  变体稿仅把细曲线横向平移 0.55px。整图差异约 0.02%，肉眼整图极易误判，
  但边界行带 y=27..34 的单通道最大差达 86/255。预期结论：整图相似度**不构成放行**，
  必须通过 ROI/几何核查才能认定局部偏差。

## 5. 查看环境会影响哪些判断

| 判断 | 是否受查看环境影响 | 说明 |
|---|---|---|
| 字节身份（sha256/ETag） | 不受影响 | 纯字节运算，任何机器结论一致 |
| PNG 解码像素、逐像素差异、ROI | 不受影响（本核查器自行解码，不经过看图软件） | 8-bit RGB/RGBA、非隔行，过滤还原在工具内完成 |
| SVG 序列化几何/结构安全 | 不受影响 | 只分析文本与数值序列，不调用浏览器 |
| 用看图软件/浏览器“肉眼看”PNG | **受影响** | 缩放重采样、色彩管理（ICC/sRGB 转换）、透明背景合成色、显示器 profile 都会改变观感 |
| 浏览器渲染 SVG 的像素效果 | **受影响，且不承诺一致** | 仓库 README 明确：浏览器在覆盖率与透明渐变插值上可能与权威 PNG 渲染器不同；`color-interpolation`、混合模式实现也有差异 |
| 低透明渐变、亚像素细曲线的肉眼评估 | **高度受影响** | 正是风险 B 的载体：不同查看器的插值/抗锯齿让局部偏差在整图层面被淹没 |

结论：放行证据以核查器的字节与数值输出为准；肉眼查看只用于人工复核，不能替代摘要、
像素和几何检查，截图更不能作为 SVG 几何判定依据。

## 6. 实机 / 模拟执行 / 未验证组合（本次发布的证据边界）

| 组合 | 状态 | 证据 |
|---|---|---|
| linux/arm64 二进制在 arm64 实机 | **实机已验证** | 构建主机即 aarch64；14 个 walkthrough 流程 + 四阶段恢复演练全部通过，重出与签样逐字节相同 |
| linux/amd64 二进制经 qemu-x86_64-user 模拟 | **模拟执行已验证（非实机）** | 同一份恢复演练在 qemu 下全过，输出摘要与 arm64 实机逐字节相同；Go 核查器模拟执行同样通过 |
| linux/amd64 二进制在 amd64 真实硬件 | **未验证** | 静态 Go 二进制、渲染路径为确定性整数/浮点运算，且模拟层已证明两架构同输入同字节；但首次上 amd64 实机仍必须先跑第 3 节全套核查，通过后方可投产 |
| 容器镜像 `docker load`/`podman load` 后运行 | **未验证（构建环境无容器守护进程）** | 镜像 tar 的 docker-save v1 布局、架构标记、非 root、入口、层摘要由 `manifest` 子命令静态校验通过；真实运行时装载需现场首次执行时确认 |
| 浏览器内 SVG 视觉效果 | **未验证且不作为判据** | 按仓库契约，SVG 仅为矢量校样，像素以 PNG 为权威 |
| 篡改/负向路径 | **已验证（实机+模拟各一）** | 翻转快照一个字节 → `repository.Check` 判 invalid artifact lineage；改封包背景色 → `bundle.Open` 判 digest mismatch，均非零退出 |

## 7. 重新生成发布包（仅中央工位，需要 Go 工具链）

```sh
python3 release/build/build_release.py \
  --go /path/to/go/bin/go \
  --qemu /path/to/qemu-x86_64-static   # 没有 qemu 时留空，amd64 仅产出未模拟验证的二进制
```

构建脚本不访问网络；跨架构编译由 `GOOS/GOARCH` 完成，CGO 关闭，产物全部静态链接。
每次构建重新生成全部摘要；`release/out`、`release/dist` 不纳入版本控制。
