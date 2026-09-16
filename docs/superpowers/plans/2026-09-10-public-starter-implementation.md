# 公开评测起步：P0/P1 代码实施计划

> 2026-09-16：本文为历史实施记录。其中 IFEval 人工正反例审核前置条件已取消，当前直接使用固定 SDK verifier，见 [最新决定](../../ifeval-direct-scoring.md)。无需执行本文旧审核步骤。

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 写出五套公开题的离线冻结逻辑、显式模型适配、逐题评分接口与 BoolQ 产品适配；本轮不执行代码或测试。

**Architecture:** 新增独立 `starter_*` 模块，不改变历史 baseline 入口。数据准备只读本地公开原始导出，SDK 模板与 scorer 延迟加载；模型客户端由调用方显式传入。准备、预测、评分与汇总分开，任何准备动作都不创建产品 runtime 或模型客户端。

**Tech Stack:** Python 标准库；原生协议接口针对 DeepEval 4.2.2 / Pydantic，复用产品 `chat_json` 客户端接口。

**Spec:** [公开评测起步方案](../../deepeval-public-starter-plan.md)。

状态：以下勾选仅表示代码或文档已写入，不代表测试/运行通过；全部验证继续待执行。

## 全局边界

- 用户本轮明确要求专注代码和逻辑、不运行和测试，覆盖技能中的运行测试步骤。既有开发分支继续使用，不提交或推送。
- 不下载数据、不调用 Ask/judge、不导入产品、不恢复 timer、不修改生产代码；不设置质量门槛。
- P0 交付冻结工具，不声称数据已经冻结；P1 交付未验证实现，不声称接口已通过验收。
- N 与 R 分开记录，IFEval 无已执行规则审计时保持不适用；人审标签不由程序生成。

## 1. 数据协议与冻结入口

文件：`src/rag_eval/starter_protocol.py`、`scripts/prepare_public_starter.py`。

- [x] 五个套件声明来源、split、scorer、shots、选题限制；DROP 按实际 section 挑 history/nfl，LogiQA 保留重复任务归属。
- [x] 接收原始 JSONL 及来源记录；校验文件哈希，保存完整 raw row、行号、稳定 ID、公开参考与选题不足原因。
- [x] 来源记录必须含固定 revision、许可说明、来源文件哈希和转换说明；复制本地 SDK benchmark/scorer/schema 源码并记录哈希，不 import SDK。
- [x] 输出独立目录中的 manifest/cases/题卡；拒绝覆盖已有目录；无网络或模型入口。

## 2. 原生协议和模型接口

文件：`src/rag_eval/starter_native.py`、`src/rag_eval/starter_model.py`。

- [x] 从冻结 raw row 调用 4.2.2 原生模板、使用相同 schema 和 scorer；不调用自动下载 dataset 的 loader。
- [x] `ExplicitBenchmarkModel(client, model_id, role, parameters, config_sha256, sink)` 要求客户端和身份；分别构造 tested/judge，无默认供应商。
- [x] 记录实际请求、schema、客户端返回、解析及错误；schema 错误不退回到额外在线调用。JSON 客户端已处理的传输原文不能冒称为 HTTP raw response。
- [x] IFEval 提供规则清单，评分在审计之前明确跳过；不复刻 SDK 中未知规则默认通过的逻辑。

## 3. 产品适配与结果解释

文件：`src/rag_eval/starter_product.py`、`src/rag_eval/starter_results.py`。

- [x] BoolQ 转换为既有 questions/documents 结构；独立问题适用性须由人审阅，通过后才能生成 R 输入。
- [x] 最多 40 个去重原文，先 gold 再固定干扰；三个数据集共享相同构库逻辑，记录不足与排除原因。
- [x] BoolQ 固定首行 `Final answer: Yes` 或 `Final answer: No`，解析失败/相反标签出现时不猜答案；解析覆盖率与有效标签准确率分开。
- [x] 结果按 suite/track/mode/scorer 分组；重复结果、计划外结果及非法分数报错；未完成、错误、未解析、不适用保留数量，均分不填零。

## 4. 文档交接

文件：`docs/deepeval-public-starter-implementation.md`、README、context/status、起步方案。

- [x] 记录输入格式、模块调用关系、隔离配置、未执行状态和后续验证清单。
- [ ] 后续授权验证时：离线检查来源/hash/空样本/重复任务；预存响应检查 schema/异常留痕；BoolQ 检查否定/冲突/解析覆盖；IFEval 正反例审计；随后才讨论在线 P2/P3。

本轮所有验收项均只写代码，不执行其验证。代码完成状态与验证完成状态分别记录于交接文档。
