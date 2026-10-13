# R1 规划的 agent 执行 prompt

2026-10-13。配套 [目标与规划](../../goal-and-roadmap.md) 第 5 节。每个工作项给出推荐 agent／模型、owned 范围、必读输入、交付与验收、禁止事项，以及可直接使用的 `paseo run` 命令。统筹（shared 文件与长期文档的唯一写入者）现由 `codex/gpt-6.1-sol` high 担任；各工作项只写自己的 owned 文件，统筹负责接线与文档。

## 模型选择原则

| 工作性质 | 推荐 | 理由 |
| --- | --- | --- |
| 日常统筹、接线、基线执行、文档收口 | `codex/gpt-6.1-sol` high | 用户要求尽量少调用 Fable 并以 Sol 6.1 替代 Astra；Sol 6.1 已承担过本仓库多数开发与评审工作 |
| 跨阶段判断、读错题定分类标准、里程碑收口 | `claude/claude-fable-5-1` high，按需少量介入 | 只在需要跨仓库判断时使用；今日已验证能独立跑通全链路 |
| 有明确接缝的后端工具与脚本 | `codex/gpt-6.1-sol` high | 与现有代码风格一致，适合在既有模块上做窄实现 |
| 整体体验负责的界面重做 | `claude/claude-opus-5-5` high | 需要对整体体验负责而非单文件切片；此前 Flash 单文件切片的结果与预期差距大 |
| 机械性迁移（归档搬运、表格填充） | `pi/deepseek/deepseek-flash` max | 便宜，但只在范围完全明确、无设计判断时使用 |
| 不再使用 | `codex/gpt-6-astra` | 用户 2026-10-13 指定以 Sol 6.1 替代 Astra |
| 里程碑边界的一次独立回看 | `claude/claude-opus-5-5` high 或 `codex/gpt-6.1-sol` high | 按风险触发，一次即可；reviewer 的产出是“我试图弄坏它的方式与结果”，不是 hash 核对 |

所有 agent 使用现有 workspace（`wks_33bfcd650d3260f6`），`paseo run --background`，保留完成通知，不轮询。Codex 用 `--mode full-access`，Claude 用 `--mode auto`。

## 通用前置段（每个 prompt 开头原样包含）

```
工作目录 /home/wabiwabi/silicon-notebook/benchmark-deepeval/.worktrees/benchmark-protocol-correctness。
先读 AGENTS.md、docs/goal-and-roadmap.md、CURRENT_STATE.md 的不变量与活跃约束，再读本任务列出的文件。
不变量：生成不见 gold；状态真实不补零不筛题；证据来自实际输出；答案先保存再评分；实验与生产隔离。
约束：保留现有 dirty/untracked；不 commit/push/stash/reset/clean；不改 /home/wabiwabi/silicon-notebook/project 原树、原数据库、原 .env、原 model-services.toml；不输出凭据、.env 正文或服务地址；不启动或重启 preview/daemon；不触碰 var/sn-workbench 用户 state。
只写本任务列出的 owned 文件；共享文件与长期文档由统筹接线。完成时给出：改了哪些文件、实际运行的命令与退出码、未做的事与原因。不要生成按 agent ID 命名的证据目录。
测试用 .venv/bin/python -m pytest；真实模型调用只在本任务明确允许时进行。
```

## M0-A 文档重构与能力表（Sol high）

```
任务：把长期文档从日志式改为状态式。
owned：docs/goal-and-roadmap.md 第 3 节能力表（只更新状态列与证据列，不改目标与规划）、新建 docs/archive/2026-10-source-execution-history.md、CURRENT_STATE.md、docs/evaluation-status.md、ARCHITECTURE_CURRENT.md 中按日期追加的“当前／待审／历史”段落。
做法：把 CURRENT_STATE.md「活跃约束」里属于历史时点的段落整段搬到 archive 文件并注明原位置，只保留仍有效的不变量与约束，每条不超过三句；evaluation-status 的 2026-10-11 至 10-13 各节压缩为一段并链接 archive；ARCHITECTURE_CURRENT 中“2026-10-xx 增补”段落合并为一个“工作台与源码执行现状”小节。
保留：所有原红灯、失败证据与限制表述的含义；删除的只是重复与过程描述，不删除事实。
验收：新读者只读 CURRENT_STATE 与 goal-and-roadmap 能说出当前能做什么、不能做什么；git diff --check 通过；不改源码与测试。
```

```bash
paseo run --background --provider codex/gpt-6.1-sol --mode full-access --workspace wks_33bfcd650d3260f6 "$(cat prompt-m0a.txt)"
```

