# 结果保存与导出：离线验证记录（2026-10-08）

本记录对应 `feat/benchmark-protocol-correctness`，基线 `bcea0c51c63729d413709cf664584188f758d9cb` 之后的工作树改动。它只记录代码、模拟边界与合成工件验证，不代表服务器实验或正式成绩验收。实际接口见[存储和回传指南](result-storage-and-export.md)，授权设计及检查清单见 `superpowers/specs/2026-10-08-result-storage-export-design.md` 和 `superpowers/plans/2026-10-08-result-storage-export.md`。

## 评分和持久化边界

五套 full record 与 compact projection 的 prepared 输入等价检查通过。QASPER／Hotpot 保留整份 response、captures 和证据快照，MultiHop 保留排名观测，ALCE 保留 anchors／映射／错误，QMSum 保留完整答案及失败状态。原始记录先校验再过滤；金标、凭据或非有限数值不会因为字段被裁掉而绕过校验。读取期间 outputs 变化时拒绝发布答卷。

共享 store 的并发发布、失败清理、篡改、缺文件、符号链接、角色身份和源码哈希检查有合成回归。分区 capsule 与 canonical 请求等价，多分区上下文不串用缓存。已安装 index ID 必须由 canonical 安装器返回并固定到 campaign 配置；运行时不以可变指针自行决定期望身份。替换 index，或同时替换 bundle/index 后仍声称原来源，都被已有安装身份拒绝。

共享 SN run、QMSum baseline、补评、ALCE attach、Dashboard 和迁移后的 review 包有无模型集成检查。发布目录拒绝与 store 重叠；原输入和源 run 只读。数据库／storage／配置／原始服务日志不进入包，原始 runtime 不删除，逐条 fsync 不改变。

## 合成完整导出与打包

环境：本地 Linux／WSL2，Python 3.13.15，本地临时文件系统。不同规模分别在新进程执行；三个规模曾并行运行，缓存与竞争未受控。数字只用于结构和规模回归，不能推算服务器秒数，也不用于机器无关 CI 阈值。

复现入口：`scripts/measure_result_storage_offline.py <1000|5000|10000>`。它只造合成 QMSum bundle、产品请求、账本、一个 8 MiB 假 runtime 数据库，调用真实 export/package/reader；不调用 SN、模型或 judge。每题原始观测多放 4 KiB 可裁掉 context，资料也是重复文本，**压缩率不代表真实回答／证据**。setup 包含 prepare/install/合成账本写入，不模拟真实 fsync 事件运行。

| 题数 | Setup 秒 | Export 秒 | Canonical 重建次数 | Submission 字节 | Full outputs 字节 | Results 包字节／秒 | Review 包字节／秒 | Export Python 峰值 MiB | 全进程峰值 RSS MiB |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0.328 | 0.664 | 1 | 354,398 | 4,450,780 | 10,929 / 0.119 | 444,938 / 0.447 | 25.54 | 81.38 |
| 5,000 | 1.529 | 3.331 | 1 | 1,770,398 | 22,262,780 | 37,687 / 0.674 | 2,068,580 / 2.333 | 127.64 | 339.33 |
| 10,000 | 3.433 | 7.206 | 1 | 3,540,402 | 44,527,780 | 67,993 / 1.581 | 4,081,635 / 5.026 | 255.29 | 593.89 |

每个 review 包迁移后实际读取同样题数，原始 outputs 仍完整，runtime 未进入包。Export 的文件打开逻辑字节累计约 52.47／262.27／524.51 MB；计数器按每次读模式 `Path.open` 的文件大小累计，**不等于系统实际磁盘读取量**，不覆盖所有底层 I/O。导出仍执行多次常数轮哈希／解析，内存也随 N 增长；本版不是流式常量内存实现。

独立 scope 查找检查使用带比较计数的 case ID：1k／5k／10k 为 2N 次比较；旧版 1k 列表查找为 501,500 次。单题约 2,019,458 字节的 QMSum 合成 full record 投影后为 19,035 字节，完整答案保留。这些说明查找和字段裁剪的结构收益，不能代替真实端到端测量。

## 多 run 去重与包成本

另一组 QASPER v3 合成检查每个规模四个共享 run，包括 evaluator 和 SN 快照。每次 review 打包复制四个唯一对象，分别验证原 store 和包内 store 的四个物理对象；迁移读取只重建一次 canonical bundle。

| 每 run 题数 | Setup 秒 | Package 秒 | Readback 秒 | 包内逻辑字节 | Archive 字节 | Python 峰值字节 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0.276 | 0.547 | 0.050 | 10,880,409 | 273,748 | 2,542,587 |
| 5,000 | 1.306 | 2.092 | 0.303 | 54,588,415 | 1,191,667 | 12,628,713 |
| 10,000 | 2.775 | 3.898 | 0.636 | 109,223,431 | 2,304,810 | 25,240,235 |

这组规模 fixture 使用最小 manifest，完整 reader/补评语义由另一组合成集成测试覆盖。四个 sparse 1 GiB 假数据库的独立打开计数为 **runtime 内容读取 0 次**。Inventory 仍遍历并 stat 每个目录项，很多小文件仍会增加耗时；复制、哈希、包内验证、归档也各有必要的常数轮读写。

## 回归及待验收

最终完整离线回归：**Python 685 passed，1 skipped**（24.06 秒）；**Node 34 passed，0 failed**。独立最终审查另运行 162 项相关测试通过，未发现当前范围内剩余重大问题。`git diff --check` 和 Python 编译检查通过；同步记录见 `evaluation-status.md`。可选真实 Perl ROUGE 未配置时跳过；不将离线等价检查冒充官方模型评分。

仍需服务器按相同题单／源码／模式记录真实 input、source、runtime、outputs、Agent、官方产物的 logical/allocated bytes，导出／打包耗时及 RSS，并核对逐题状态、证据回放和官方实际分母。旧物理 run 副本各自哈希，不能享受新版共享的全部收益；不会自动迁移或删除。新 index 为每分区保存三个 request revision，run 也保存完整 product-bundle；独立 runtime 的占用没有减少。如果 runtime 是主因，应依据 inventory 另行设计明确保留/删除边界。
