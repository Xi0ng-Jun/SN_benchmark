# 原生评分超时后的推进

文档状态：当前恢复说明。它描述已保存组件补评和整轨迹验收顺序，不把服务器转述当成本地验证。


SN 已回答并保存组件；当前需要恢复评分链路。先补评现有组件，再单独验收整轨迹，暂不扩大题量。此次只改评测框架，服务器已应用的 SN 原生补丁无需重打。

## 已知事实与需要验证的部分

服务器反馈：chunk 最大题完成四项分数、两份检索组件超时；reasoning 已保存回答/组件/轨迹，但 judge 出现 HTTP 504（180s）与 500，无最终分数文件。此处是用户转交的报告，本机没有服务器原始工件。

这些记录说明本次请求链路失败，不能推出“该模型永远评不了 reasoning”。旧版成功评分的轨迹与新版原生 prompt 未核对一致，不能沿用旧 1.4 MB 当作新版实测输入。多指标会增加总耗时，不自动增加每个 HTTP 请求的时间。

| 限制 | 控制位置 | 本次处理 |
| --- | --- | --- |
| 单请求网关 180s | 服务端网关 | 客户端调大 timeout 不能改变它；若仍 504，需要换链路/更快 judge，或由服务维护者调整网关 |
| judge 客户端 timeout / retries | 独立 judge JSON `parameters.timeout/max_retries` | 诊断批次显式 `max_retries=0`；保留已有采样/token 设置，记录配置身份 |
| SDK evaluate 累计时限 | `DEEPEVAL_PER_TASK_TIMEOUT_SECONDS_OVERRIDE`；新 CLI `--task-timeout` | 单个组件指标可以有许多次 judge 调用；给足明确总预算，不改变每次 HTTP 上限 |

固定 SDK 4.2.2 中，同步原生 iterator 的 trace/span 指标直接执行 measure；不是每个入口都套相同的 180s 任务时限。另走 `evaluate` 的多查询样本和新补评入口才有同步任务 deadline。提高 `--task-timeout` 不能替整轨迹保证完成，原生整轨迹仍依赖客户端时限与有限重试。

## 代码与产物

- `run_notebook_agent.py --metric` 可重复，指标 ID：`contextual_relevancy`、`faithfulness`、`answer_relevancy`、`task_completion`、`step_efficiency`、`plan_quality`、`plan_adherence`。后四个完整轨迹指标均需 `--trajectory`。未选择的指标没有假 N/A。原生 span、真实输入及数据采集不因选指标而减少。
- 每个内置指标通过公开 `BaseMetric` 包装，只增加开始事件、结束落盘和调用关联，不改内置模板/算法/阈值。每项完成立即 fsync，不等全题评分结束。`native-metric-events.jsonl` 的 `score_id` 关联 `native-scores.jsonl` 与 `judge-events.jsonl`。
- 新 `score_native_components.py` 用公开 `LLMTestCase/evaluate` 对已保存样本补评。只读原 run，新目录保存原样选中样本、来源哈希、judge 身份、代码哈希、SDK 配置、单项报告与分数。不调用 SN 问答，不导入/检索资料。`--project-root` 只为复用已有显式模型客户端，使用独立 runtime，禁止读取生产 dotenv 默认值。
- 新评分目录包含 `component-score-manifest.json`、`components.jsonl`、`planned-component-scores.jsonl`、`component-scores.jsonl`、`metric-events.jsonl`、`judge-events.jsonl`、`component-score-summary.json`、`sdk/`。不是新的问答 run，也不自动混入旧 run 或 Dashboard。
- judge 事件记录逻辑调用耗时、prompt UTF-8 字节、指标/样本关联及可获得的 HTTP 状态和异常类型。字节不是 token；逻辑调用不是 HTTP 尝试，SN 客户端可能还有 JSON 格式回退。异常正文不落盘；无法判断超时层级时保持未知。

错误、中断、未执行均不填零。SIGKILL 时 started 无终态表示未完成；不能据此说该项已失败，更不能说未选或未开始的项已尝试。SDK 同步 deadline 是软超时：已发出的请求可能继续；结束后阻止后续 judge 调用、忽略迟到回复，不能承诺撤销远端计算。