## M0-B 评测工具 skill 与 run 账本（Sol high）

```
任务：让任何 agent 直接使用评测工具。
owned：新建 skills/sn-eval/SKILL.md；新建 src/rag_eval/run_ledger.py 与 tests/test_run_ledger.py；新建 scripts/sn_eval_ledger.py。
必读：RUNBOOK.md 第 3 节、scripts/run_notebook_benchmarks.py、scripts/benchmark_protocol.py、src/rag_eval/run_reader.py、apps/sn-workbench/test-results/source-execution-20261012/e5-s3-real-run-20261013/README.md 与 commands-exits.json（今日真实链路的实际命令）。
SKILL.md 内容：命令清单（prepare 安装 bundle、captured 单分区运行、export-sn、score、load_run 读取、账本查询），每条给何时用、必需参数、产物位置；付费边界（哪些命令会调用模型、运行前必须先出预算卡并由人确认）；禁止事项（不读 gold 进生成、不重用 run 目录、不筛成功题、不输出凭据）；今日单题链路的完整可复现命令序列。
run 账本：runs.jsonl 每行一个 run：run_dir、suite、bundle_id、partition_id、case_ids 数、mode、sn_source_ref、service_config_sha256、runtime_settings_sha256、state、official 分（可空）、阶段指标（可空，M1 补）、创建时间。提供 append（从 run 目录与可选 scores.json 派生，拒绝重复 run_dir）与 query（按 suite/mode/state 过滤，输出 JSON 与表格）。把今日 attempt-1 作为第一行写入 var/sn-eval/runs.jsonl（该路径加入 .gitignore）。
验收：pytest 新测试通过；在新开的 agent 会话中只给 SKILL.md，能在 10 步内复述今日链路的命令并查询账本。不调用模型。
```

## M1-A 批量展开与阶段指标（Sol high）

```
任务：让现有单分区 CLI 可以按题单批量执行，并从已保存 outputs 计算阶段指标。
owned：新建 scripts/run_notebook_batch.py、src/rag_eval/notebook_batch.py、src/rag_eval/stage_metrics.py、tests/test_notebook_batch.py、tests/test_stage_metrics.py。
必读：scripts/run_notebook_benchmarks.py、src/rag_eval/notebook_runner.py（predictions 与 outputs 字段）、src/rag_eval/qasper_evidence.py、src/rag_eval/run_report.py、今日 attempt-1 的 outputs.jsonl 与 model-events.jsonl 作为真实 fixture。
批量：输入为 bundle、store、index、题单文件（case id 每行一个）、mode、输出根目录；按分区分组，每分区一个新进程一个新 run 目录（复用现有 CLI 的 argv，不改 runner），顺序或 --parallel N；每个分区结束后写 run 账本一行（调用 M0-B 的 append，若尚未交付则先写本地 jsonl 并标注）；失败分区记录退出码与 stderr 尾部，不重试，不跳过。
阶段指标（只读 outputs，不调模型）：gold 证据召回率（gold 段落有多少出现在 retrieval_context／captures 的上下文中）、上下文精度（上下文块中相关块占比）、引用精度（anchors 命中 gold 的比例）、拒答率、每题模型调用数与延迟（来自 model-events 与 usage）。QASPER 优先；Hotpot 与 MultiHop 若字段可得一并支持，否则明确标不适用。输出 stage-metrics.json 到 run 目录并可汇总多个 run。
验收：合成 fixture 与今日真实 run 上测试通过；在真实 run 上算出的阶段指标与人工核对一致（今日那题：16 个检索块、1 个相关、evidence 命中）。本任务不调用真实模型。
```

## M1-B 基线运行与读错题（Sol high 执行；错题分类标准由用户与 Fable 少量介入确定）

```
任务：跑 QASPER 20 篇论文基线并完成首批错题分类。
前置：M1-A 的批量脚本可用；用户确认预算卡（题数、预计调用数按今日每题 20 次估算、模型服务）。
做法：分层随机选 20 篇论文（固定 seed 写入题单文件），其中 5 篇标为留出；chunk 与 reasoning 各跑一次；export-sn 与 score；阶段指标与账本写入。然后按分差与状态排序，逐题读最差 20 题，给出失败类别初判（检索未召回／召回但综合错／不该答却答／引用错误／格式或截断／其他）并引用案例文件；交给用户确认。
产物：var/sn-eval/baselines/qasper-20-<seed>/（题单、两份 run 账本行、scores、阶段指标汇总）；docs/failure-ledger.md 首批条目（类别、归因组件、证据路径、确认状态）。
禁止：不按分数换题、不重跑低分题、不把留出集纳入读案例。
```

