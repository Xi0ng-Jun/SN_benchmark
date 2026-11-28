# 轨迹分析说明（给在评测服务器上做分析的 agent 或人）

## 目的

ALW 实验已经跑完（结果见 `docs/alw-deployment-log.md` §7）。分数本身说明不了问题出在哪：多数运行
停在同一个分数上。这里要做的是读已有的运行轨迹，回答两件事：

1. 一次运行的时间花在了哪里；
2. 它在哪些地方卡住了，为什么。

不需要提出改进方案，不需要评价分数高低。要的是有证据的事实，和与事实分开写的判断。

## 背景（只列读轨迹必须知道的）

- 任务：给定晶体管级网表，3 小时内用容器里的工具（KLayout、Magic、Netgen、ngspice、Python 库）
  做出版图，存到 `submission/layout.gds`。官方验收依次看：版图有效、DRC、LVS、后仿真等；LVS 不过
  时分数固定为 0.1，没有有效版图为 0。
- 被分析的 agent：Claude Code 2.1.302 + `deepseek-v4-1-flash`，无界面运行。
- 两种运行方式：
  - `H_raw`：原样运行，3 小时到点被终止。
  - `H_base`：多了一层外壳。agent 被要求在 `work/draft/` 里工作，用 `mcr_promote` 送检并交卷；
    第 140 分钟收到一条时间提醒；第 165 分钟主会话被停止，之后由另一个新会话做最多 15 分钟的修补。
    `trajectory.json` 只含前 165 分钟的主会话，修补会话不在里面。
- 轨迹里会出现两种来自客户端的消息：`Output token limit hit…`（单次回复超过 32000 输出 token，
  被截断后要求续写）和 `API Error: … exceeded the 32000 output token maximum`（会话因此结束）。

## 材料和工具

状态目录记作 `S`（`/localdata/$(whoami)/ssap-alw-state`）。轨迹在
`S/jobs/<批次>/harbor/<运行名>/<trial>/agent/trajectory.json`，每个约 8 MB，不要整文件读入上下文。
用仓库里的只读工具看（只依赖 Python 标准库）：

```bash
cd ~/autoalw/ssap && export SSAP_STATE="/localdata/$(whoami)/ssap-alw-state"
python3 scripts/traj_digest.py list "$SSAP_STATE"            # 列出全部轨迹（批次、运行名、大小、路径）
python3 scripts/traj_digest.py overview <运行名的唯一片段>     # 结果、每 10 分钟的活动、每步一行
python3 scripts/traj_digest.py steps <片段> 120-135           # 第 120–135 步的思考、命令、工具输出
python3 scripts/traj_digest.py grep <片段> 'Netlists .*match' --where obs   # 按正则找步骤
```

- `overview` 每步一行：步号、分钟、活动标签、思考长度、输出长度、agent 自己写的命令说明。
  活动标签是按命令关键字猜的，只用来导航；下结论前要用 `steps` 看原文。
- `steps` 默认每段截到 3000 字符，可用 `--obs N --think N --cmd N` 调整。
- 批次名：`test-H_raw`、`test-H_base`（测试集 14 题各一次）、`phase1c-dev-hbase`（开发集 12 题 × 2）。

## 规则

- 只读。不要改动 `S/jobs` 下任何文件，不要启动容器，不要调用被测模型重跑任务。
- 不要接触验证集（`configs/alw/split.json` 里 `val` 的 14 题）的任务文件；它们没有运行记录，
  也不应该被读。
- 每条结论都要带出处：运行名 + 步号 + 分钟。引用原文不超过两行。
- 事实和判断分开写。数不出来的写“未统计”，没看的写“未查看”，不要估。
- 分钟和步号都从 `overview` 的步骤行或 `steps` 的标题行读，不要估。“时间去向”表里各段按时间顺序排、
  互不重叠；写完后检查一遍：步号变大时分钟不能变小。
- 评价 agent 对剩余时间的说法之前，先算实际剩余时间（180 减去该步的分钟数），再比较。
- 成本只引用 token 数。轨迹里的美元数是客户端按别的价目估的，不要引用。
- 报告里不要出现服务器用户名和密钥。
- 报告用中文，引用的原文保持原样。

## 任务 A：单次运行报告

每次只分析一条轨迹（建议每条用一个新会话，避免上下文被撑满），写到
`S/analysis/run_<批次>_<任务简称>.md`，不超过 8 KB。固定结构：

