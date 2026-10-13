# 服务器工作台接入交接（2026-10-14）

目标：在已有 SN 的实验服务器上运行本仓库当前工作台，完成“选题 → 评测 → 分析 → 批准假设 → 独立 SN worktree 修改 → 同计划对照”的操作入口。仓库：`git@github.com:Xi0ng-Jun/SN_benchmark.git`；分支：`feat/benchmark-protocol-correctness`。**交接消息给出的完整 SHA 是本次目标版本**，不要使用旧协议 SHA 或随时间变化的 branch HEAD 代替。后续修订必须另外记录。

当前授权是代码同步、安装、配置、离线验收和启动工作台。服务器 Agent 可完成这些操作；新的真实模型运行仍需用户给出题单和预算。不要自动延续本机已结束的三题实验。证据排序候选未采纳，也未包含在这次 SN 生产代码交付中。

本机交付检查结果与限制见[验证记录](server-workbench-verification-2026-10-14.md)，不等于远端验收。

## 给服务器 Agent 的任务

先读 `AGENTS.md`、`CURRENT_STATE.md`、`docs/evaluation-context.md`、`docs/evaluation-status.md`，再执行本文。实际服务器目录、Paseo 版本、workspace ID、模型 profile、凭据与可达性由你在服务器核验；本机未连接服务器，不能把历史路径或进程号当成现状。

1. 盘点现有 SN、评测 checkout、运行中的实验和待提交修改。通过 GitHub 在**新目录**取得交接 SHA；保留旧 checkout、进程、结果和生产配置。
2. 建独立评测环境，构建 Web 与 Agent bridge，生成本机私有配置。先通过无模型启动验收。
3. 核验并登记服务器已有 frozen bundle/scorer、SN Git 源码、Paseo workspace、pi profile 和独立实验模型配置。准备全新基线 worktree，完成本文离线接线检查。
4. 启动只监听 loopback 的工作台，给用户 SSH 隧道或 Paseo 代理入口。回传版本、验证结果、当前能做的操作和剩余缺项。**停在首个付费运行之前**；不以旧预算作为新服务器运行授权。

## 1. 取得精确版本

以下变量由服务器实际环境填写，路径不得指向正在运行服务的工作目录。`DELIVERY_SHA` 从交接消息复制。

```bash
export DELIVERY_SHA='<交接消息中的完整 SHA>'
export EVAL_ROOT='/absolute/new/sn-benchmark-workbench'
git clone --branch feat/benchmark-protocol-correctness \
  git@github.com:Xi0ng-Jun/SN_benchmark.git "$EVAL_ROOT"
cd "$EVAL_ROOT"
git merge-base --is-ancestor "$DELIVERY_SHA" origin/feat/benchmark-protocol-correctness
git switch --detach "$DELIVERY_SHA"
git rev-parse HEAD
git status --short
```

也可以从已有评测仓库 fetch 后 `git worktree add --detach` 到新目录。不要对服务器已有修改执行 reset/clean。后台 Python 已加载的代码不会随 git pull 更新；新工作台使用新进程和新 state。

## 2. 独立环境和前端构建

Linux、Python >=3.11、Node >=22.18、npm、Git。新环境不要用 `--system-site-packages`；不要复制本机 `.venv`。先确认所选 SN 对 Python 的实际要求。下列命令从 evaluator 根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[workbench,dev,notebook]'
.venv/bin/python -m pip check
npm --prefix apps/sn-agent-bridge ci --ignore-scripts --no-audit --no-fund
npm --prefix apps/sn-agent-bridge run build
npm --prefix apps/sn-workbench ci --no-audit --no-fund
npm --prefix apps/sn-workbench run typecheck
npm --prefix apps/sn-workbench run build
```

两个 npm lock 均随 Git 提交。Python 环境在安装 SN 依赖之后还须按第 5 节冻结；启动 Web 的依赖齐全不等于 SN/官方 scorer 全部可执行。不升级或安装包到生产 SN venv。项目是 **source checkout + editable install** 部署；单独 wheel 不包含 `apps/` 和 runner scripts。

若数据和 Paseo 尚未定位，可先用空配置检查安装：

```bash
.venv/bin/python scripts/configure_workbench_server.py init \
  --config var/setup-smoke/config.json