## M2-A oracle 轨道与翻转表（Sol high）

```
任务：实现 oracle context 诊断轨道与两 run 翻转表。
owned：src/rag_eval/oracle_track.py、src/rag_eval/flip_report.py、scripts/compare_runs_flips.py、对应 tests；runner 侧的最小显式入口由统筹接线，你只提供函数与契约。
oracle：给定 run 的 product-bundle 与评测侧 gold 证据，构造“只含 gold 段落”的上下文输入，走生成阶段，产物目录标 kind=diagnostic/oracle，manifest 加 diagnostic 标记，export-sn 必须拒绝把它当正式 run。
翻转表：输入两个 run 目录（或账本两行）与 scores；校验同 bundle、同 case 集、同 scorer；逐题列出 before/after 分数、状态、翻转方向，汇总变好／变差／不变计数；复用 benchmark_comparison 的准入与配对核心，不重写；输出 JSON 与 Markdown。
验收：合成双 run fixture 测试通过；用今日 attempt-1 与自身比较得到全部“不变”；准入失败时分别可读而非报错退出。不调用模型。
```

## M2-B 有效特性集合与分档身份（Sol high）

```
任务：把 runner 实际关闭／开启的 SN 特性作为身份投影，并让比较身份分档披露。
owned：src/rag_eval/feature_identity.py、tests/test_feature_identity.py；benchmark_comparison 的分档披露以函数形式交付，由统筹接线。
必读：src/rag_eval/runtime_environment.py 的 overrides 字典、src/rag_eval/benchmark_comparison.py、docs/goal-and-roadmap.md 4.1 第 1、2 条。
特性集合：从 runtime-identity.json 的 overrides 与 effective settings 派生一个稳定的 feature-set 字典（KG、profile、检索经验、记忆、生成问题索引、缓存、thinking 模式等的开／关），带 fingerprint，写入 manifest.identity。
分档：必须相等（bundle、case 集、scorer）、必须披露（源码修订、配置 hash、特性集合、依赖环境摘要）、无法得知（远端模型代际只记录不比较）。比较报告新增“披露差异”区块，不再因披露档差异拒绝比较。
验收：测试通过；今日 run 的特性集合可打印；两份只在 .env 无关键不同的合成 run 可比较并披露差异。
```

## M2-C Task 绑定 SN worktree（Sol high）

```
任务：让工作台 Task 像 Paseo 一样绑定一个 SN worktree，并让评测从该 worktree 捕获源码。
owned：src/rag_eval/workbench/task_worktree.py 与 tests/test_task_worktree.py；SQLite 迁移只允许新增一张 task_worktrees 表（task_id、paseo_workspace_id、worktree_path、branch、base_revision、start_snapshot_json、created_at），不改既有表；HTTP/CLI 接线由统筹完成，你提供函数与契约。
必读：src/rag_eval/sn_source_capture.py（capture_sn_source 的输入输出）、src/rag_eval/workbench/ 中 Task 与 Paseo 会话的既有模块、/home/wabiwabi/.agents/skills/paseo/SKILL.md 的 workspace 部分、docs/goal-and-roadmap.md M2-C。
做法：
1 绑定：输入 task_id 与一个已存在的 worktree 路径（由 Paseo `create_workspace --isolation worktree` 或 `git worktree add` 创建；本任务不创建 worktree，不登记 Paseo project），校验它是 /home/wabiwabi/silicon-notebook/project 的 git worktree 且不是原树本身；记录 HEAD、分支、`git status --porcelain` 的起点 dirty 清单，并做一次 capture_sn_source 作为起点捕获 ref。
2 读取：给定 task_id 返回绑定记录、当前 HEAD、相对 base 的 `git diff --stat` 与按文件 diff（只读、限制大小、二进制只记名字），并把起点既有 dirty 与之后改动分开标记。
3 运行输入：提供函数返回“在该 worktree 上运行所需的 argv 片段”：先做新的 capture_sn_source 得 ref 文件，--project-root 仍为原 project（只读 .env），--model-config 由调用方传入；不启动进程。
验收：用临时 git 仓库与临时 worktree 测试绑定、diff、起点 dirty 区分、捕获 ref 随改动变化；对原树路径绑定被拒绝。不改 SN 原树，不调用模型。
```

## M3 工作台围绕 run 与案例重做，含初步更改页（Opus high，单一 agent 对整体体验负责）

