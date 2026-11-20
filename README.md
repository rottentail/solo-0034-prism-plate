# 棱版 Prism Plate

Go 二维打样渲染服务。HTTP 接收不可变稿本，输出具有内容摘要的 PNG 和 SVG，支持有界异步成像、校样裁决与可移交版面封包。领域为图形渲染，产品形态为 backend；依赖只有 Go 标准库。

## 启动

需要 Go 1.24 或更新版本；本次验证环境为 Go 1.27.0 / macOS arm64。演示流程还需要 Python 3 标准库。构建和运行无需网络。

```sh
go build -o bin/prismplate ./cmd/prismplate
./bin/prismplate -listen 127.0.0.1:7846 -data var/prism-plate-v1
```

```sh
curl -H 'Content-Type: application/json' --data-binary @examples/amber-proof.json http://127.0.0.1:7846/v1/plates
curl -X POST 'http://127.0.0.1:7846/v1/plates/amber-plate/render?revision=1&format=png'
```

第二个响应返回成品 id；以 GET /v1/artifacts/{id} 获取图片。服务不依赖浏览器、GPU、字体或外部图片。默认只监听回环地址，无身份认证；运行边界和容器挂载见 docs/operations.md。

## 场景约定

examples/amber-proof.json 提供曲线、线性渐变、仿射变换、镂空、裁域、二值蒙版和 screen 叠色。坐标原点在左上，颜色为 0..1 的 sRGB 直通色值，PNG 在内部转换为线性光预乘透明度。M/L/Q/C/Z 路径必须显式闭合，轮廓至少三条端点指令；Q/C 分别固定细分16/24段。图层可选 normal/multiply/screen，组透明度在整层合成后施加，蒙版与裁域处于版面坐标，笔刷与填充轮廓处于形状局部坐标。未提供的数值使用 JSON 的零值；形状必须明确传入可逆 transform。

PNG 是像素校样的权威结果：每像素四点覆盖采样，画布最大512×512。SVG 是复用展开几何的矢量校样；浏览器在覆盖率与透明渐变插值方面可能产生差异，不承诺逐像素相同。所有数值和引用均由服务生成，SVG不接受任意标记、脚本或外链。

## 版本及生命周期

新建版面的初始稿本为1。PUT 传 expected_revision，只有当前最新版本相符才追加；旧版本不修改。归档是显式定稿而非无参数状态翻转：设计人员在归档请求中选定定稿稿本（不默认取最大稿号）以及对应的PNG、SVG和accepted审阅记录，服务校验全部引用属于同一稿本后在一个原子快照内生成可核对的定稿移交清单；归档禁止后续修订，仍可渲染历史及从历史分支。异步任务提交即捕获指定稿本；稍后修订不会改变它。任务只在当前进程内保留，重启后以已持久化成品为事实，不支持任务续跑；256条任务记录用尽后拒绝新任务。两名工作者、8条等待队列，所有渲染共用2个计算名额，同步或异步均可能在拥挤时得到拒绝。

取消是请求，已进入成品提交点的任务仍可成功；成功任务的事实不会变为取消。取消或失败不得提交半张图片。成品按版面、稿本、格式、内容摘要产生稳定标识；撤销后不允许再次悄悄激活。校样只有 open→accepted/rejected；通过的校样禁止撤销其成品。

## 定稿移交

最新稿本未必是通过审阅的稿，同一稿本的SVG也不承诺与PNG逐像素相同，因此定稿选择必须显式给出，服务不会替调用方猜。`POST /v1/plates/{id}/archive` 接收 `revision`、`artifacts`（PNG与SVG的成品id）、`proofs`（accepted校样id）和 `on_missing`。选择的成品必须确实属于该版面与该稿本且仍available，accepted校样引用的成品也必须落在同一稿本；错稿本、已撤销、未通过审阅的选择分别以409/404/400拒绝，归档不会发生。

缺少某种成品时由 `on_missing` 显式选择处理方式：默认 `reject` 在PNG、SVG或accepted校样缺失时拒绝并列出缺口，版面保持可编辑；`exclude` 允许带缺口归档，缺失的PNG/SVG以`null`、缺失审阅以空数组保留在清单里，`complete`如实反映缺口。两种方式都不会把不存在的成品编进清单。

移交清单含每行的成品字节摘要与源稿场景摘要、accepted校样到成品再到源稿的引用链，以及覆盖清单全部内容的自校摘要。`GET /v1/plates/{id}/handover` 读取清单，`GET /v1/plates/{id}/handover/verify` 从当前事实独立重算并逐项返回核对结论；清单随快照持久化，重启后仍可核对。详见 docs/api.md。

## 入口

详见 docs/api.md。保存版面、修订、分支、归档、PNG、SVG、异步提交、取消、成品读取/撤销、校样、封包、恢复及写入隔离分别有独立的有界生产流程：

```sh
python3 scripts/walkthrough.py --workflow png
python3 scripts/walkthrough.py --workflow finalize
python3 scripts/walkthrough.py --workflow bundle
```

可选workflow值：create、revise、branch、archive、finalize、png、svg、exposure、cancel、artifact、proof、bundle、restore、isolation、rejection。每个流程在临时数据根启动实际HTTP服务，完成后正常关停，不占用长期端口、不修改演示输入。archive演示空归档被拒与未定稿无清单；finalize演示多稿本下显式选定非最新稿、错选/未审阅/撤销拒绝、reject与exclude两种缺件策略、清单核对以及重启后对应关系保持。

## 持久化与范围

完整状态以临时写入、文件fsync、原子替换提交，失败写入不会成为内存事实。数据根使用排他 writer.lock；干净退出会清除，异常退出后需人工确认没有旧进程再清除。摘要校验保护谱系一致性，不提供恶意篡改认证。启动还会重新编译持久化场景，并重放版面、成品、校样与移交清单的全部谱系校验；不含移交清单的旧快照会自动补齐为空清单后继续使用。封包只含版面稿本，不含成品、校样、移交清单和任务；导入重名拒绝，不合并历史。

上线容量：128版面，每版64稿本，256成品，1024校样，状态快照128MiB，HTTP输入8MiB，单稿本4倍采样预计边遍历不超过1.6亿。容量达到时需要运维规划，不会自动淘汰被校样引用的数据。完整快照复制意味着吞吐适用于小规模打样服务。

自动化测试套件按初始化边界延后，本仓库不含测试命令。已提供构建、静态检查与真实生产流程演示；后续质量保障任务可以建立独立测试体系。Dockerfile提供容器构建路径，容器跨架构和生产部署不在本机Go构建的验证结论内。
