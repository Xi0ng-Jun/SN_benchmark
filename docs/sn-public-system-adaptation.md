# 公开 Benchmark 的 SN 系统接入

> 历史实现说明：本页记录公开题 SN 系统接入阶段，当前运行边界和协议见 [评测状态](evaluation-status.md)。


日期：2026-09-13。用户已确认实施；本轮只写代码、回归用例与静态审阅，不运行测试、模型、SN、数据下载或定时任务。

2026-09-14 更新：用户已授权并完成[离线回归](offline-regression-2026-09-14.md)，205 项通过，无跳过或警告；实际检查了 SDK 的客观评分和合成样本适配。真实 SN 问答与正式数据规则审核尚未执行，在线任务继续暂停。

同日最新要求为仅整理[任务与选题方案](public-benchmark-selection-plan.md)，测试与实验再次暂停。选题不设预先题数，选定范围内符合规则的原题全部纳入；下文 40 篇为当前单库实现约束，不能裁剪新题单。新选择器与分库设计尚未实施，现有单库协议保持原样。

## 测量对象

主目标是 Silicon Notebook 系统表现。保留 Native 模型参照，新系统适配使用独立的 `sn-public-system-v1` 协议，不修改已冻结的 `public-starter-v1` / `public-expansion-v2` 数据身份。冻结 manifest 内的 product 字段是历史声明；当前可执行系统适配由独立 registry 决定。

| 套件 | 资料与提问 | 评分与解释 |
|---|---|---|
| SQuAD / DROP / BoolQ | 沿用已实现的配套文章导入、原问题 Ask | 沿用已有产品评分与审核，不改历史协议 |
| LogiQA | 导入原 text；Ask 原 question 和 options | 提取明确最终选项，官方 exact-match；资料覆盖另报 |
| GSM8K | 导入 question；Ask 同一原题 | 最终数值 exact-match；官方答案文本不暗中数值归一化；题面不是答案证据 |
| BBH | 导入 input，按原 task 类型提问 | 按 task 输出域提取最终答案并 exact-match；条件自足/常识依赖分 task 解释 |
| MMLU | 导入原 question 与 choices，明确选项是候选 | 知识题系统作答；不以题库引文证明知识正确 |
| TruthfulQA MC1 | 导入 question 与 seed-42 排序后的 mc1 choices | 固定数字选项 exact-match，不冒充开放式诚实性/拒答评分 |
| HellaSwag | 导入 ctx 与 endings；Ask 选择后续 | 常识推断；候选续写不是已发生事实 |
| IFEval | 导入原 prompt；Ask 严格原样 prompt | 完整答案正文直接交给固定版本 DeepEval verifier，不增加 Final answer 指令、不删引用或解释 |

## 数据边界

生成资料只使用各套明确白名单的题面字段，不遍历 raw_row，不导入 answer/target/labels/解析。选项文本可能包含正确答案，这是原题的一部分；禁止的是标明正确项。原始完整 row、参考答案和审核资料只保存在评测侧。

现有单库协议中，每 suite/mode 新建隔离运行时与 notebook，使用固定资料并集和当前库全库范围，不逐题限制 gold source。每题不传 conversation_id，禁用 Memory/偏好/经验注入。资料去重且超过 40 个时仍显式报错，不静默删题或减少分母。新选题方案将完整题单与后续分库清单分开；须实现明确的分库及运行身份后才能覆盖更大题单，不将缩减题数作为默认处理。不得为通过无来源检查导入无关占位资料。

reasoning 通过产品正常 intent preview 与确认协议提交；仅在原生 preview 无阻塞澄清且允许自动提交时按原样确认，不猜补缺失条件。澄清保留为终态，不绕过检查。产品异常、无答案、解析失败分别记录。文本拒答识别仅作候选观察，未经人审不把它当可靠行为标签。

## 评分和报告

除 IFEval 外，新系统请求明确要求独立一行 `Final answer: ...`，其余解释与引用原样保存。只提取唯一明确的最终答案行，拒绝矛盾的多行结论；不通过猜测正文最后一个数字/字母取得成绩。产品提取规则单独版本化，属于适配协议，不能冒充 Native 原始模型 schema 路径。

调用已冻结 DeepEval 模板核对官方答案与 schema；评分使用对应 exact-match / IFEval verifier，均不调用 judge。IFEval 无人工正反例审计前置条件，全部指令直接按 DeepEval 4.2.2 评分；旧审计元数据不阻止执行。新旧指标与兼容规则见 [直接评分说明](ifeval-direct-scoring.md)。新套件不默认加 Faithfulness：题面与候选项不一定是事实证据。LogiQA 的资料覆盖与所有套件的引用对象存在检查仅作诊断，不证明推导或断言获得支持。

报告按 suite/task/mode/scorer 分组，保留每题计划、回答状态和 null 未评分项。对二元主指标补充“已知答对数 / 全部计划题数”，明确是包含缺失项的答对覆盖率；未完成运行不是最终正确率。已评分均分、输出覆盖率、解析覆盖率和行为候选分开展示。不合并模型与系统总分，不把 N/R 差值当作系统损失的因果估计。引用完整对象、上下文和原始正文可回查。Agent/DAG 未实现，trace 即使 complete 也不产生 Agent 分数。

## 实施边界

已编写上述七套新系统适配，原三套产品路径保留；离线回归已完成，真实产品运行待验证。当前先按新选题方案明确任务范围、完整数据身份与分库设计，随后再实施必要改动；数据准备、测试和在线任务需依照用户后续指示恢复。生产代码/配置、旧 baseline、weekly timer、KG、重排、PDF/OCR、交互可靠性和资料更新一致性保持原边界。