DeepEval 原生调度仍先轨迹后组件；**先组件后轨迹是服务器分批执行顺序**，没有通过改 SDK 排序实现。现有原生 JSON 整轨迹不回灌私有字段；后续完整轨迹验证用原生运行器重新执行选中题。

## 服务器下一步

1. 保留现有回答、组件、引用、轨迹、客观分和已完成的原生分数；暂停扩大实验，不跑 Dashboard。同步新代码，保留服务器模型配置和已有修复。
2. 从失败 chunk 检索样本选一个真实 `sample_id`，从 reasoning 选一个合成 `sample_id`。读取 `record_type=sample` 的条目即可，不编辑组件内容。每条确认来源 case/span、上下文段数及原始大小。
3. 继续同一个明确的 judge，诊断配置 `max_retries=0`；客户端 timeout 显式设置（例如 190 秒，可观测已报告的网关 180 秒错误），SDK 单项总预算先用 900 秒。900 是本次实验预算，不是质量门槛或万能值。
4. 检索样本只跑 ContextualRelevancy；reasoning 合成先跑 AnswerRelevancy，再以另一新目录跑 Faithfulness。任何一步持续 504/超窗，记录一次失败并停止该组合，不批量重试。其它独立小任务仍可完成。
5. 输出简表：样本/指标/实际 judge/上下文段数/逻辑调用数/最大单次耗时/总耗时/HTTP 状态/分数或失败原因。若多个短调用累计超过旧 180s、在新预算内成功，说明旧外层时限不足；若单次仍 504，应处理网关或 judge。不同 judge 的分数另建批次。
6. 组件链路通过后，再安排一题 reasoning 的原生整轨迹 TaskCompletion/StepEfficiency。可只启用一个指标定位问题，但每条原生运行会重问 SN，不能当作同一次回答；后续正式对比使用清楚标注的原生批次。确认可行后完成 meeting18，其余会议暂不扩大。

示例（所有 ID/路径由服务器从实际工件确定）：

```bash
python scripts/score_native_components.py \
  --source-run /path/to/native-chunk-largest \
  --project-root /path/to/sn-native-evaluation \
  --judge-config /path/to/judge-no-retries.json \
  --sample-id ACTUAL_RETRIEVAL_SAMPLE_ID --metric contextual_relevancy \
  --task-timeout 900 --output /path/to/NEW-retrieval-score

python scripts/score_native_components.py \
  --source-run /path/to/native-reasoning-largest \
  --project-root /path/to/sn-native-evaluation \
  --judge-config /path/to/judge-no-retries.json \
  --sample-id ACTUAL_SYNTHESIS_SAMPLE_ID --metric answer_relevancy \
  --task-timeout 900 --output /path/to/NEW-synthesis-ar-score
```

Faithfulness 同第二条命令，替换 metric 与新 output。来源不覆盖，不自动跳过或重试旧分；显式选择表示一个新评分尝试，不能挑最高分回填。

后续原生入口沿用原参数，加 `--trajectory --metric task_completion --metric step_efficiency` 可只评这两项。PlanQuality/Adherence 后续再选，chunk 无计划仍 N/A。

## 验证和依据

本机阶段性离线回归（评分恢复合并前）：432 passed、0 skipped、网络尝试 0，包含真实 DeepEval 4.2.2 与配对 SN 契约；真实模型延迟、网关和服务器补评尚未验证。合并后的当前主线回归以[评测状态](evaluation-status.md)为准。新用例覆盖逐项持久化、中断及后续未开始项、指标选择下轨迹输入不变、原生样本补评、未知/错配选择拒绝、来源不变、调用关联与 HTTP 504 脱敏、结束后阻止后续调用。审阅另外用真实 SDK、假传输和线程事件复现了多查询任务超时后迟到成功覆盖的问题；修复后两项均保留 error/null，未出现迟到终态写入。现有普通 benchmark/BM25 回归保持通过。

沿用官方公开 [自定义 BaseMetric 接口](https://deepeval.com/docs/metrics-custom)、[组件评测](https://deepeval.com/docs/evaluation-component-level-llm-evals)、[评测配置](https://deepeval.com/docs/evaluation-flags-and-configs)。耐久写入、分批顺序及恢复协议是本项目工程安排，不是 DeepEval 自动提供的保证。
