# 棱版产品与实现规范

用户为需要二维叠色校样的设计集成人员。核心工作是提交几何版面、冻结稿本、生成可追溯图像、校验并移交源稿。没有前端依赖，HTTP JSON是业务入口。

实体：Document（画布背景与尺寸）、Layer（隔离组透明度/混合方式）、Shape（局部轮廓与变换）、Brush（实色或渐变）、Revision（序号/摘要/时间）、Plate（版面历史）、Artifact（二进制内容及谱系）、Proof（终局校样）、Handover（定稿移交清单：选定稿本、PNG/SVG与accepted校样引用、缺件策略与自校摘要）、Exposure（瞬态任务）、Envelope（带摘要的历史封包）。

职责分工：scene定义契约；geom负责仿射和闭合轮廓展开；validate负责准入与估算预算；paint处理线性光合成；raster负责确定性覆盖采样与PNG；vector负责复用几何的安全SVG；repository负责原子快照、谱系校验与写入锁；exposure负责有界并发与取消；bundle负责封包摘要、版本和导入校验；service维护稿本与成品/校样关系以及定稿选稿和移交核对；transport负责HTTP编解码与状态码；cmd负责启动恢复和关停。

主要不变量：旧稿本不可改写；同一expected_revision最多一个成功修订；渲染提交引用固定稿本；成品先完整计算后提交；被通过校样引用的成品不可撤销；被撤销的相同成品不得重新激活；导入不得覆盖已有版面；失败持久化不得产生内存成功；任何HTTP来源场景及恢复场景都必须通过同一编译准入。定稿必须显式选定稿本，不默认取最大稿号；清单中的PNG、SVG与accepted校样必须属于同一选定稿本且成品仍available，SVG不得顶替像素承诺对应的PNG；缺件策略只允许reject（拒绝并不归档）或exclude（带显式null缺口归档）；清单自校摘要与全部引用关系在写入和恢复时都必须重算通过。

生命周期：版面active→archived（archived由显式定稿产生，同时生成唯一Handover）；任务queued→running→succeeded/failed，queued/running可取消→cancelled；成品available→revoked；校样open→accepted/rejected；进程open→draining→closed；封包sealed→verified→imported/rejected，所有阶段由真实执行路径产生，无额外虚构状态字段。

验证边界：所有公开工作流均由scripts/walkthrough.py通过实际HTTP和临时持久化根检查正常与拒绝路径。PNG检查签名、尺寸、摘要与重复确定性；恢复检查重启前后成品内容；并发修订检查冲突与旧稿本保留。finalize工作流构造最新稿未过审阅的多稿历史，检查非最大稿号定稿、错稿本/未审阅/已撤销选择拒绝、reject与exclude缺件策略、清单逐项核对，以及归档前后成品、审阅与源稿在重启后的对应关系。此为有界生产演示，不冒充完整视觉正确性证明。单元/性质/视觉回归测试全部延后。未接入远程服务，不需要私人数据库。

配置、容量、接口、复现输入与运维约束见README及docs。内部审计、任务题面、参考实现和标注材料不属于源仓库。