.venv/bin/python scripts/configure_workbench_server.py check \
  --config var/setup-smoke/config.json
SN_WORKBENCH_CONFIG=var/setup-smoke/config.json \
SN_WORKBENCH_STATE=var/setup-smoke/state bash scripts/serve_workbench.sh
```

该模式可以创建任务，但没有选题、Agent 创建和执行配置。验收后正常 Ctrl-C，只停止该前台工作台进程。

## 3. 数据和独立源码目录

历史交付见 `RUNBOOK.md` §2.4：服务器曾接收 `sn-benchmark-assets-v1.tar.gz` / `sn-python-metadata-v1.tar.gz` 与校验清单。先定位实际目录并核验 SHA256，不重新打包整个本机 `var/`。如果缺包，按 `RUNBOOK.md` §3.2 和官方资源文档从合法原始数据重建 bundle；不可伪造来源/hash，或用本机 smoke 子集冒充完整 benchmark。

本轮先接通 **QASPER chunk**（本机有实际闭环证据）。其他四套可按原协议登记；它们各自的运行和官方依赖验收另行报告，尤其 QMSum Perl ROUGE、ALCE 模型评分，不能从 Web 启动成功推断可用。

`SN_ROOT` 应是服务器现有 SN Git 仓库的**独立评测 clone**，由服务器 Agent 按生产 checkout 的已提交 SHA 检出。不得将生产原树登记为实现 Agent 的编辑目录。记录 SN remote、SHA、dirty 状态及是否与当前生产版本一致；不要带入本机未采纳的排序候选。本机已验证闭环的 SN 基线为 `0680aa12313a5d3e67f4e0a4be94144a5b6af28c`，服务器版本不同则重新检查 captured runner 兼容性，不自动替换生产版本。runner 直接加载捕获的源码，无需 SN HTTP API。模型服务可能与已有 SN 共用，但数据库、存储、日志、配置和预算必须独立。

## 4. Paseo 和可信注册

Paseo daemon 与工作台需运行在**同一服务器、同一可见文件系统及有访问权限的用户下**。bridge 会 realpath 核对目录，不支持只连另一台机器的 daemon 却使用本机路径。SDK 锁为 `@getpaseo/client@0.11.2`；服务器 daemon 的创建/worktree 能力需实际核验，不擅自升级其生产实例。

```bash
export SN_ROOT='/absolute/isolated/sn-eval-source'
export QASPER_BUNDLE='/absolute/assets/bundles/qasper-v3'
paseo workspace create --isolation local --path "$EVAL_ROOT" --json
# 从返回值取得评测仓库 workspace ID，不是 SN workspace ID。
export EVAL_WORKSPACE_ID='<服务器实际返回的 workspaceId>'
.venv/bin/python scripts/configure_workbench_server.py init \
  --sn-root "$SN_ROOT" --workspace-id "$EVAL_WORKSPACE_ID" \
  --bundle "qasper-input=$QASPER_BUNDLE"
```

默认输出 `var/sn-workbench-server/config.json`，权限 0600。初始化拒绝覆盖已有配置；重新配置用新文件，或由 Agent 显式修改已有私有 JSON。`--bundle ID=PATH` 可重复；它调用既有 loader 全校验并安装不可变 bundle/index，登记真正的完整 ref，不复用本机 hash。不会调用模型。所有路径写入服务器绝对路径；手工配置的相对路径相对 **config.json 所在目录**，不是 shell cwd。

在私有配置中补齐：

- `agent_bridge.url`：服务器本地 daemon 的实际 `/ws` 地址；需要密码时只写入本机 0600 配置的 `agent_bridge.password`，不进 Git、不输出到报告。
- `agent_creation.profiles`：通过服务器 Paseo 的 profiles/providers/models 查询实际 pi 配置，按照 `{ "pi-profile-name": { "provider": "pi/实际-provider/实际-model", "thinkingOptionId": "实际支持值" } }` 填写。只写支持字段，可省略 thinkingOptionId。初始化保留空 profiles，避免复制本机模型假设；真实测试用 pi，不用 Claude。
- 初次基线需一个独立 worktree：从已核实 SN SHA 创建，并记录返回的 directory 和 workspaceId。

```bash
export SN_BASE_SHA='<核实的 SN 已提交 SHA>'
paseo workspace create --isolation worktree --path "$SN_ROOT" \
  --mode branch-off --new-branch eval/server-baseline \
  --base "$SN_BASE_SHA" --worktree-slug eval-server-baseline --json