```
任务：重做 apps/sn-workbench 的中心区与右栏，使其围绕 run 与案例，对照 Paseo 的体验。
owned：apps/sn-workbench/src/** 全部前端文件；后端只读接口若缺可新增 src/rag_eval/workbench/run_views.py 与 tests（只读 run 目录与账本，不写 state）。
必读：docs/goal-and-roadmap.md 第 2.6 与 4.3 节、docs/product/sn-workbench/interaction.md、apps/sn-workbench/README.md、apps/sn-workbench/test-results/ui01-20261012/root/*.png（现状截图）、/home/wabiwabi/paseo/packages/app/src 中 sidebar、agent-panel、files-panel、diff-panel（参考）。唯一数据 fixture：apps/sn-workbench/test-results/source-execution-20261012/e5-s3-real-run-20261013/attempt-1/cli-run 与 official/scores/scores.json；用独立临时 state，不碰 var/sn-workbench。
交付：
1 侧栏：任务行带状态点与待决数；
2 中心时间线：run 卡（进度／状态、官方分与阶段指标摘要、变差题入口），diff 卡位（占位即可），人决策行；
3 右栏：案例列表（按状态、分差筛选）、案例详情（题、答案、gold、论文原文证据高亮、引用对象）、翻转表（M2-A 未交付时用两份同 run 占位）、provenance 抽屉（源码 ref、配置 hash、特性集合、模型服务代际）；
4 把所有免责声明收敛为一个“如何解读”入口；
5 计划页的 case ID 文本框改为选题器（按分区分组、搜索、随机抽样带 seed、显示已选题数与论文数）；
6 初步更改页（Changes）：显示 Task 绑定的 SN worktree（路径、分支、base 提交）、相对 base 的文件 diff（列表＋按文件展开，起点既有 dirty 与之后改动分开标记）、关联假设卡、该 worktree 上跑过的 run（按账本查询）、“验证”动作——R1 阶段生成可复制的批量运行命令并显示预算说明，M4 后变为按钮。数据来自 M2-C 的只读接口；M2-C 未交付前用一个临时 git worktree 的真实 diff 作 fixture。页面不提供提交、合并、回滚。
不做：设置页、Job 派发、新的任务状态存储、任何 git 写操作。
验收：类型检查与构建通过；在浏览器用真实 fixture 截图：默认视图、run 卡、案例证据高亮、翻转表、选题器，各一张，存独立临时目录；三步内从 run 卡到任一错题的证据高亮。最终由用户与 Paseo 并排对照判断，不以评审报告代替。
```

```bash
paseo run --background --provider claude/claude-opus-5-5 --mode auto --workspace wks_33bfcd650d3260f6 "$(cat prompt-m3.txt)"
```

## M4 Job 最小可执行与第一轮改进（Sol high 统筹与实现；假设取舍由用户决定）

```
Sol 任务：Job 最小定义。owned：src/rag_eval/workbench/job_execute.py 与 tests。Job 执行＝预算卡确认后在新 run 目录以子进程运行既定 argv；状态只从 run 目录 state.json 与子进程退出读取；取消是明确动作并记录回执；不实现 claim/CAS/重启对账；不改 jobs schema 以外的表。验收：用今日 argv 在临时 state 中执行一次真实 Job（需用户确认预算），Job 状态与 run state 一致。
Fable 任务：从失败账本选一条假设（候选：chunk 模式 top-k 与 rerank），与用户确认后在 SN 独立 worktree 做最小改动，用 M1 题单配对重跑，生成翻转表，写假设卡；无论结论正负都记录。
```

## 里程碑回看（一次，Opus high 或 Sol high）

```
任务：对 M1～M2 交付做一次按风险的独立回看。只回答三个问题：阶段指标在什么输入上会算错；翻转表在什么情况下会把不可比的 run 比在一起；oracle 轨道有没有任何路径让 gold 进入正式 run。每个问题给出你实际构造并运行的反例与结果。不核 hash，不复述文档，不重跑全量测试。
```

## 启动记录

- 2026-10-13 用户批准开发并指定以 Sol 6.1 替代 Astra、尽量少用 Fable。已并行启动三个 `codex/gpt-6.1-sol` high／full-access agent：M0-A `14537f46…`、M0-B `cb45bc3f…`、M1-A `d8980b91…`；实际 prompt 与启动回执在 `apps/sn-workbench/test-results/r1-launch-20261013/`。完成通知保留，不轮询。交付后的接线由一个 Sol 6.1 high 统筹 agent 完成，Fable 只在跨阶段判断时介入。
- 同日 M1-A 已交付（新增 `run_notebook_batch.py`、`notebook_batch.py`、`stage_metrics.py` 及测试，144 passed；今日真实题阶段指标：召回 1、上下文精度 0.0625、引用精度 1）。统筹职责已移交 Sol 6.1 high agent `a7fa9bc7…`（prompt 在 `r1-launch-20261013/prompt-coordinator.txt`）：等待 M0-A/M0-B、接缝接线、登记 SN 的 Paseo project 与首个实验 worktree、启动 M2-A/M2-B/M2-C（Sol）与 M3（Opus）、准备 M1-B 预算卡交用户确认。Fable 退出日常统筹。

