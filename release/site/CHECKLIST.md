# 现场放行核查清单（逐项打勾，留存签字）

机器架构：□ arm64 实机　□ amd64 实机（首次）　□ amd64 仅 qemu 模拟演练
执行人：____________　日期：__________　发布包 built_at_utc：见 release-manifest.json

## A. 装载前完整性
- [ ] A1 `python3 site/verify_release.py manifest --release .` 全部 PASS
- [ ] A2 `SHA256SUMS` 与 release-manifest.json 登记摘要逐条相符
- [ ] A3 选用镜像/二进制的架构标记与本机一致（arm64 ↔ arm64；amd64 ↔ amd64）

## B. 谱系与签样身份（Go 权威核查）
- [ ] B1 `verify-linux-<arch> -snapshot recovery/state.json -bundle ... -manifest ...` 0 失败
- [ ] B2 两条 accepted 校样（proof-amber-png / proof-amber-svg）成品摘要与随包文件全等
- [ ] B3 已签成品状态 available；对其 DELETE 返回 409（受保护不可撤销）

## C. 恢复演练（临时数据根，不碰正式根）
- [ ] C1 `restore_rehearsal.py --arch <arch>` 四阶段 0 失败
- [ ] C2 阶段1：恢复实例 GET 的 PNG/SVG 与签样**逐字节**相同（不是“看着一样”）
- [ ] C3 阶段2：bundle 空实例重出件与签样逐字节相同；若不同，已转独立可用性判定且未覆盖签样
- [ ] C4 阶段3：重名封包导入被 409 拒绝

## D. 两类放行风险回归
- [ ] D1 风险A：重编码 PNG 像素逐字节相同但 sha256 不同 → 已确认不得替换原签样
- [ ] D2 风险A：SVG 几何变体数值序列不同 → 仅可登记为新成品
- [ ] D3 风险B：整图差异占比极低（本次实测约 0.02%）但 ROI(y=27..34) 单通道最大差 86 → 整图相似度不放行

## E. 查看环境纪律
- [ ] E1 放行依据只采用核查器的 sha256/像素/几何数值输出
- [ ] E2 浏览器/看图软件仅作人工复核；已知其缩放、色彩管理、SVG 插值差异不纳入判据
- [ ] E3 amd64 实机/容器真实运行若此前无证据，本次记录为“首次验证”并附本清单

结论：□ 通过，准予按恢复材料投产　□ 不通过，保留原件并上报中央
签字：____________
