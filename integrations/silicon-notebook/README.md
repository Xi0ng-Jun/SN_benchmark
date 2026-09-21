# SN 本地执行轨迹补丁包

本目录把 SN 的观测改动随评测仓库一起交付，**不需要推送 SN 上游仓库**。补丁只修改追踪模块、具名调用边界、测试与 operations 文档；默认关闭、不引入 DeepEval 生产依赖、不改数据库 schema 和部署配置。

完整功能与运行指令见 [SN 执行轨迹说明](../../docs/sn-execution-tracing.md)。`manifest.json` 记录本机基准/实现提交、补丁 SHA256 和文件清单；`SHA256SUMS` 用于传输校验。补丁是 Git format-patch 邮件格式，包含原文件 blob 身份，比复制整份文件更适合保留服务器的本地修复。

## 应用到服务器

先拉取评测代码，核对 SN 的 `git status --short --branch` 和 `git rev-parse HEAD`。本机基准为 `74e9c61e`；服务器不必恰好等于它，但基准不同意味着需要检查补丁上下文与冲突。

以下路径由服务器 Agent 按实际情况填写：

```bash
BENCH_REPO=/path/to/benchmark-deepeval
SN_REPO=/path/to/existing/silicon-notebook
SN_EVAL=/path/to/sn-evaluation-tracing
SN_PYTHON=/path/to/sn-python-environment/bin/python

cd "$BENCH_REPO/integrations/silicon-notebook"
sha256sum -c SHA256SUMS
git -C "$SN_REPO" status --short --branch
git -C "$SN_REPO" rev-parse HEAD
git -C "$SN_REPO" worktree add -b feat/benchmark-execution-tracing "$SN_EVAL" HEAD
git -C "$SN_EVAL" apply --check "$BENCH_REPO/integrations/silicon-notebook/"*.patch
git -C "$SN_EVAL" am --3way "$BENCH_REPO/integrations/silicon-notebook/"*.patch
git -C "$SN_EVAL" status --short
git -C "$SN_EVAL" log -1 --oneline
```

这会在原 SN 当前已提交版本上创建独立 worktree。原生产目录里的未提交修改不会被带过去，也不会被覆盖；如果它们是本轮评测必需修复，先审阅并单独带入评测分支，不要盲目 stash、reset 或覆盖生产文件。评测仓库中服务器已有的 model-config/并发修复同样需要保留。

已有目标分支/worktree 时先检查是否已经应用，避免重复 `am`。`apply --check` 失败时检查差异；版本稍有不同但祖先可用时，可以在这个新 worktree 执行 `am --3way`。若发生冲突，由 Agent 按原生方法签名保留两侧改动后 `git am --continue`；无法正确合并就 `git am --abort` 并说明具体冲突，禁止以整文件覆盖绕过。

应用后必须形成干净提交再运行：评测的 `snapshot_sources` 拒绝未提交的 SN tracked 变更，并保存实际 `HEAD` 和源码归档。服务器三方合并后的提交 SHA 可能不同于 manifest 中的本机实现 SHA，这是正常的，实验身份应使用服务器实际提交。

新 worktree 不会自动复制被 Git 忽略的 `.env`、`.local/model-services.toml` 和依赖目录。按服务器已采用的模型配置注入方式提供这些只读输入，不把配置、密钥或地址提交到补丁/评测仓库，不重启正在服务的生产 SN。

## 验证与实验顺序

应用后先执行 SN 针对性回归：

```bash
cd "$SN_EVAL"
SILICON_NOTEBOOK_ENV_FILE='' MODEL_SERVICES_CONFIG='' \
  PYTHONPATH=backend "$SN_PYTHON" -m pytest \
  backend/tests/test_evaluation_tracing.py -q
PYTHON_BIN="$SN_PYTHON" bash scripts/check.sh
```

`SN_PYTHON` 由服务器设置为其 SN Python 环境；按 SN 的标准 gate 配置独立前端依赖，不写穿共享 node_modules。补丁应用在不同版本且调整过冲突时，这些验证尤其必要。本机验证证据见 [交付记录](../../docs/sn-execution-tracing-validation.md)，不能替代服务器真实模型接口验收。

随后只选一个已冻结的 QMSum 分区，两种模式各用新 run-dir，加 `--request-revision notebook-request-v2 --capture-agent-trace`。查看 `product_record.execution_trace` 的父子关系、阶段与 `capture_errors`，再运行默认离线 Agent 报告。先不要整套重跑，也不生成 Dashboard；确认真实轨迹可用后再显式运行 judge。

## 回退

- 不需要观测时，去掉 `--capture-agent-trace` 即回到原采集路径，SN 装有补丁也默认关闭。
- `git am` 冲突过程中用 `git am --abort` 回到应用前状态。
- 已应用提交的评测分支若需撤销，用 `git revert <服务器实际补丁提交>` 保留历史。原生产目录和生产分支从未切换，运行中的服务不需操作。
- 已生成的实验产物仍绑定原提交，不回填新版本到历史实验。
