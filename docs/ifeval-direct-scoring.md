# IFEval：直接使用 DeepEval benchmark 评分

2026-09-16 决定：按照用户要求，取消项目自行增加的人工正反例审计前置条件。Native 和 SN Product 都直接使用固定版本 DeepEval 4.2.2 的 `IFEvalInstructionVerifier.verify_instruction_compliance`；不另造 verifier，不调用 LLM judge，不筛掉 SDK 默认分支。

每题按原始顺序将回答、instruction ID 和对应 kwargs 交给 SDK。所有指令通过为 1，否则为 0；保存每条指令的位置、参数、结果和 SDK 理由。Product 使用 SN 完整正文，包括空白、解释和引用，不提取 `Final answer`。SDK 内部捕获并返回 False 的情况照其原生口径计失败，理由保留；逃逸到适配层的异常按执行错误记录，产品未正常回答单独记录状态。

这是 DeepEval 4.2.2 的实现口径，不另行声称为 Google 原论文 strict/loose 成绩。SDK 某些分支较宽松也照原实现执行。主分是题级全部指令通过率；项目报告同时展示有效评分覆盖率，避免执行失败被均分隐藏。

## 兼容已有实验

新运行在 `manifest.identity.ifeval_scoring` 保存 `deepeval-ifeval-direct-v1`，纳入配置和配对哈希。新 scorer：

| 轨道 | scorer |
|---|---|
| Native | `deepeval.ifeval.all_instructions.v1` |
| SN Product | `product.ifeval.all_instructions.full_body.v2` |

旧运行保留 `audited_all_instructions` 指标及历史说明，不能与新口径合并。数据协议 v1 的 `scorer`、`verifier_audit: pending`、`instruction-audit.jsonl`、题卡和 limitations 均属冻结来源元数据，为兼容旧数据包保留；**不是当前运行的前置条件**。当前实际评分以运行 manifest 的策略和 `planned.jsonl` 为准。`answer_extraction.status: not_applicable` 仅表示 IFEval 不需要提取答案标签，不表示主指标无法评分；主指标状态读取 `scores.jsonl`。不要修改冻结文件解除 pending，也不要伪造审计通过记录。

`--instruction-audits` 及 Python 评分函数的同名参数仅兼容旧调用，IFEval 直接评分忽略它们；不读取或重放审计文件。没有该文件也能执行。

## 实施与验证顺序

1. 更新实际 SDK 离线回归：无审计可评分；原样正文和引用参与；与 SDK `IFEval.predict` 输出一致；重复指令按位置保留。
2. 删除 Native 生成阻断和评分审计阻断，在 runner、system scoring、selection/report 中登记新策略。保留冻结来源结构和旧报告读取。
3. 更新 dashboard 指标说明与当前文档，验证新旧策略分离、旧数据可加载、异常不填零。
4. 只用本地合成行和已安装 SDK 执行禁止网络的回归；不下载数据、不调模型、不操作 SN。检查并提交推送当前分支。

## 公司服务器使用

获取更新后的 `docs/public-benchmark-agent-expansion` 分支；实验仍在运行时使用独立 checkout，不切换正在执行进程使用的源码。复用已冻结的 IFEval bundle 和 partition plan，沿用现有 Native/System 执行命令，省略 `--instruction-audits`，指定新的 run-dir。执行需沿用服务器既有模型配置和隔离规则。

旧 N/A 不会通过更新 dashboard 自动变成分数。本次不提供独立的旧输出重评分 CLI，现有 runner 会在新 run 中重新问答并评分；是否补跑由服务器按实验安排执行，不在本地启动。完成后将新 run 交给现有报告/dashboard 命令，查看新 scorer；旧 run 仍按旧口径展示。

官方参考：[DeepEval IFEval](https://deepeval.com/docs/benchmarks-ifeval)；评分事实同时核对已安装 4.2.2 的 `benchmarks/ifeval/ifeval.py`。

## 本地验证结果

2026-09-16：全量 Python 离线回归 **281 passed**，无失败或跳过，审计钩子统计联网尝试 **0**。使用本地合成输入与已安装 SDK；模型和 SN runtime 用测试替身，没有新增公开数据下载或实际实验。

覆盖 Native/SN Product 直接评分、SDK 单题结果对照、重复指令与默认分支、完整正文/引用、忽略不存在的审计文件、起步/完整题单两种 bundle、分区执行、新旧报告及 Dashboard 指标分离。独立代码审阅未发现阻断问题。

复现命令（从仓库根运行，Python 路径按本机虚拟环境调整）：

```bash
DEEPEVAL_TELEMETRY_OPT_OUT=YES HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
SILICON_NOTEBOOK_PROJECT_ROOT=/absolute/path/to/silicon-notebook/project \
/path/to/venv/bin/python - <<'PYTEST'
from pathlib import Path
import sys, pytest
sys.path.insert(0, str(Path.cwd() / "src"))
blocked = []
def guard(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        blocked.append(event)
        raise RuntimeError("Network blocked")
sys.addaudithook(guard)
code = pytest.main(["-q", "-ra", "--tb=short"])
print("Network attempts:", len(blocked))
raise SystemExit(code or bool(blocked))
PYTEST
```
