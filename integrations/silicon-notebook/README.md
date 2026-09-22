# SN 原生 DeepEval 增量补丁

本目录随评测仓库交付 SN 修改，不向 SN 上游推送，也不覆盖生产目录。当前补丁把之前的自制采集器换成原生 DeepEval；修改观测、业务字段投影、测试和成对 operations 文档。基础生产依赖不变，评测环境增加可选 `deepeval==4.2.2`。

- 本机增量起点：`1b4eb2b3`（旧 `sn-execution-trace-v1` 已应用）。
- 本机实现提交：`052b7373`，分支 `feat/evaluation-tracing`。
- 新协议：`sn-deepeval-native-v1`。准确提交、文件哈希见 `manifest.json`。
- 服务器此前已在新基准上合并旧补丁，实际提交为 `af03de32...`；不要重复应用旧补丁，不要求提交 SHA 与本机一致。

## 应用到独立 worktree

以下路径由服务器填写。起点应是**已经含旧观测补丁及服务器必要修复的评测分支**，不能直接从不含旧补丁的生产 HEAD 应用此增量。

```bash
BENCH_REPO=/path/to/SN_benchmark
SN_WITH_OLD_TRACE=/path/to/sn-eval-tracing
SN_NATIVE=/path/to/sn-native-evaluation
PATCH="$BENCH_REPO/integrations/silicon-notebook/0001-feat-native-deepeval-evaluation.patch"

cd "$BENCH_REPO/integrations/silicon-notebook"
sha256sum -c SHA256SUMS
git -C "$SN_WITH_OLD_TRACE" status --short --branch
git -C "$SN_WITH_OLD_TRACE" worktree add -b feat/native-deepeval-evaluation "$SN_NATIVE" HEAD
git -C "$SN_NATIVE" apply --check "$PATCH"
git -C "$SN_NATIVE" am --3way "$PATCH"
git -C "$SN_NATIVE" status --short
git -C "$SN_NATIVE" log -1 --oneline
```

`apply --check` 是上下文预检。失败时先查看差异，再在独立 worktree 用 `am --3way`；服务器基准不同可能有合理冲突。保留服务器的模型路由、RetrievalControlError、全局问答和并发修复，按业务签名合并后 `git am --continue`。无法确认正确性则 `git am --abort` 并报告具体冲突。不要用整文件覆盖，不要 reset/stash 用户改动。应用后形成干净提交；运行身份记录服务器实际合并 SHA。

新 worktree 不带被忽略的配置和依赖。使用独立评测 Python 环境，具有 SN backend 所需依赖及 DeepEval 4.2.2；不要向生产服务使用的虚拟环境安装新依赖。模型 TOML 用 `--model-config` 注入隔离 runtime，judge JSON 用 `--judge-config`，凭据继续使用服务器已有环境变量。

## 必要检查

从 benchmark 根目录运行跨仓库离线契约检查，使用真实 SN 观测模块、真实 SDK 和本地替身：

```bash
DEEPEVAL_TELEMETRY_OPT_OUT=YES DEEPEVAL_DISABLE_DOTENV=1 CONFIDENT_TRACE_FLUSH=0 \
  SN_EVALUATION_SOURCE="$SN_NATIVE" PYTHONPATH=.:src \
  "$EVAL_PYTHON" -m pytest integrations/silicon-notebook/test_native_agent_contract.py -q
```

`EVAL_PYTHON` 是服务器独立评测环境 Python。SN 针对性测试为 `backend/tests/test_evaluation_tracing.py` 与 `backend/tests/test_evaluation_tracing_integration.py`；按 SN 仓库约定执行标准 gate，尤其是解决过合并冲突时。已知本机 `dotenv` 打包环境失败见[验证记录](../../docs/sn-execution-tracing-validation.md)，不应以此修改无关业务。

这份增量不能直接用于全新、没有旧埋点的 SN。若是全新克隆，应先从评测仓库 Git 历史取得上一版补丁并应用，再应用本补丁；当前服务器已有旧补丁，无需此步骤。本包不维持两套运行实现。

## 实验与回退

当前命令和工件说明见[原生评测协议](../../docs/native-agent-evaluation.md)，完整[服务器 prompt](../../docs/server-agent-tracing-prompt.md)可直接转交。先最大 QMSum 题两模式，再 meeting18 六题两模式，不自动跑全量或 Dashboard。

不用 Agent 评分时运行普通 Notebook 命令；SN 补丁默认不启用观测。`git am` 冲突可 abort；应用后的评测分支可 revert 服务器实际提交。生产目录和服务不需要切换。旧答案/客观分保留，旧 Agent JSON 归档，不迁移或混入新主结果。