1. **结果**：一行。官方状态和分数、各项检查是否通过、步数、时长、被截断次数、会话如何结束。
2. **时间去向**：一张表，按时间顺序分段（例如“读说明和工具文档”“单个器件试验”“搭整体版图”
   “整体 DRC/LVS 修改”“仿真”“交付”），每段给出起止分钟和步号范围。分段按这次运行实际发生的事
   来划，不要套用例子。
3. **关键时刻**（各给步号和分钟，没有就写“没有”）：
   - 第一次生成包含全部器件的整体版图；
   - 第一次对整体版图跑 LVS、第一次跑 DRC，以及各自最后一次的结果；
   - 第一次把版图写进交卷位置（`H_raw` 是 `submission/layout.gds`，`H_base` 是 `mcr_promote` 成功）；
   - agent 第一次提到剩余时间；
   - 每次回复被截断时它正在写什么（一句话），截断后是否丢了内容、怎么接上的。
4. **卡点**：按耗时从大到小列。每个卡点写：
   - 现象：发生了什么；
   - 出处：步号范围、分钟范围；
   - 耗时：约多少分钟、多少步；
   - 经过：agent 看到了什么反馈、怎么理解的、做了什么、有没有解决；
   - 判断（标明是判断）：为什么会卡在这里。是缺少关于工具或器件库的知识而靠试验摸索、
     没有执行题面的明确要求、验证反馈看了但修不收敛、被客户端或环境限制打断、时间分配问题，
     还是别的。不属于这几种就直接描述，不要硬归类。
5. **靠试验才弄清的事实**：这次运行里 agent 通过读源码或做小试验才知道的工具/器件库事实
   （例如某个器件单元的几何细节、某个检查的判定条件）。每条给出处和耗时。
6. **结束时的状态**：离通过还差什么；agent 自己最后怎么说的。
7. **没看清的地方**。

## 任务 B：汇总

读完一批单次报告后（只读报告，需要时回到轨迹核对），写 `S/analysis/summary_<批次或名称>.md`，
不超过 15 KB：

1. 卡点 × 运行的表：每个卡点在哪些运行出现、各耗时多少。只列在报告里有出处的。
2. 各运行“关键时刻”的对照表（第一次整体版图、第一次整体 LVS、第一次交付各在第几分钟）。
3. 重复出现的“靠试验才弄清的事实”：哪些事实在多次运行里被各自重新摸索了一遍，合计耗时。
4. 通过的运行和没通过的运行，过程上有什么不同。
5. 同一道题 `H_raw` 和 `H_base` 的过程有什么不同。
6. 哪些卡点看起来和这个具体场景（这套工具、这个器件库）无关，换一个场景也可能出现；
   哪些是这个场景特有的。标明是判断，并给理由。
7. 仍然回答不了的问题，以及还需要看哪些运行。

## 第一批（先做这 4 条，再决定是否扩大）

| 批次 | 运行名片段 | 说明 |
|---|---|---|
| `test-H_base` | `H_base-sky130-internet-ajc-schmitt` | 唯一完全通过的运行 |
| `test-H_raw` | `H_raw-sky130-internet-ajc-schmitt` | 同一题，会话因输出超限结束，无交卷 |
| `test-H_base` | `H_base-sky130-local-two-stage-ota` | LVS 未过 |
| `test-H_raw` | `H_raw-sky130-local-two-stage-ota` | 同一题，会话因输出超限结束，无交卷 |

做完后把 `S/analysis/` 下的报告文本交回。如果某条的 `overview` 输出过大，先加 `--no-steps`
看分段，再用 `grep` 和 `steps` 定位。

## 任务 C：修补会话报告

对象是 `H_base` 运行第 165 分钟之后的修补会话，日志在该次运行目录的
`agent/mcr_snapshot/phase_b_stream.jsonl`（格式与 `trajectory.json` 不同，`traj_digest.py` 直接给它的完整路径即可，
用法相同）。背景：

- 这是一个全新的会话，看不到主会话的任何思考和对话，只拿到一段指令：先读题面，说明上一会话留下的版图在哪、
  哪些检查没过（gate report）、上一会话的交接记录、时间和轮数预算；只许改 `work/draft/`，用 `mcr_promote` 送检，
  通过检查更多的版本才会替换交卷文件。指令原文不在日志里，生成它的代码是 `src/ssap/checkers/alw/repair.py`
  的 `repair_prompt`。
- 同一目录下可以用 Read 看的文件（不一定都存在，先用 Glob 确认）：`pre_repair/workspace/.mcr/gate_report.json`、
  `pre_repair/workspace/.mcr/best/cert.json`（修补前各项检查的结果）、`post_repair/best/cert.json`（修补后）、
  `post_repair/repair_trace.json` 或 `repair_trace.json`；以及这次运行的 `verifier/evaluation/metrics.json`。

