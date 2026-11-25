# 交接：harness-builder 方法的实现（截至 2026-11-25 晚）

## 1. 要做什么

实现一个新方法中与场景无关的部分，即设计文档里的「外部程序」。这一阶段不选定、不接入任何真实场景，包括 ALW。怎么开发已经定好，见 `docs/dev-plan.md`，照它执行。

方法的目标：研究员把手头任何材料丢进去，系统花 token 和时间，把一个通用 agent（Claude Code 或 pi 加一个固定模型）适配到给定任务，产出一个 harness 文件夹。

它和 ssap 的区别：ssap 里「把通用 agent 适配到领域」这一步是研究员和外部 agent 合作完成的；新方法把这一步也交给沙盒里的开发 agent，外部只提供信息，不替它干活。

设计已定稿为草稿，尚无任何实现。完整设计见 `docs/method-design.md`，开工前必读。

## 2. 设计要点

- **四个角色**：研究员；开发 agent（在沙盒里做适配）；做题 agent（被改进、被考试的对象）；外部程序（方法唯一要实现的软件）。
- **研究员提供**：题目和划分、运行方式、打分方式、通用 agent、开发 agent、材料文件夹、限制、无效运行的判定条件、最终考试的设置。
- **六条固定规则**，全部在沙盒外强制：题目隔离；打分不可改；全部留痕；预算和模型由外部强制；材料只增不改；交付冻结、考试只按规定次数进行。
- **开发 agent 的五种操作**：运行、打分、进入题目环境、提问、交付。打分和进入题目环境不调用模型。
- **交付物**：harness 文件夹，硬性要求只有一条启动命令和一份说明每个部分来历的清单。文件夹里可以是通过产品扩展方式加的东西，也可以是一份改过源码的产品。
- **其余全部留给开发 agent**：写不写检查、是否备份中间结果、怎么诊断失分、一次改几处等。

## 3. 研究员已做的决定

1. ssap 的不变式 1（只进不退、禁止中途回滚、只有与考官一致的检查才能覆盖）在新方法里不是规则，只是可供参考的经验。ssap 工作树内的实验仍按它自己的 `AGENTS.md`。
2. 允许开发 agent 看到练习题的参考答案；只禁止最终考试题的内容和答案。
3. 设计风格：简洁明了，场景的复杂性留给 agent；不用自创术语；抽象方法里不预设任何数值；不为了创新点而设计。

## 4. 这次讨论得出的结论

按讨论顺序，供理解设计为什么是现在这样。完整记录见第 7 节。

1. **现在的保底壳是两种东西**：通用的安全部分（`src/ssap/kernel/`，约 1200 行）和领域检查（`src/ssap/checkers/alw/`，约 2600 行）。后者才是真正的领域适配，由人写。
2. **早先对话记录 `hbase_chat.md` 里有几处说得比实际满**：失败分诊写死了 ALW 的分项名；三个开关算出来后没有任何代码读取；「自动建底座」只存在于那份记录里。
3. **封闭的改进循环自己给不了自己五类东西**：目标与规则、量尺、视野、手脚、知识。量尺出错没有任何报错；后三类缺口的症状相同，都是几轮没有进展，和模型能力到顶也分不开。
4. **ALW 上的实际经历**：关键修正几乎都来自循环之外。三次量尺错误（读分程序把损坏文件标成有效；代理把正常退出标成网关故障；诊断把「没测」当成「测得为零」）、预警时间、修复轮数、全部领域检查。
5. **研究员的方向**：把外部的工作拆成信息和干活，干活全部移进沙盒，信息格式不限，谁提供都行。材料不可能一次给全，所以要有不阻塞的提问。
6. **为什么内部的量尺可以让 agent 自己造**：只要最终考试留在沙盒外，内部量尺造错的后果只是考试考不好，不会污染结论。
7. **四个独立 agent 的头脑风暴**（最小设计、已有工作、挑毛病、输入与交付物）：
   - 共同的骨架是一个强的编码 agent、一个存着全部历史和原始记录的文件夹、一个在外面的固定打分器。
   - 给开发 agent 看原始记录，不要压缩过的摘要。
   - ssap 里挡新方向的规则：禁止提示词教程和禁词表；只许改指定路径；练习题内部再遮住一部分；重型题库不自动改全局提示词。
   - 没人想到的失败：交付物只在开发沙盒里能用。对策是考试前在全新环境里试跑一道练习题（已写入规则 6）。
   - 相关已有工作：Meta-Harness、AHE、DGM、SICA、AIDE、ACE、Doc2Harness、Veritas。由子 agent 用 arXiv 接口查的摘要，未复核。