```

将返回值写到 `task_worktrees.registrations`：

```json
[{"id":"server-baseline","path":"/actual/returned/worktree","paseo_workspace_id":"actual-returned-id"}]
```

在任务“更改”页选择该基线目录。首次运行不能省略可信绑定/捕获；之后批准假设才通过卡片新建独立实现目录。工作台配置 `project_root` 指向独立 clone，registration 指向其 linked worktree；不要混淆。

## 5. 配置实际 runner（仍不执行模型）

在 evaluator 的 `.venv` 安装**所选 SN SHA** 的 backend requirements 和本仓库 `.[workbench,dev,notebook,deepeval]`，保留安装日志，执行 pip check。若需另一解释器，单独建新环境，并在该环境 editable 安装同一 evaluator checkout。不要继承生产环境的 `PYTHONPATH`、数据库、存储或任意 `.env`。

建立 `var/sn-workbench-server/private/`，目录 0700：

- `model-services.experiment.toml`：从实际 SN 支持的 schema 制作独立实验配置，明确 chat/embedding/rerank 和调用价格、超时、重试；检查全部 workload bindings。只按需要引用模型凭据环境变量，在工作台进程环境注入，不把凭据放进 Git。server-only `model_names` 是展示标签，必须与真实配置一致。
- `dependency-requirements.txt`：所选 SN 的实际声明（普通 PEP508 行；不支持 `-r`、editable、URL）。若原始声明含这些项，显式形成并审查普通声明清单；不要删掉不满足的依赖来通过检查。另保存 `pip freeze` 作为实际环境快照，两个文件职责不同。
- `runtime-expectation.json`：用 **job_execution.python 对应解释器**生成，不能复制本机 executable/prefix。

```bash
mkdir -p var/sn-workbench-server/private
chmod 700 var/sn-workbench-server/private
umask 077
cp "$SN_ROOT/backend/requirements.txt" var/sn-workbench-server/private/dependency-requirements.txt
.venv/bin/python -c 'import json,sys; print(json.dumps(dict(executable=sys.executable,prefix=sys.prefix)))' \
  > var/sn-workbench-server/private/runtime-expectation.json
.venv/bin/python -m pip freeze > var/sn-workbench-server/private/installed-freeze.txt
```

从 `configs/sn-workbench-execution.example.json` 复制 **job_execution 字段** 到生成的私有 config.json，将所有 `/absolute/...` 换为实际路径；不要覆盖已经生成的 inputs/ref。`artifact_root` 必须与 `task_worktrees` / `job_preparation` 完全相同。解释器路径保留 `.venv/bin/python`，不要 resolve 到系统 Python。

官方来源优先复用已核验服务器目录。缺失时仅获取固定公开脚本，不涉及模型调用：

```bash
.venv/bin/python scripts/benchmark_protocol.py fetch-sources \
  --output var/sn-workbench-server/scorers
```

已有目录内文件必须符合 `src/rag_eval/benchmark_official.py` 的固定 SHA；不通过时保留旧目录，新建目标查原因。使用下面的只读检查（把目录改为 `job_execution.scorer_sources` 的实际值）；`check --execution` 本身只检查 scorer 目录存在，不替代此项。新 scorer 文件不说明 Perl/权重等外部依赖齐备。

```bash
export SCORER_SOURCES='/absolute/verified/scorers'
.venv/bin/python - <<'PY'
import hashlib, os
from pathlib import Path
from rag_eval.benchmark_official import SOURCES
root = Path(os.environ['SCORER_SOURCES'])
for name, expected in SOURCES.items():
    if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected['sha256']:
        raise SystemExit('Scorer hash mismatch: ' + name)