- 2026-10-13 用户指定 Sol 6.1 high 接替 Fable 为统筹及 shared／长期文档唯一写入者。M0-A、M0-B 各挂一次 `paseo wait <完整 id> --timeout 10800`，均 exit 0；最终回复保存 `r1-launch-20261013/final-m0a.txt`、`final-m0b.txt`，M1-A 接缝保存 `final-m1a.txt`。M0-A 文档归档完成；M0-B skill 与 run 账本已交付，今日 attempt-1 首行存在；其全量测试 exit 1（1957 passed、1 skipped、9 个 PYTHONPATH CLI 环境失败），环境补齐后对应文件 17 passed，不能表述全量已通过。
- 统筹接线：`compute_stage_metrics` 已接 `run_report` 单 run Markdown／summary 与 suite×mode 汇总；`run_ledger` 新增 `exit_code`、脱敏 `stderr_summary`（旧行查询为 null、不改写），batch 传递执行事实，ledger CLI 可显式传入。账本只投影阶段 summary，与 M0-B 最终契约一致。红测试 4 failed／exit 1 → 定向 23 passed／exit 0；受影响 12 文件回归 187 passed／exit 0，`git diff --check` exit 0。实际命令见 `r1-launch-20261013/coordinator-commands.json`。未调用模型；model-events 无 case ID，仍只按 Ask 窗口归因并披露未知。

- SN project 已存在（`prj_179d3118fc290133`），`paseo project ls --json` exit 0，按授权跳过 create。`paseo workspace create --isolation worktree --mode branch-off --base 0680aa12313a5d3e67f4e0a4be94144a5b6af28c --new-branch sn-eval-exp-1 … --json` exit 0；实验 workspace `wks_fc0534ddd56f1261`，路径 `/home/wabiwabi/.paseo/worktrees/31u90a1x/sn-eval-exp-1`，分支 `sn-eval-exp-1`。校验原树 HEAD／dirty 清单不变、新 worktree 干净且 HEAD 等于指定 base，exit 0；仅登记与建目录，没有 fixture 改码、配置／数据库改动或模型执行。回执 `r1-launch-20261013/sn-experiment-workspace.json`。

- 下一波四个完整 prompt（通用前置段＋对应工作项＋协作规则）与 `launch-*.json` 已存 `r1-launch-20261013/`；四次 `paseo run --background --json` 均 exit 0。M2-A `462754dc-f4ab-49ad-92ed-ce5d0da6fdf6`、M2-B `6ae7db12-cc69-4a52-ade5-62e1cb511a41`、M2-C `a3eb6828-712f-4a5c-b7c2-a4c7f9ed82fa` 使用 `codex/gpt-6.1-sol --thinking high --mode full-access`；M3 `f83f4a58-50b3-43fd-b4e8-d524b5b48457` 使用 `claude/claude-opus-5-5 --thinking high --mode auto`；均在 `wks_33bfcd650d3260f6`。M3 prompt 含 SN 实验 worktree 的路径、base、分支与真实空 diff 边界。未启动 M1-B 或里程碑回看，前者待预算，后者须 M1-B 与 M2 都交付。

- M1-B 预算准备（仅离线）：固定 seed `20261013`，按每篇题数分层随机 20 篇（6／10／4），72 题；分层留出 5 篇，分析题单排除留出题。题单及抽样 manifest 在 `var/sn-eval/baselines/qasper-20-20261013/`，预算卡在 `r1-launch-20261013/m1b-budget-card.md`；生成命令 exit 0，chunk／reasoning 各 20 个零执行 argv plan。每题约 20 次参考为 2880 次、1242864 chat token；准备按论文复用参考为 1216 次、967056 chat token。embedding token 与 reasoning 放大未观测、不是上限；没有真实模型执行或错题分类，等待用户确认预算，不启动 M1-B 或里程碑回看。

- 四个下一波 agent 各挂一次 `paseo wait <id> --timeout 10800`，没有轮询 agent 状态，等待退出码与交付接线尚待完成；一次等待的 session 记录在 `r1-launch-20261013/coordinator-progress.json`。M1-B 仍保持预算待确认。

