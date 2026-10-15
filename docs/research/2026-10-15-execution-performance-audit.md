# 2026-10-15 · 评测执行链路性能／存储审计（无模型）

范围：runner、journal、ledger、batch、post-run 汇总。未调用模型、未派发服务器任务、未改协议或生产配置。证据分层：**本地核实**（代码 + 本机已有工件 mtime + 合成基准）；服务器数值仍 unknown。

## 基线事实（本地核实）

来源：`var/sn-eval/baselines/qasper-20-20261013` 已保存的 40 个 run（chunk/reasoning 各 20 partition），用 batch 行 `started_at/finished_at` 与 run 文件 mtime 推算，取中位数。

| 阶段 | 中位耗时 |
| --- | --- |
| 子进程启动 → artifact-refs | 0.3 s |
| source/configure/provider → manifest | 2.0 s |
| SN `prepare_notebook` 导入建索引 | 18–171 s（SN 侧，非评测框架） |
| 评分（scores 写入） | < 0.2 s |
| 单 run 报告 | 约 0.1 s |
| 父进程 stage metrics + ledger | 0.4 s |

结论：评测侧固定开销约 3–5 s／partition，主耗时是 SN 导入与 Ask；评测侧 CPU/I/O 不是瓶颈。

单 run 存储（同一工件）：`captured-source/` 30 MB（663 源文件 18 MB + 379 个 run 内生成的 `.pyc` 12 MB），`report/` 812 KB，`manifest.json` 148 KB（`identity.code` 113 KB 与 `source-identity.json` 128 KB 内容重复），runtime 2.8 MB。分区规模：HotpotQA 7405 partition（每题一个），MultiHop-RAG 1 partition × 2556 题，QASPER 416，QMSum 35。

## 已实现（局部、可回滚）

1. **ledger 追加 O(N²) → O(N)。** `run_ledger.append_run` 每次全量解析 ledger 查重。新增 `LedgerAppender`（只解析新增字节、记住 run_dir 集合，文件变短或被替换时全量重读；坏行仍按原错误拒绝），`notebook_batch.execute_batch` 默认路径使用它；`append_run` 原函数不变。
   合成基准（约 3 KB 行，同字节 ledger）：1000 run 3.47 s → 0.12 s；4000 run 61.8 s → 0.52 s。
2. **post-run 终态汇总 5 次全量解析 → 1 次流式。** `notebook_runner._saved_failures` 单遍扫描 outputs/scores，error 判定与 failures 字段、顺序不变。`run_support.read_rows` 改为逐行流式（不再先 `read_text` 再 `splitlines`）。
   合成基准（2556 行、247 MB outputs，模拟 MultiHop 单分区）：2.86 s／峰值 RSS 916 MB → 0.25 s／19 MB。
3. **正确性缺陷修复（审计中发现）。** `json.dumps(ensure_ascii=False)` 原样写出 U+2028/U+2029/U+0085，而仓内 29 处 JSONL 读取用 `str.splitlines()`，一行会被拆成两半 → `JSONDecodeError`。已复现：runner 的 `score_outputs` 会因此在答案保存后失败，run 记为 failed 且无分数。修复：`artifacts.jsonl_line` 在 `save_jsonl`、`EventJournal`、ledger 写入时将三者转义（解析值不变，纯 ASCII 行字节不变）；`read_rows` 只按 `\n` 分行，可读修复前写出的文件。

未改变：答案先保存再评分、各状态语义、逐行 fsync、run 隔离、hash/manifest、诊断字段。

测试：新增 4 个针对性测试；相关回归 375 passed（runner/batch/ledger/journal/source loading/runtime deps/artifact store/bundle index/stage metrics/evidence/projection/export/package/workbench job/run views/R1 shared integration）。

独立复核（2026-10-15）：主 Agent 检查上述代码与测试 diff，并独立重跑同一相关回归集合，结果 **375 passed，1 条 Starlette 弃用警告，52.58 s**；`git diff --check` 通过。batch 的 ledger 追加发生在父线程的完成结果循环中，符合 `LedgerAppender` 的单写入者用途；它不是多进程并发查重锁，也不检测已读前缀的原地改写。抽查 bundle、组件补评、oracle 和工作台读取路径确认新核心文件使用已修复写入入口，但不据此声称历史文件或任意外部 JSONL 已全面兼容。此次未独立重跑合成性能基准、调用模型或核验服务器。

## Pending（未改，需要决定或服务器证据）

- **captured-source 每 run 30 MB**：ASQA 948 × 2 mode ≈ 57 GB，Hotpot 7405 × 2 ≈ 440 GB，且每 run 重新编译约 379 个模块。可选方案：store 内一次校验的只读展开树，run 内用硬链接（路径坐标 `<RUN>/captured-source/backend` 不变，每 run 仍按 manifest 重算哈希）。风险：跨 run 共享 inode（root 运行时只读权限不生效）、跨文件系统要回退复制、牵涉已审的 E1/E3 源码绑定契约。建议服务器前由用户决定；最低成本替代是确认磁盘配额。
- **manifest 内重复的 113 KB 源码清单**：属于 protocol identity 输入，改动会改变 protocol_id 格式，不动。
- **`score_outputs` 仍把全部 outputs 留在内存**（MultiHop 约等于 outputs 体积的数倍）；可改为按偏移量索引，暂未做。
- **其余 28 处 `splitlines` 读取器**：新文件已因写侧转义安全；修复前写出且含这些字符的旧文件仍会被它们拒绝。
- **跨 mode 复用 SN 导入／索引**（最大可能的时间收益，约为导入时间的一半）违反"一个 partition × mode × attempt 新 runtime"的不变量，不做。
- fsync 次数（每题约 2 + 指标数）相对模型耗时可忽略，保留以维持"指标完成即落盘"。