8. **关于「直接修改 harness 本身」**：两种深度都允许。Claude Code 源码不公开，只能通过它的扩展方式改；pi 开源，可以改源码。建议默认从前一种做起（判断，未对比过）。

## 5. 正在进行的验证

目的：检验候选开发 agent 拿到原始运行记录后，能否自己看出已知问题。答案清单 K1–K4 已在看到结果之前写入 `docs/method-design.md` 第 13.3 节。

- 位置：服务器 `/localdata/$(whoami)/hb-check/run1`。材料是第一批 3 题实测中 2 道可见题的原始运行目录副本，题目只有一句「这里有什么问题」。
- 模型：与做题相同（DeepSeek V4.1 Flash），经 `ssap.eval.proposer_sandbox.run_proposer_docker` 启动，最长 90 分钟。
- 状态：研究员报告前四段成功；查看结果时 `findings.md` 不存在。可能还在运行，也可能出错，尚未确认。排查命令已给出（看 `$W/out`、`docker ps --filter name=ssap-proposer`、`tail "$W/run.log"`）。
- 结果怎么用：
  - 四条都指出：「先核对运行和记录」留作建议。
  - 漏掉 K1：题目里加一句「先确认运行和记录本身是否正常」再跑一次（目录 `run2`）。
  - 大部分都漏：换更强的模型再跑。若仍然漏，设计要往回收。
- 这个结果不阻塞实现。

## 6. 下一步

照 `docs/dev-plan.md` 实现外部程序。要点：

- **范围**：只做与场景无关的部分。不需要研究员决定任何场景；测试用的替身（一个最小的桩场景、不调用模型的做题脚本和开发脚本、桩模型服务）是测试代码的一部分，直接写。
- **测试全部在服务器上做**，本机只写代码。研究员不在本机测试。每一步做完后，给研究员一条测试命令，由研究员在服务器上执行并贴回输出。
- **七步**：场景接口和桩场景 → 记录、版本、运行与打分 → 模型出口 → 开发沙盒和五种操作 → 交付、考试、报告 → 六条规则的违反测试 → 真实 agent 的一次冒烟。前六步不调用真实模型。
- **代码安排**：Python，只用标准库，包名 `harness_builder`，模块划分见开发计划第 4 节。
- **服务器上取代码和跑测试的命令**见开发计划第 7 节。其中取新分支的两段命令没有在服务器上执行过，第一次用时先看输出。
- **提交和推送**需要研究员同意后再做。新工作树目前没有任何提交。

开工的第一件事是第 1 步，不需要先问研究员任何问题。遇到需要研究员决定的事再问。

ssap 里可以借鉴的代码（均在 `.worktrees/ssap/` 下，复制后修改，不要引用，也不要改 ssap 工作树）：

| 用途 | 文件 |
|---|---|
| 按运行发凭据并计量模型调用 | `src/ssap/targets/alw/harbor_runner.py` 的 `MeteringProxy` |
| 在容器里启动一个 agent 会话 | `src/ssap/eval/proposer_sandbox.py` 的 `run_proposer_docker`、`proposer_command` |
| 记录的写法 | `src/ssap/eval/event_ledger.py`、`src/ssap/eval/infra_ledger.py` |
| 配置里引用环境变量的做法 | `src/ssap/config.py` |

三处不要照搬：

