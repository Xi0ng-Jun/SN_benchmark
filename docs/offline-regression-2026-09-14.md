# 公开评测离线回归记录

> 历史验证记录：数字只适用于当时分支和输入，不能覆盖合并后的当前回归。


日期：2026-09-14。用户授权先执行离线回归；在线评测仍暂停。

## 结果

最终执行 `tests/` 全部测试：**205 passed in 3.61s**，退出码 0，无失败、跳过或警告。测试进程通过 Python audit hook 阻断 socket 连接、域名解析和 UDP 发送，记录到的网络请求尝试为 0。

测试对象为 `docs/public-benchmark-agent-expansion` 工作树，基于已推送提交 `63214c7`，加本轮测试改动；本轮未修改 `src/` 或 SN 生产代码。Python 3.13.15、pytest 9.1.1、DeepEval 4.2.2。使用评测仓库主检出的已有 `.venv`，通过 `src` 路径与断言保证加载的是当前工作树源码，不使用主检出的评测源码。

| 检查范围 | 实际验证内容 | 限制 |
|---|---|---|
| 七套新系统资料适配 | 白名单字段、标签隔离、选项顺序、原题保持、资料数量限制 | 使用合成测试行，没有下载或冻结正式公开样本 |
| 六套客观答案评分 | 本地 prepare/load → 系统资料适配 → 真实 DeepEval schema/模板/exact-match；正确和错误答案均检查 | 输入为预先写好的答案文本，没有 SN 生成 |
| IFEval | 真实 verifier 的正反例、缺审核 N/A、参数/源码身份匹配、审核重放、完整正文保留 | 合成规则测试不等于真实数据规则审核或人工校准 |
| 执行与报告 | 澄清不进入 Ask、独立会话、持久化检查、保留引用、上下文采集失败处理、分母、解析覆盖率和产物防篡改 | SN 仓库/响应与模型使用测试替身，没有实际启动服务 |
| 原三套产品路径 | SQuAD/DROP/BoolQ 待审样本不进入执行；合成审核样本生成资料；DROP 缺完整注释时 N/A；BoolQ 解析 | 合成审核仅用于测试，不写入真实人审记录 |
| Native 与历史兼容 | SDK 请求/答案/来源身份、旧报告、legacy N/A；DROP/BoolQ 真实客观 scorer；SQuAD 无 judge 时拒绝评分 | 未调用 SQuAD judge，未产生语义质量分数 |
| SN judge JSON 契约 | 使用生产侧 `model_json.py` 的纯校验函数与假客户端检查 schema 转换 | 只读加载校验代码，不创建仓库、数据库或模型服务 |

## 发现与处理

1. 工作树自己的 `.venv` 缺少 pytest，首次命令未进入测试。改用评测仓库已经安装依赖的解释器，无需安装或下载。
2. 新系统适配首轮 68 项通过；随后全量为 194 项通过、1 项跳过、1 条警告。
3. 跳过来自原生 judge 测试按普通检出目录推算 SN 位置，worktree 路径不满足这个假设。测试现支持 `SILICON_NOTEBOOK_PROJECT_ROOT`；显式配置无效路径时失败，未配置且缺少同级产品时仍允许跳过。指定实际产品根目录后该测试通过。
4. 测试正则中的 `\.` 改为 raw string，消除 Python 转义警告。
5. 补入 10 项集成检查：六套系统客观评分、IFEval 真实规则、三套 legacy 产品适配。它们弥补了此前系统评分主要使用 SDK 替身、原三套适配缺少直接测试的缺口。
6. 补充检查与修改后的定向回归 56 项通过；最终完整回归 205 项通过。没有因测试失败而修改评测业务实现。

## 复现

从被测工作树根目录执行；以下是本机本轮使用的解释器和产品路径。`SILICON_NOTEBOOK_PROJECT_ROOT` 只供离线契约测试定位纯校验模块，不启动 SN。

```bash
DEEPEVAL_TELEMETRY_OPT_OUT=YES HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
SILICON_NOTEBOOK_PROJECT_ROOT=/home/wabiwabi/silicon-notebook/project \
/home/wabiwabi/silicon-notebook/benchmark-deepeval/.venv/bin/python - <<'PY'
from pathlib import Path
import sys
import pytest

root = Path.cwd()
sys.path.insert(0, str(root / "src"))
import rag_eval
assert Path(rag_eval.__file__).resolve().is_relative_to(root / "src")
blocked = []
def offline_guard(event, args):
    if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        blocked.append(event)
        raise RuntimeError("Network access blocked during offline regression")
sys.addaudithook(offline_guard)
code = pytest.main(["-q", "-ra", "tests"])
print("Offline network attempts blocked:", len(blocked))
raise SystemExit(code or bool(blocked))
PY
```

本轮额外保存了 JUnit 记录与执行环境信息：工作树下 `var/offline-regression/20260914/initial.xml`、`final.xml`、`execution.json`。这些本地工件不随 Git 上传；本页保存可共享的结果、范围和复现方式。

## 后续边界

离线测试通过表明已覆盖的代码契约在上述环境中符合预期，不代表真实 SN 导入/检索/Ask 已验收，也不代表产品质量或发布门禁通过。下一步为每套选择少量正式公开题并冻结身份，审核 IFEval 实际规则，再在用户恢复在线任务后验证 chunk/reasoning。Agent/DAG、完整轨迹、多轮 Memory 仍未接入；旧 baseline、timer、生产配置均未恢复或修改。