- 下一波等待中的阻塞：M3 的唯一一次 wait exit 0（只表示等待结束），实际服务返回 403／账户余额不足，未交付，不自动更换 Opus 或重启该 agent；终态日志 `r1-launch-20261013/final-m3.txt` 已脱敏。M2-A/B/C 的原 wait 仍挂起，不能为续接重新执行 wait；session 与下一步在 `coordinator-progress.json`。按用户“遇到预算／M3 取舍停下”要求，统筹暂停后续接线等待用户决定；开发 agent 保留原任务，不做取消。
- 统筹已先完成一个官方导出边界：合法 run 标记 `diagnostic=true` 或 `kind=diagnostic/oracle` 仍可导出的反例先红（2 failed／exit 1），`benchmark_submission` 导出前拒绝标记；首次回归漏 `json` 导入（101 passed、2 failed／exit 1），补齐后 103 passed／exit 0，`git diff --check` exit 0。它只证明官方导出标记保护，不代表 M2-A oracle 生成入口已接线。M1-B 预算卡已具备具体题单、服务、sha 与估算，待确认；里程碑回看未启动，条件仍是 M1-B 与 M2 都交付。

- 2026-10-13 续接：M2-A／B／C 的原 wait 均已收取，exit 0；没有重新挂 wait 或轮询。各执行一次 `paseo logs <完整 id> --filter text --tail 1`，exit 0，最终回复与接缝分别保存 `r1-launch-20261013/final-m2a.txt`、`final-m2b.txt`、`final-m2c.txt`；agent 自报相关测试分别 112／116／47 passed，不能代替统筹共享接线验收。oracle 真实生成入口、reader／export 更完整保护、同 method 不同 attempt 翻转、feature manifest／比较披露、worktree HTTP／CLI／Agent scope 尚待接线。
- 用户确认原 M3 已恢复；保留原 agent `f83f4a58-50b3-43fd-b4e8-d524b5b48457`，没有重派或换模型，原 403 记录保留。恢复开发证据见 `r1-launch-20261013/m3-recovery-before-logs.json`，尚未交付或做用户并排验收。
- M1-B 模型预算口径补充到 `r1-launch-20261013/m1b-budget-card.md`：`qwen-turbo` 是 chunk／reasoning 共用的 chat 模型，`text-embedding-v4` 做 embedding，`qwen3-rerank` 做重排；每题 20 次口径拆为 chat 720／embedding 2160，按篇复用准备阶段拆为 chat 408／embedding 808。1242864／967056 token 只计 chat；reasoning 与 embedding token／重排消耗未知。QASPER 本地官方评分无 judge 调用，开发 agent 费用另计。此次仅核对与文档记录，未新增真实模型调用；M1-B 等待用户明确预算上限或先行 reasoning 小样本预算，里程碑回看仍未启动。

- 用户要求查询模型价格后，统筹查询百炼官方人民币价格表与模型详情，并只在本地分类实验配置地域为北京，不输出地址／凭据。`qwen-turbo` 输入／非思考输出／思考输出为每百万 token ¥0.30／¥0.60／¥3.00；embedding 与 rerank 输入均 ¥0.50。现有 chat 外推 ¥0.2954–0.4514；embedding／rerank 各 100 万输入的假设场景总计 ¥1.2954–1.4514，再增加 100 万思考输出场景为 ¥4.2954–4.4514，额外迭代输入另计。预算卡已补来源、计算与未知项，机器记录 `r1-launch-20261013/m1b-price-estimate.json`；Decimal 离线计算 exit 0。建议审批 ¥10，仅为待确认余量，不是实测或已实现硬截断；查询价格不构成运行授权，真实模型调用仍为 0。

