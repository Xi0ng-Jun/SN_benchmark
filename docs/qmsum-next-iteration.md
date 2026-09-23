# QMSum 结果收口与下一步实现

> 历史记录：本页描述的是此前阶段。2026-09-22 起 Agent 采集/评分以[原生 DeepEval 协议](native-agent-evaluation.md)为准；旧 `--capture-agent-trace`、`--judge/--dag` 和 `inspect_agent_inputs.py` 已退役。旧实验结论及原始工件保留，不执行下文的旧命令。

2026-09-20。输入为用户提供的服务器修订审计报告；数字来自服务器，本机没有下载数据或重算真实答卷。本轮接受审计结论，不再要求同一轮反复审计。

## 已知结果

- 35 场会议、281 题，两种模式各完成 35 runs。chunk 成功 281；reasoning 成功 132、澄清 148、错误 1。
- 原 0.246 / 0.235 是不同题集的会议均值，不能作同题差值。
- 共同 132 题：ROUGE-1 为 0.2506 / 0.2427；ROUGE-2 为 0.0794 / 0.0833；ROUGE-L 为 0.1512 / 0.1535（chunk / reasoning）。会议等权 ROUGE-1 配对差为 +0.0006。均为描述性统计，不推断质量等价。
- 148 次澄清停在 intent_preview；147 次包含模板化指代理由。原始报告的引用表误写 chunk 299 题，与固定 281 题冲突，因此暂不采用该表引用均值，作为局部报告问题记录，不阻塞后续开发。
- 实际路由模型名、引用语义支持没有观测到，保留限制。

## 关键发现与最小证据

服务器 SN commit 为 `ee87fa242ab2338e3fb4ccdd4ff3865266cd05c5`。该版本 `backend/app/services/query_intent.py` 的 `_has_unresolved_reference` 将 `this` / `it` 等视为待解析指代；`plan_query_intent` 还结合模型生成的规范问题和 entities 决定是否强制澄清。意图预览在检索前执行，不能指望会议正文自动消除该检查。

评测侧 `partition_bundle` 在 QMSum 原题后追加 `Summarize the meeting with respect to this query. Use only the meeting transcript.`，其中 `this` 来自评测器。QASPER 原模板的 `if it is not answerable` 也引入同类指代。

本机用 AST 只提取上述 SN commit 的原始正则与纯函数（没有导入产品服务或调用模型）检查：

| 输入 | 指代规则命中 |
| --- | --- |
| `Summarize the whole meeting.` | false |
| 原题 + 旧 QMSum 提示词 | true |
| 原题 + `Provide a query-focused summary using only the meeting transcript.` | false |

这证明评测包装可引入额外的澄清触发条件，不证明所有历史澄清的根因，也不保证在线澄清率降至零。

## 实施顺序与边界

1. 在现有 partition → run 路径新增显式 `--request-revision notebook-request-v2`。默认及缺字段的历史实验仍用 `notebook-request-v1`。新模板只调整评测追加指令，不改原题、资料、gold、评分器、原生澄清门或会话历史。
2. 将请求版本纳入 product bundle 和 notebook_context 身份；旧包字节结构不变。新旧版本不属于同一模式比较配置族。无需重新 prepare 原始数据。
3. Agent 离线诊断读取已保存的 intent_preview/request/response，输出进入 Ask 与否、终止阶段、澄清理由、原题和实际请求。不伪造 reasoning steps，不把预览信息升级为完整轨迹。
4. 补必要的版本兼容和终止状态回归；通过后结束验证，不启动本机模型实验。

服务器下一步：先整合其模型配置/并发修复与本地新增代码，在独立 run 中使用新模板执行一个预先选定会议的 chunk/reasoning，然后按已批准的实验安排继续。新模板与旧模板的比较必须标注为请求版本变化；两侧同时采用 v2 才构成新条件下的模式对比。若该模板调整仍有大量澄清，记录结果后继续 BM25 对照与其他 benchmark，不无限调整措辞或挑选得分。

后续优先顺序：QMSum BM25 对照 → 其余已批准的 Notebook benchmark → 有完整 trace hook 后启用 Agent/DAG 语义评分。当前的确定性阶段诊断可以直接用历史答卷，不需要重问。

完整的三条 QMSum 路径示例和代码数据流见[QMSum 三路径说明](qmsum-three-paths-walkthrough.md)。

## 服务器交接

收到代码后按以下顺序推进，不再为旧 QMSum 报告安排重复审计：

1. 先将服务器现有 `run_notebook_benchmarks.py`、`notebook_runner.py`、`starter_runtime.py` 的模型配置注入与并发修复保存为本地提交，再整合远程开发分支。新代码尚未包含这些服务器独有补丁；不能用覆盖文件的方式丢弃它们。特别保留 `--model-config` 与新增 `--request-revision` 两条参数链。
2. 保留原 70 runs 及修订报告作为 request-v1 结果。新运行直接复用 `qmsum-data-v2` bundle，在新目录显式选择 request-v2，两种 mode 同时采用新模板。不要只重跑旧失败题再与旧成功题拼接成新全量结果。
3. 固定一场会议（例如包含 `qmsum:18:general:0` 的分区，从 partitions 中查其真实 ID），按正常原生导入/Ask 执行 chunk/reasoning。只确认新参数与模型注入可用、原题不变、run 身份正确，之后按既定资源安排继续完整题单。这个检查只做一次，不按得分或澄清率反复调 prompt。

```bash
python scripts/run_notebook_benchmarks.py \
  --bundle /eval/bundles/qmsum-data-v2 \
  --partition-id '<实际分区 ID>' --mode reasoning \
  --project-root /path/to/silicon-notebook/project \
  --request-revision notebook-request-v2 \
  --run-dir /eval/runs/qmsum-request-v2-meeting18-reasoning
```

路径按服务器实际替换；使用服务器补丁要求的原有 `--model-config` 参数。chunk 使用同一分区及 request revision、独立新 run。保持 SN 版本和模型配置，才有助于研究模板变更；若环境已更新，标明多项条件变化，不单独归因。

4. 对已保存 run 可选生成阶段诊断，直接读文件，不触发问答或 Judge：

```bash
python scripts/evaluate_agent_traces.py \
  --run-dir /eval/runs/qmsum-request-v2-meeting18-reasoning \
  --output-dir /eval/reports/agent-qmsum-request-v2-meeting18
```

打开 `agent-report.md`。阶段字段是可观测状态，不是 Agent 能力分数；没有完整 trace 不加 `--judge` 或 `--dag`。

5. 继续已经实现的 [QMSum BM25 对照](qmsum-bm25-baseline.md)，固定同一 bundle、生成模型配置和检索预算；分别与 SN chunk/reasoning 作同题比较。旧 SN 答卷仍可用于明确标注 request-v1 的对照，不以等待新全量为前提。然后推进其余已批准 benchmark。

后续汇报以新增产物与结果为主：完成哪些运行、新模板下输出状态及共同题分数、BM25 对照结果、阻塞执行的实际错误。模型路由未知、引用语义未评等既有局限继续注明，不要求补齐所有观测才推进。

## 本机验证

67 项相关离线回归通过，覆盖 QMSum/QASPER 请求版本、旧版重建和篡改拒绝、历史官方评分挂接、baseline、执行与 Agent 诊断。新功能测试先复现缺少参数和阶段字段，再实现通过。没有下载数据、运行 SN/模型或修改生产代码；线上澄清变化留待服务器新实验。
