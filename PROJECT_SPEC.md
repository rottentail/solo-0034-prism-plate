# 棱版产品与实现规范

用户为需要二维叠色校样的设计集成人员。核心工作是提交几何版面、冻结稿本、生成可追溯图像、校验并移交源稿。没有前端依赖，HTTP JSON是业务入口。

实体：Document（画布背景、尺寸与命名专色表）、Spot（共享专色墨定义与打样色）、Layer（隔离组透明度/混合方式/套印策略）、Shape（局部轮廓、变换与镂空/套印标记）、Brush（实色或渐变，可引用专色）、Revision（序号/摘要/时间）、Plate（版面历史）、Artifact（二进制内容及谱系，含逐专色覆盖版与分版清单）、Proof（终局校样）、Exposure（瞬态任务）、Envelope（带摘要的历史封包）。

职责分工：scene定义契约；geom负责仿射和闭合轮廓展开；validate负责准入与估算预算；paint处理线性光合成与油墨叠印算子；raster负责确定性覆盖采样与PNG；sep负责从几何派生逐专色覆盖版、镂空/套印语义、重新叠合与差异解释；vector负责复用几何的安全SVG；repository负责原子快照、谱系校验与写入锁；exposure负责有界并发与取消；bundle负责封包摘要、版本和导入校验；service维护稿本与成品/校样关系；transport负责HTTP编解码与状态码；cmd负责启动恢复和关停。

主要不变量：旧稿本不可改写；同一expected_revision最多一个成功修订；渲染提交引用固定稿本；成品先完整计算后提交；被通过校样引用的成品不可撤销；被撤销的相同成品不得重新激活；导入不得覆盖已有版面；失败持久化不得产生内存成功；任何HTTP来源场景及恢复场景都必须通过同一编译准入。专色覆盖版是几何超采样alpha，不得由合成RGB像素拆通道得到；专色引用必须指向已声明专色且颜色定义全局共享；一次分版的参考图、全部覆盖版、重新叠合图与清单在同一提交点原子发布；无套印时重新叠合必须与参考PNG字节一致，有套印时差异只能出现在叠印区并在清单中解释。

生命周期：版面active→archived；任务queued→running→succeeded/failed，queued/running可取消→cancelled；成品available→revoked；校样open→accepted/rejected；进程open→draining→closed；封包sealed→verified→imported/rejected，所有阶段由真实执行路径产生，无额外虚构状态字段。

验证边界：所有公开工作流均由scripts/walkthrough.py通过实际HTTP和临时持久化根检查正常与拒绝路径。PNG检查签名、尺寸、摘要与重复确定性；恢复检查重启前后成品内容；并发修订检查冲突与旧稿本保留；spotsep逐像素检查镂空穿透、knockout互斥、overprint保留底墨、覆盖版与清单摘要谱系，以及无套印重新叠合的字节恒等性。此为有界生产演示，不冒充完整视觉正确性证明。单元/性质/视觉回归测试全部延后。未接入远程服务，不需要私人数据库。

配置、容量、接口、复现输入与运维约束见README及docs。内部审计、任务题面、参考实现和标注材料不属于源仓库。