- 原批准M1-B已终态，执行脚本exit1；chunk 17 finished／3 failed（62 success／10 missing），reasoning 17 finished／1 finished_with_errors／2 failed（39 success／30 clarification／3 missing）。官方Answer F1=0.28073104315453706／0.19377097940632276，分母各72；Evidence F1覆盖62／72与63／72，整体pending。`finalize-m1b-artifacts.py` exit0，写40run阶段汇总、完整官方汇总、baseline报告与20分区翻转（15准入／5拒绝），并在scored及全局账本各追加40run；不得重复执行入账脚本。不同分区evaluator身份保留，不伪造统一答卷。
- 真实读侧接缝：`flip_report` canonical `official_scores_path`／外置评分目录重叠保护、原生非终态pending；五反例先红exit1，受影响五文件119 passed／exit0。命令见 `r1-launch-20261013/coordinator-commands.json`。最终分析55题，留出17题在读partition前跳过；[失败账本](../../failure-ledger.md) 写20道初判，人工确认0／20；run页保持判读入口。
- 用户询问未完成题是否能重试。仅离线准备missing-only的新attempt：chunk10／reasoning3，共5run；不重跑30 clarification／已有答案的Evidence pending。零执行计划与具体预算卡 `m1b-retry-plan.json`／`m1b-retry-budget-card.md`；建议串行一次、共用原¥10账本、不追加金额不释放旧预留。账本占用约¥6.514、可用约¥3.486，费用参考约¥0.16；原预算卡明确排除额外补跑，新增范围待用户确认，没有模型调用。
- 按原授权启动唯一一次里程碑回看：`paseo run --background --json --provider codex/gpt-6.1-sol --thinking high --mode full-access --workspace wks_33bfcd650d3260f6 <prompt全文>` exit0；agent `c7eacb0f-a0b1-4571-8a67-0bcac92f55cf`，回执 `launch-milestone-review.json`。一次 `paseo wait … --timeout 10800` 已挂session59300，不轮询／不重挂。只运行离线反例、只写milestone-review.md，不读留出正文／gold、不调用模型，交付待收取。

- 用户明确批准“按此范围与原总上限补跑”：仅missing13次（chunk10／reasoning3），5个新run、parallel1、共用原¥10总上限，不追加金额不释放旧预留。审批绑定 `m1b-retry-plan.json` SHA（`m1b-retry-approval.json`）。`execute-m1b-retry.py` 已启动session37578，不重启原生成或新补跑脚本；终态后独立评分／报告／新run入账，不覆盖原72题基线。

- 用户批准的13次missing-only补跑已全部有终态输出：chunk10success，reasoning2success／1clarification；`execute-m1b-retry.py` exit0（session37578），`finalize-m1b-retry.py` exit0（session31801）。新5run的官方分、阶段／报告与scored账本在 `retry-attempt-2/`，全局账本新增5行；原72题基线不变，没有高分择优合并。共享账本占用¥6.6277184，观测usage估算¥1.4699017、未确认预留¥5.1578167，剩余¥3.3722816；补跑新增占用¥0.113757，未追加预算。clarification保持终态，不继续补跑。

- 唯一里程碑回看已交付：agent `c7eacb0f-a0b1-4571-8a67-0bcac92f55cf`，原一次wait收取exit0，最终logs exit0；`milestone-review.md` 保留原反例与早期失败，没有新派reviewer或重新wait。统筹复现并修复：missing/pending残留record计分、not_applicable被投影pending、SN run借用另一答卷mode/runtime/features身份、明确oracle provenance经通用submission进入正式评分／配对。新增红12failed／1passed（exit1）→绿13passed（exit0），受影响回归220passed（exit0，1依赖弃用警告）。
- 收口离线回读 `verify-milestone-readback.py` exit0：45真实run阶段cases/summary及原20对翻转admission/summary/comparison与保存值相同，15准入／5拒绝；原163结果工件hash、共享预算不变。`verify-m1b-retry.py` exit0：原SN HEAD/status、实验worktree干净、配置SHA不变；补跑12success／1clarification，累计预算占用约¥6.6277184、observed公开价估算约¥1.4699017、未知预留约¥5.1578167、剩余约¥3.3722816。首次回读脚本误写报告文件名exit1已记录，修正后通过，未重写run。详情 `r1-launch-20261013/milestone-review-resolution.md`／`coordinator-commands.json`。
- Hotpot标题／重复文本可命中文本包含诊断的限制保留，不自行更改指标定义或假设。失败20题仍human_confirmed=0/20，M3仍待用户并排体验验收；M4假设取舍／真实Job及真实oracle未执行，须用户决定并另行确认相关预算。本轮无进一步模型调用、SN改码、preview／daemon／用户state写入。

