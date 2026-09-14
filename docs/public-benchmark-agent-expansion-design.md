# 公开 Benchmark 与 SN Agent 评测扩展设计

更新时间：2026-09-13

## 目标

在现有 SQuAD、DROP、BoolQ、LogiQA、IFEval 起步方案上扩大 DeepEval 官方公开 benchmark 覆盖，并为 Silicon Notebook 的 Agent 过程评测预留统一 trace 接口。公开 benchmark、SN 端到端结果和 Agent 轨迹结果分开报告。

## 第一批范围

| Suite | 主要能力 | Native 代码 | 当前 SN Product | 说明 |
|---|---|---:|---:|---|
| MMLU | 多领域知识与选择题 | 已编写，未验证 | 系统适配，未验证 | 导入不含正确标签的原题与选项 |
| GSM8K | 多步数学推理 | 已编写，未验证 | 系统适配，未验证 | 原题面作为待分析资料，不导入解题过程 |
| TruthfulQA | 含常见误解问题的答案选择 | MC1，未验证 | 系统适配，未验证 | MC1 题面与候选项；不宣称开放式诚实性 |
| HellaSwag | 语境理解与常识推断 | 已编写，未验证 | 系统适配，未验证 | 情境与候选续写；候选不作为已发生事实 |
| BIG-Bench Hard | 复杂推理子任务 | 已编写，未验证 | 系统适配，未验证 | 原始 input，按 task 标明资料性质与输出域 |

HumanEval、KG、重排、PDF/OCR、交互可靠性和资料更新一致性不在本阶段范围内。

## 两条评测路径

Native track 使用 DeepEval 官方 benchmark 的题目模板和 scorer，衡量被测模型完成通用任务的能力。Product track 将题目所需材料导入隔离 notebook，通过 SN Ask 保存最终答案、实际上下文、引用和状态，再使用适用的 DeepEval 指标及确定性检查。两类分数不合并、不相减。

最新接入见[SN 系统适配方案](sn-public-system-adaptation.md)：新增五套及 LogiQA/IFEval 的 R 默认 `sn-public-system-v1`；冻结数据中的 product=False 保留历史含义，当前执行能力从独立 registry 读取。LogiQA 导入配套文章，IFEval 原始指令不加统一答案格式，其余导入白名单原题面。系统请求不携带答案标签，题面不是正确答案的证据。

显式 `--product-protocol legacy` 才对这些套件保存旧 N/A 记录（不创建 notebook）；原 SQuAD/DROP/BoolQ 产品路径仍按原审核方案执行。新系统路径正常预览 reasoning 意图并保留澄清，不补造澄清答案；其答案提取和评分单独版本化。代码与回归用例只静态审阅，尚未运行。

## Native 协议与结果解释

新增协议为 `public-expansion-v2`。准备阶段读取本地原始 JSONL，保留原始字节 hash 与规范化记录 fingerprint，按固定映射重建 case，同时冻结 DeepEval 4.2.2 的模板、schema、scorer 和相关提示词资源。加载时重新从 raw 构建题目与标签，不能仅凭重新填写 cases hash 接受被改过的标准答案。旧扩展 v1 bundle 缺少这些身份信息，需另行重新准备，不改写历史产物；原五套 `public-starter-v1` 不作这次协议迁移。

MMLU/GSM8K/HellaSwag/BBH 固定零样本，GSM8K/BBH 不启用 CoT；TruthfulQA 固定 MC1，使用官方种子 42 的选项顺序、数字答案 schema 和模板内置示例。各套使用对应官方请求模板、答案 schema 和 exact-match scorer。结构化回答原样交给 scorer；抽取字母、数值等归一化结果仅供排查。例如 GSM8K 参考字符串中的逗号不由本地归一化偷偷移除，TruthfulQA 也不以自由文本命中某个候选字符串充当 MC1 得分。

被测模型通过 SN 模型适配器请求，仍受其结构化输出系统提示等行为影响。这里是冻结协议下的模型参照，不声称与外部排行榜设置完全等价；Native 不经过 notebook 检索或 SN Ask，不能据此推断产品引用、拒答或 Agent 工具能力。

离线检查对 bundle 做原始数据重建，对 run 做计划身份、输出和评分台账校验；普通 JSON/JSONL 仅检查可识别的 trace 字段，不能代替完整 bundle/run 审计。哈希证明工件内部一致性，不代替对上游 revision 和许可证的人工来源核验。

## Agent 评测预留

当前不修改 Silicon Notebook 生产代码。统一结果协议增加可选 `trace`：

```json
{"trace_id":"...","completeness":"none|partial|complete","spans":[]}
```

未来 span 类型包括 `intent`、`query_rewrite`、`retriever`、`reasoning`、`memory`、`answer_generation` 和 `citation_assembly`。只记录结构化动作、输入输出摘要、source/chunk ID、工具参数、状态和耗时，不记录完整 chain-of-thought。若现有 API 或日志无法提供真实有序轨迹，先标记缺失，不虚构 trajectory metric 输入。

轨迹完整后再接入 `TaskCompletionMetric`、`StepEfficiencyMetric`、`ToolCorrectnessMetric` 和 `ArgumentCorrectnessMetric`；只有存在可观察正式计划时才考虑 `PlanAdherenceMetric`、`PlanQualityMetric`。

当前只校验 trace envelope 并汇总完整度，尚无 Agent metric 执行器。即使输入标记为 complete，报告仍显示 Agent 指标未接入；不能将完整度当作任务完成或工具正确性分数。

## DAG 的位置

`DAGMetric` 是 DeepEval 的自定义决策树指标，用于把“是否有引用 → 引用对象是否属于当前 notebook → 是否支持主要断言”等条件拆成判断节点，并为终点分支指定分数。它不是 Agent 执行轨迹，也不替代公开 benchmark scorer。待引用、澄清和拒答规则完成人工校准后，再选择一两个场景试用；在此之前只保留设计，不设置门禁阈值。

## 分阶段交付

1. 扩展 suite、数据 manifest、Native/Product 适用性和离线重建检查。
2. 扩展 runner、结果协议和报告，保持失败、澄清、拒答和不适用可区分。
3. 定义最小 trace/span schema，并用已有 captures 离线构造 `none/partial` 轨迹。
4. 在用户明确恢复在线评测后，选择小样本验证新增 suite；不恢复旧 baseline 或 weekly timer。
5. 根据真实观测和人工校准决定 Agent metrics、DAG 指标及后续最小观测改动。

## 验收标准

- 每个新增 suite 保存来源、revision、hash、原始输入和答案映射；
- 明确 Native/Product 适用性和不适用原因；
- 报告分别展示模型、产品和轨迹层结果；
- 缺失 trace 时不会伪造 Agent 指标；
- 生产项目、配置、timer 和在线服务保持不变；
- 未经人工校准不设置质量门禁。