- `src/ssap/targets/alw/metrics_reader.py` 是方法一侧的读分层，新方法的规则 2 不允许外部程序解读分数。
- `proposer_sandbox.py` 里对提案的各种限制不属于新方法。
- `proposer_command` 启动容器时没有任何限制网络的参数；规则 1 要求的网络限制要重新实现并用测试确认。

## 7. 材料位置

### 本机，新工作树（在这里实现）

`/home/wabiwabi/silicon-notebook/benchmark-deepeval/.worktrees/harness-builder`，分支 `feat/harness-builder`，不带历史，尚无提交。

- `docs/method-design.md`：方法设计，共 13 节。第 12 节是与 ssap 的逐项对照，第 13 节是决定、假设和验证。
- `docs/dev-plan.md`：外部程序的开发计划，下一个对话照它执行。第 7 节是服务器上取代码和跑测试的办法。
- `docs/handoff.md`：本文件。

### 本机，ssap 工作树（只作参考，不要修改）

`/home/wabiwabi/silicon-notebook/benchmark-deepeval/.worktrees/ssap`，分支 `feat/ssap`，提交 `7075999`。

- `AGENTS.md`：ssap 的规则。
- `docs/mcr-plain.md`、`docs/mcr-formal.tex`：ssap 的方法。
- `docs/alw-deployment-log.md`：上机实测和踩坑记录。第 2 节是部署问题，第 4.3 节是第一批实测的问题，第 6.2 节是读分程序的错误。
- `docs/alw-server-env.md`、`docs/alw-progressive-plan.md`、`docs/alw-mcr-instantiation.md`：服务器环境、上机计划、ALW 的评分规则。
- `docs/server-info/`：从服务器拷回的摘要数据。
- `docs/logs/hbase_chat.md`：引出这次讨论的早先对话记录。
- `.cc-history/2026-11-25_153313_…_66de06ec.md`：这次讨论的完整记录，包括四个头脑风暴 agent 的报告原文和给服务器的全部命令。
- `.cc-history/2026-11-25_140419_…_17a05969.md`：含已核实的 Round 2 启动脚本。

### 本机，其他

- ssap 主对话（Antigravity）：`~/.gemini/antigravity/brain/9cde5e0b-3c8c-47a3-9b93-95bdabd5c9f9/.system_generated/logs/transcript.jsonl`。更早的阶段在同目录下的 `90132aa9-…` 和 `4ef0c0f1-…`。
- 记忆：`~/.claude/projects/-home-wabiwabi-silicon-notebook-benchmark-deepeval/memory/`，其中 `harness-builder-worktree.md` 和 `feedback-method-design-style.md` 是这次新增的。

### 服务器（Claude 无法直接访问，命令由研究员转贴）

- ssap 代码：`~/autoalw/ssap`；ALW 仓库：`/localdata/$(whoami)/AnalogLayoutWorld`。
- ssap 状态目录：`/localdata/$(whoami)/ssap-alw-state`。第一批实测的原始目录在其下 `jobs/phase1b-3tasks/harbor/`。
- 这次验证的目录：`/localdata/$(whoami)/hb-check/run1`。
- 镜像 `ssap-alw-agent:2.1.302`；服务器时钟是 UTC（本机 UTC+8）。
- 环境变量的设置方式见 `.cc-history/…_66de06ec.md` 里验证步骤的第 0 段。

## 8. 约束

- 给服务器的命令只用已核实的路径和参数；每段不超过 600 字节（SSH 粘贴过长会丢字符）；先查上面的记录，不要让研究员重找。
- 服务器上的工具在 `~/.local/bin/`，是 Python 3.13 的用户级安装，没有虚拟环境。
- 测试在服务器上做，不在本机做。
- 验证集和测试集不得接触。
- ssap 的 Round 2 可能正在服务器上运行（是否已启动以服务器状态为准）。新工作不要写 `ssap-alw-state/loop` 和它的账本；ssap 实验里被遮住的那部分练习题的逐题明细仍然不拷回本机。
- 本机内存只有 7.8 GiB，测试和子 agent 要限制内存。
- 不引入统计否决门。
- 记录事实、判断和暂定假设时分开写；引用的数字用脚本抽取，没有核对的要注明。
