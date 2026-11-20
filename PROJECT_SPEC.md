# 棱版产品与实现规范

用户为需要二维叠色校样的设计集成人员。核心工作是提交几何版面、冻结稿本、生成可追溯图像、校验并移交源稿。没有前端依赖，HTTP JSON是业务入口。

实体：Document（画布背景与尺寸）、Layer（隔离组透明度/混合方式）、Shape（局部轮廓与变换）、Brush（实色或渐变）、Revision（序号/摘要/时间）、Plate（版面历史）、Artifact（二进制内容及谱系）、Proof（终局校样）、Exposure（瞬态任务）、Envelope（带摘要的历史封包）。

职责分工：scene定义契约；geom负责仿射和闭合轮廓展开；validate负责准入与估算预算；paint处理线性光合成；raster负责确定性覆盖采样与PNG；vector负责复用几何的安全SVG；render提供与数据根无关的PNG/SVG后端及有界计算；repository负责原子快照、谱系校验与写入锁；exposure负责有界并发与取消；bundle负责封包摘要、版本和导入校验；service维护稿本与成品/校样关系，并统一成品身份与发布提交；transport负责HTTP编解码与状态码；cmd负责启动恢复和关停。

主要不变量：旧稿本不可改写；同一expected_revision最多一个成功修订；渲染提交引用固定稿本；成品先完整计算后提交；出图后端不打开数据根；同步与后台共用一条发布流水线；提交点前的取消不产生成品，提交点后的成功不被取消改写；不同字节的同一稿成品各自独立，签样指向当次提交；被通过校样引用的成品不可撤销；被撤销的相同成品不得重新激活；导入不得覆盖已有版面；失败持久化不得产生内存成功；任何HTTP来源场景及恢复场景都必须通过同一编译准入。

生命周期：版面active→archived；任务queued→running→succeeded/failed，queued/running可取消→cancelled；成品available→revoked；校样open→accepted/rejected；进程open→draining→closed；封包sealed→verified→imported/rejected，所有阶段由真实执行路径产生，无额外虚构状态字段。

验证边界：所有公开工作流均由scripts/walkthrough.py通过实际HTTP和临时持久化根检查正常与拒绝路径。PNG检查签名、尺寸、摘要与重复确定性；boundary用真实PNG与SVG字节复核出图计算与发布成品的分离，以及取消落在提交点前后的两种结局；恢复检查重启前后成品内容；并发修订检查冲突与旧稿本保留。内部测试仅固定该边界与双格式输出，不冒充完整视觉正确性证明；更大范围的单元/性质/视觉回归测试仍延后。未接入远程服务，不需要私人数据库。

配置、容量、接口、复现输入与运维约束见README及docs。内部审计、任务题面、参考实现和标注材料不属于源仓库。