print('Fixed scorer sources verified:', len(SOURCES))
PY
```

```bash
.venv/bin/python scripts/configure_workbench_server.py check \
  --config var/sn-workbench-server/config.json --execution
.venv/bin/python -m pytest -q tests/test_workbench_server_setup.py \
  tests/test_workbench_configuration.py tests/test_workbench_notebook_job.py \
  tests/test_workbench_job_worktree_provenance.py tests/test_hypothesis_worktrees.py
npm --prefix apps/sn-agent-bridge run typecheck
npm --prefix apps/sn-agent-bridge test
```

`check --execution` 只校验配置/工件、路径隔离、解释器身份和声明语法；不连接 Paseo、不检验密钥/模型可达性、不声称实际 SN import、官方评分或预算计价已通过。运行前仍由现有 captured runner 作启动核验。成本上限是 SN 账本估算及调用前准入，不是 provider 账单的强制封顶，也不包含 pi Agent 费用。

## 6. 启动与验收

```bash
bash scripts/serve_workbench.sh
# 或已由 Paseo 管理该 evaluator workspace 时：
paseo script start workbench --cwd "$EVAL_ROOT"
paseo script ls --cwd "$EVAL_ROOT"
```

二选一启动，不启动重复实例。服务默认 `127.0.0.1:8091`；Paseo 自动分配 `PASEO_PORT` 时用返回代理地址。手动可设 `SN_WORKBENCH_PORT`；配置/state/解释器可用 `SN_WORKBENCH_CONFIG` / `SN_WORKBENCH_STATE` / `SN_WORKBENCH_PYTHON` 显式指定。旧本机配置若存在，启动脚本兼容原本机 state；**新 clone 不含该配置**，默认使用 server 配置/state。

`scripts/sn-eval` 采用相同的本机/服务器默认路径；可只读执行 `scripts/sn-eval --task <服务器任务ID> task` 验证 Agent 查数入口。使用非默认配置时，Paseo Agent 进程也必须具备同样的 `SN_WORKBENCH_CONFIG` / `SN_WORKBENCH_PYTHON`，或在工具命令显式追加 `--config /absolute/config.json`；仅在启动 Web 的终端设置环境变量，不会自动传给已有 daemon 下的新 Agent。角色 brief 已携带该任务实际 state。

从自己电脑连手动启动的服务：

```bash
ssh -N -L 8091:127.0.0.1:8091 <user>@<server>
```

浏览器打开 `http://127.0.0.1:8091/`。访问码在服务器 `var/sn-workbench-server/state/access-token`，仅由用户在本机终端读取并输入 UI；不要提交、贴进公共报告或带 token 截图。不要简单改成 `0.0.0.0` 暴露服务。

验收：页面加载 → 输入访问码 → 新建任务 → 可读取 QASPER 题单 → 可选择基线 worktree → 可保存固定计划和捕获准备。Agent providers/models 的目录读取可验证 bridge 连接但不会产生对话费用。真正创建并发送 Agent 消息、点击确认预算运行，都另行取得用户的当前预算与题单授权。

不要搬运本机 SQLite/task IDs/workspace IDs/执行绝对路径来伪造服务器历史。旧历史如需阅读，另做只读结果导入；不是复制整个 state 后改字符串。本机实验报告可见 `docs/experiments/2026-10-14-r1-ordering-loop.md`。

## 回传格式与停止位置

在服务器私有运行目录保存 `setup-report.md`，回传脱敏摘要：evaluator SHA/import 路径、SN SHA、Python/Node/Paseo 版本、依赖检查/测试/构建结果、输入 bundle ID、scorer 校验、服务访问方式、baseline workspace 绑定、各能力 ready/pending。不得回传凭据、完整配置或私有源资料。

说明未做真实生成与端到端官方评分；用户确认新题单和预算后才进入第一轮服务器 smoke，失败保存原工件且不自动重跑。若接入被版本/依赖/数据缺失阻塞，报告确切层次，保留已完成的离线验收，不能通过省略校验或改分母推进。