- 用户2026-10-13决定按建议默认值直接推进第二圈：20题按四类授权确认、H1单来源reasoning过度澄清、同72题reasoning一次parallel1并共用原¥10账本，M3待读阈值及真实diff截图，不追加预算/真实oracle/H2/preview切换。假设卡 `docs/hypotheses/h1-reasoning-clarification.md` 已先写，Task `ae1ec1f7e9124618b7e842d0aaf1ced1` 改码前绑定SN workspace `wks_fc0534ddd56f1261`；独立state在 `/tmp/sn-workbench-h1-20261013-cg1ep59p/state`，用户state不动。起点源码捕获与原baseline ref均 `81a682e4…`，backend/app实际字节一致。
- SN H1实施agent `9bf4a900-8d72-4004-9336-818f2968a1c9`（codex/gpt-6.1-sol high/full-access）在已有SN实验workspace启动exit0；原M3 `f83f4a58-50b3-43fd-b4e8-d524b5b48457` 收四项followup，send exit0。各挂一次wait，session75696／37879，尚待收取；不重新wait或轮询。prompt/receipt与执行脚本在 `r1-launch-20261013/h1/`。共享后端QASPER待读默认strict<0.5可配置、假设卡显式task关联，红9failed→绿9passed，相关33passed/exit0；H1尚无真实模型调用。

## 启动顺序建议

1. 立即并行：M0-A、M0-B、M1-A（三者 owned 文件互不重叠）。
2. M1-A 交付后：M1-B（需用户确认预算）。
3. M1-A 交付后并行：M2-A、M2-B、M2-C、M3（M3 的更改页在 M2-C 交付前用临时 worktree fixture 开发）。
   统筹在 M2-C 前把 SN 仓库登记为 Paseo project（`paseo project create /home/wabiwabi/silicon-notebook/project`），并用一次 `create_workspace --isolation worktree` 建出第一个实验 worktree；这两步只登记与建目录，不改原树。
4. M1-B 与 M2 交付后：里程碑回看一次，然后 M4。

统筹在每个交付后做接线、更新能力表与 run 账本，不另开评审。

- 用户已批准 M1-B 固定题单的 ¥10 预算。统筹增加评测侧同步 HTTP 请求预留／usage 结算，跨进程共享额度；无 usage 保留预留、未知模型／未覆盖 transport／余额不足在请求前中止；SSE `[DONE]` 与 gzip 解码反例已修正（6 passed）。`execute-m1b.py` 启动固定40个 partition×mode 尝试，parallel=2，没有追加生成、重跑、换题；完整退出与消费终态待后续条目。审批不含真实 oracle 或额外实验。
- M3 原 agent 已交付，最终回复保留 `final-m3-delivered.txt`（原403证据仍存）。统筹接 API/config，official score 与实际答卷绑定校验，翻转入口接 M2-A，feature 投影接 provenance。用户确认：主指标<1／非success仅是临时“待读”；侧栏待决只计真实 undecided 记录；本轮判读先写 M1-B 失败账本，run 页保持跳转；任务只展示显式关联 run、全局目录保留全部。类型检查与临时 export exit0；真实 E5 fixture 浏览器16 checks／0 errors；preview、daemon、用户 state 未动。agent 披露过一次 curl 未引号通配符误展开（只发了文件名，未发文件内容／凭据），已停止修正，事实保留在最终回复。
- M2-C HTTP／CLI／Agent scope 已接；绑定必须使用可信注册 ID，CLI `worktree-bind`／`worktree-read`／`worktree-run-argv` 不启动生成。run_argv 新鲜捕获保存 Task/路径/source-ref 关联，runner 写入 manifest；相同 HEAD／相同 ref 不推断任务归属。M2-B runner 保存 effective settings，reader 核验 feature 投影，比较报告披露差异；M2-A 同 method 的不同 attempt 只由显式翻转入口放行，普通比较保持拒重复。shared 回归211 passed、CLI接缝61 passed；oracle 原生chunk综合 seam 与正式报告／explorer拒收反例43 passed。reasoning oracle入口与真实oracle执行未做。
- 选题器导致旧 CommonJS 浏览器 harness 拉入 SVG 原生依赖。统筹在测试 resolver 使用真实 RN web 完整入口、真实 SVG web primitives 与浏览器 Buffer 包，并保持既有行为断言；`node tests/plans-boundaries-browser.mjs` 255 checks通过／exit0。完整命令、早期红测试及本地 scorer 路径失败与恢复见 `coordinator-commands.json`、基线 `scoring-commands.jsonl`。本地评分失败工件保留，使用新恢复目录；不重跑生成。

- 统筹续接实测：计划／比较旧浏览器回归适配 Task 新导航、隐藏 mounted 面板和选题器粘贴状态后，33 checks／0 page errors，exit 0；保留过期响应隔离、固定修订、丢失响应回执及旧列表保护断言。两临时 HTTP 测试服务器已停止，preview／daemon 未动。M1-B chunk 全部终态（17 finished／3 failed），官方评分覆盖72题（62 success／10 missing），Answer F1=0.28073104315453706，分母72；Evidence F1 pending（62／72）。reasoning 继续原已批准的单次执行，不补跑。