写一份不超过 6 KB 的报告，固定结构：

1. **结果**：一行。会话怎么结束的（`overview` 的 `session_result`）、时长、轮数、截断次数；修补前后各项检查的变化；
   有没有成功送检。
2. **接手时的状态**：上一会话留下了什么（有无整体版图、是否布线、哪些检查没过），依据是什么。
3. **时间去向**：分段表，起止分钟和步号。
4. **它做了什么**：按时间顺序，每件事带步号。特别要写清：有多少时间用来重新弄清主会话已经弄清过的东西
   （器件库、规则文件、检查命令怎么用），有多少时间真正用在改版图上；改了什么、送检结果如何。
5. **卡点**：同任务 A 的写法。
6. **结束时的状态**：离通过还差什么；按这次的进度，这类问题在十几分钟内修得完吗（标明是判断，给理由）。
7. **没看清的地方**。

## 任务 D：提案尝试报告

对象是自动优化里“提出修改方案”的 agent 的一次尝试，日志是状态目录下
`loop/round_<r>/attempt_<a>/proposer/proposer_stream.jsonl`。背景：

- 这个 agent 的任务是对现有的辅助流程提出**一处**修改。它在一个隔离的工作区里工作：`harness_src/` 是现有辅助流程
  的只读副本，`candidate_src/` 是可改的副本，`evidence/` 是开发集运行留下的材料，最后要写出 `proposal.json`。
  它有轮数上限和时间上限。任务原文在同一次尝试的 `proposer_ws/PROPOSER_TASK.md`。
- 可以用 Read 看的文件只有这次尝试目录（日志所在目录的上一级）里的 `proposer_ws/PROPOSER_TASK.md`、
  `proposer_ws/proposal.json`（可能不存在）和 `screen/candidate.patch`（可能不存在）。`loop/` 下的其他文件一律不要读。

写一份不超过 8 KB 的报告，固定结构：

1. **结果**：一行。会话怎么结束的（正常结束、到轮数上限，还是被外部停止；被停止时正在做什么）、时长、轮数、
   截断和压缩次数；有没有写出 `proposal.json`，有没有改动 `candidate_src/`（Write/Edit 调用，或命令里写文件）。
2. **时间去向**：分段表，起止分钟和步号。
3. **它看了哪些材料、看出了什么**：按文件列出它读过的 `evidence/` 内容和源码；它据此对“运行为什么失败”下了什么结论，
   引用它的原话（不超过两行）并给步号。它有没有试图弄清运行里时间花在哪、整体版图什么时候才出现；
   材料里有没有这类信息。
4. **它考虑过哪些修改**：每个候选一行：改哪里（主会话阶段的检查/提示/时间安排，还是结束前的修补环节）、
   它的理由、最后选了还是放弃了、放弃的原因（原话 + 步号）。
5. **卡点**：同任务 A 的写法。
6. **这次尝试为什么没有产出可用的方案**（成功的尝试写“为什么选了这个方案”）：标明是判断，给出处。
7. **没看清的地方**。

## 批量运行（无人值守）

`scripts/analyze_batch.py` 为每条轨迹启动一个无界面会话做任务 A，会话只被允许运行
`scripts/traj_digest.py` 和读文件，最终回复存为报告；报告在 `S/analysis/auto/`，会话日志在其下的
`logs/`。已有报告的轨迹会跳过。

```bash
cd ~/autoalw/ssap && export SSAP_STATE="/localdata/$(whoami)/ssap-alw-state"
python3 scripts/analyze_batch.py check                       # 先确认本机的 agent 命令可用，应输出 PASS
python3 scripts/analyze_batch.py list --batch 'test-H_raw*'  # 将要分析哪些轨迹
nohup python3 scripts/analyze_batch.py run --batch 'test-H_raw*' --jobs 3 --summary hraw \
  > "$SSAP_STATE/analysis/auto/batch.log" 2>&1 &
python3 scripts/analyze_batch.py bundle --batch 'test-H_raw*'  # 全部报告合成一个文本文件，便于传出
```

修补会话和提案尝试用 `--kind` 选择（报告前缀分别是 `repair_`、`proposer_`，打包文件是 `bundle_repair.txt`、
`bundle_proposer.txt`）：

```bash
python3 scripts/analyze_batch.py list --kind repair --batch test-H_base
python3 scripts/analyze_batch.py run  --kind repair --batch test-H_base --jobs 3
python3 scripts/analyze_batch.py run  --kind proposer --jobs 3
```
