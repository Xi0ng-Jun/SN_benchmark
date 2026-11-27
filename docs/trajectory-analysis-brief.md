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
