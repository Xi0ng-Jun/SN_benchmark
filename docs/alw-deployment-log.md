# ALW 服务端部署实录、全链路验证与排障手册（Phase 0.0 $\to$ Phase 1c）

> **文档说明与保密约定**（遵循 [AGENTS.md](../AGENTS.md)）：
> - 本文记录 `ssap`（分支 `feat/ssap`，提交 `ae14d9f`）与 `AnalogLayoutWorld`（提交 `d5236c8`）在目标评测服务器上从零完成 **Phase 0.0（环境补齐）$\to$ Phase 0.4（零模型费真容器验证）$\to$ Phase 1a（`smoke10m-run1` 10 分钟带电全链路烟雾）$\to$ Phase 1b（`phase1b-3tasks` 3 题 × 3h `H_base` 实测与 pre-Phase-1c 锁定修复，§4）** 的完整实战过程与 6 个真实故障的标准解法。
> - **严格脱敏**：文中所有宿主机名、账号、内网组名、镜像源及网关/代理地址均使用占位符 `<USER>`、`<HOST>`、`<INTERNAL-GROUP>`、`<INTERNAL-MIRROR>`、`<GATEWAY_URL>`，零真实用户名、零密钥、零内网 IP（仅保留标准 Docker 网段与公开 `ghcr.io` 报错原话中的公网端点）。
> - `Phase 1a`（`smoke10m-run1`）实测数据均逐字段核对自 `docs/server-info/{launch_results.json,artifact_summary.json,metrics.json}`。
> - `Phase 1b`（`phase1b-3tasks`，3 题 × 3h `H_base`，见 §4）实测数据由脚本逐字段抽取自 `docs/server-info/out.json`（`lr` / `asum` / `met`）与 `docs/server-info/forensic.json`（`fork` / `trace` / `err` / `hand` / `lvs` / `gds`）；`docs/server-info/` 为服务端原始导出，已 gitignore，不入库。
> - `Phase 1c`（`phase1c-dev-hbase`，12 题 × 2 种子 `H_base`，见 §5）实测数据由脚本逐字段抽取自 `docs/server-info/phase1c_sync.json`（`harvest` / `runs` / `diag`），题号、`runtime_profile`、`q_max` 与盲簇划分取自 `configs/alw/{catalog.json,split.json}`；3 个内部冻结盲簇只出现聚合计数。
> - 标注约定：`[事实]` = 从上述 JSON 直接读出或纯算术派生；`[判断]` = 根因解释或设计取舍；`[未核验]` = 未能在 JSON 中核对的数字。

---

## 1. 部署与验证全流程总览（Phase 0.0 $\to$ Phase 1a）

### Step 0：宿主机基线、并发红线与网关探活

1. **宿主机资源与存储规划**：
   - 宿主机 `<HOST>`（`Linux 6.8.0-41-generic x86_64`，Xeon Platinum 8378A 32 核 / 64 线程，493 GiB 内存，20 用户共享）：实测可用内存 `259 GiB` 且 **`Swap: 0B`**。结合 ALW 单 Trial 硬配额（`6 CPU / 32 GiB`），锁定 **默认并发 `N = 4`（24C / 128 GiB）**、**安全上限 `N <= 6`（36C / 192 GiB）**，严禁 `N = 8`。
   - 数据盘 `/localdata` 拥有 `1007G` 空闲空间（仅用 1%），将 `$ALW_REPO` 与 `$SSAP_STATE` 全部置于 `/localdata/<USER>/...`，Git 工作树保持干净。
2. **网关与 8 槽子模型同质化锁死规范**：
   - `<GATEWAY_URL>` 为双协议网关（`/v1/messages` + `/v1/chat/completions`），`Authorization: Bearer` 与 `x-api-key` 均通。
   - 因宿主机默认环境变量将 Haiku / Sonnet / Opus 分别路由至不同底层模型，`configs/alw/config.server.json` 与 `harbor_runner.py` 强制执行白名单清理后将 **全部 8 个 Claude Code 模型槽位**（`ANTHROPIC_MODEL`、`ANTHROPIC_SMALL_FAST_MODEL`、`ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS}_MODEL`、`CLAUDE_CODE_{SUBAGENT,AUTO_MODE,BG_CLASSIFIER}_MODEL`）钉死为同一 `$SSAP_MODEL`，并锁定 `DISABLE_NON_ESSENTIAL_MODEL_CALLS=1`、`CLAUDE_CODE_TWO_STAGE_CLASSIFIER=false`、`API_TIMEOUT_MS=3000000`。

### Step 1–2：原生 Docker、双服务 `0600` 代理隔离与 EDA 基础镜像核验

1. **安装拆分包**：在 Ubuntu 24.04 上通过 `sudo apt-get` 安装 `docker.io`、`docker-compose-v2` 与 `docker-buildx`（解决 BuildKit `--secret` 与 compose 依赖），并将 `<USER>` 加入 `docker` 组。
2. **双服务代理隔离**：为 `docker.service` 与 `containerd.service` 配置仅 `root:root 0600` 可读的 `/etc/systemd/system/{docker,containerd}.service.d/proxy.conf`（对含 `%` 的代理密码做 `% -> %%` 转义），并在 `/etc/docker/daemon.json` 写入 `"proxies"`，使守护进程可拉取 `ghcr.io` 而代理凭据永不注入业务容器。
3. **拉取与断网体检**：拉取固定摘要基础镜像 `ghcr.io/nova-silicon/analoglayoutworld@sha256:f922e79ebbac026ed9d15987924867ba6fef4270a5c4994579500e9138bf8338`，在 `--network none` 下跑完 `/opt/analog-layout/build-checks/check_runtime.py`（KLayout `0.30.12`、Magic `8.3.685`、Netgen `1.5.324`、ngspice-46 KLU、SKY130 `continuous/binned` 库全部通过）。

### Step 3：BuildKit 零残留预烘焙 `ssap-alw-agent:2.1.302`

使用 `container/alw/Dockerfile.agent`，通过 BuildKit 临时 secret（`--mount=type=secret,id=build_proxy`）与 `--network=host` 在基础镜像之上构建 `ssap-alw-agent:2.1.302`：
- 仅在 `RUN` 安装阶段于子 Shell 注入构建代理完成 `npm install -g @anthropic-ai/claude-code@2.1.302` 并清理 npm 缓存，镜像层不保留任何代理凭据或 `ENV http_proxy`；
- 植入透明垫片 `/usr/local/bin/claude`（当挂载 `/opt/harness/bin/mcr_entrypoint.py` 时自动接管流水线与 Phase B；否则直通 `/opt/claude-code/bin/claude`）以及 `/usr/local/bin/{mcr_check,mcr_promote}`；
- 官方 `verifier` 容器及离线 `alw verify` 保持使用未经修改的原始摘要基础镜像。

### Step 4：安装 `ALW` + `ssap` 与 Phase 0.4 零模型费全绿验证

在 `/localdata/$(whoami)/AnalogLayoutWorld/.venv`（显式基于 `/usr/local/pyenv/shims/python3.12` 构建，规避默认 `3.13.14`）安装 `AnalogLayoutWorld`（`d5236c8`）`[harbor]` 与 `ssap`（`feat/ssap` `ae14d9f`），完成 3 项零模型费验收：
1. `ssap alw-check-split --config configs/alw/config.server.json`：`ALL INVARIANTS HOLD`（R1–R5 全过，`dev=12 / val=14 / test=14`，`G_dev=10`，冻结 `3` 盲簇）。
2. `pytest` 单元测试：**`150 passed, 1 skipped`**（`ALW_PAPER_DIR` 未在服务端设时跳过 1 条可选论文文本解析校验）。
3. `ssap alw-concordance --config configs/alw/config.server.json --offline-smoke-container --docker yes`：本地模拟与真实 Docker 容器双双返回 **`"ok": true`**，并在容器内实测 KLayout 触发 **`tier_incomparable` 拦截**（验证棘轮拒绝跨目标倒退覆盖）。

### Step 5：Phase 1a 10 分钟带电烟雾（`smoke10m-run1`）全链路实测

针对 `dev` 最小题 `#21`（`sky130-adb-ota-5t-gain40-pm60-noise50uv-pvt-layout`）以 `timeout=600s`、`T_warn=300s`、`T_soft=390s`（预留 `210s >= 180s` 触发 Phase B）执行 `H_base` 首跑，全程耗时 `12m 18s` 一次跑通：

| 观测维度 | `smoke10m-run1` 实测值（核验自 `docs/server-info/*.json`） | 说明 |
|---|---|---|
| **执行状态与代理通路** | `exit_code = 0`，`proxy_requests = 51`，`retries = 0`，`http_429_count = 0`，`http_5xx_count = 0`，`infra_error = false` | 宿主机 `:8791` 零缓存计量代理直通，容器 `NO_PROXY` 隔离正常 |
| **Token 与模型同质化** | `input_tokens = 2,305,890`，`output_tokens = 43,779`，`cached_tokens = 108,928`；`request_models_seen = ["ExternalAPI-Physical-Design-deepseek-v4-1-flash-volcengine-public"]` | `upstream_models_seen = ["deepseek-v4-1-flash-261017"]`（对应网关渠道全名映射到固定后端版本快照，全部 51 次请求同质无漂移） |
| **网关与容器资源** | `gateway_wait_seconds = 150.145883s`，`llm_stream_seconds = 252.51181s`，容器 `wall_sec = 446.649s`，`tool_cpu_sec = 84.444s`，`peak_ram_mb = 269.1 MB` | 按容器墙钟计 `150.15 / 446.65 = 0.336 <= 0.35`（按外层 `12m 18s` 计为 `0.203`），未触发 `gateway_stall`，但短窗下已较接近 `0.35`，正式长跑可据此关注并发排队占比；短超时下内存仅 `269.1 MB` |
| **双水位线与 Phase B** | `390s` 触发 Phase A 停机快照；`gate_triggered = true`（`["m_best_not_all_certified"]`），`phase_b_invoked = true`，`repair_ran = true`，`ratchet_promoted = false`（`promote_reason = "no_new_candidate"`） | 10 分钟时间窗内尚未产出草稿 GDS，Phase B 无状态 `claude -p` 正常拉起并在预算内安全收尾 |
| **独立断网 Verifier** | `status = "missing_submission"`，`score = 0.0`，`score_valid = true`，`evaluation_error = false`，`pipeline_completed = true`，`seconds = 0.02` | 完整验证了 Harbor 提取与断网评分三元组语义（无 GDS 时合法计 `0.0` 分而非报错） |

---
## 2. 部署实踩 6 个真实问题、根因与标准解法

### 2.1 速查表

| 编号 | 现象 / 报错特征 | 根本原因 | 标准解法 |
|---|---|---|---|
| **P1** | `docker pull ghcr.io/...` 报 `failed to resolve reference ... dial tcp 20.205.243.164:443: i/o timeout` | Ubuntu 上镜像拉取实际由 `containerd.service` 发起；且代理密码含 `%`/`$` 被 bash/systemd 解析损坏 | 用 Python 原样写入 `/etc/docker/daemon.json` `"proxies"`，并为 `docker` 与 `containerd` 双服务写 `0600` `proxy.conf`（`% -> %%`） |
| **P2** | `DOCKER_BUILDKIT=1 docker build` 报 `buildx component is missing` | Ubuntu 24.04 将 `docker.io` 与 `buildx` 拆分为独立软件包 | `sudo apt-get install -y docker-buildx` |
| **P3** | 镜像构建内 `npm install` 报 `SELF_SIGNED_CERT_IN_CHAIN` | 企业代理自签证书下，`npm` 默认 `strict-ssl=true` 覆盖了 `NODE_TLS_REJECT_UNAUTHORIZED=0`，且 `NODE_USE_ENV_PROXY=1` 冲突 | 在 `Dockerfile.agent` 的 BuildKit secret 分支内 `unset NODE_USE_ENV_PROXY`、设 `npm_config_strict_ssl=false` + `--strict-ssl=false` 并加 `--network=host` |
| **P4** | 虚拟环境路径找不到 & `pyenv: cannot rehash` | 子 Shell `$USER`/路径写死差异 + 系统公共 `/usr/local/pyenv/shims` 只读无写权限 | 用 `$(whoami)` 与 `python -c "import analoglayoutworld..."` 动态定位 `ALW_REPO`；直连 `.venv/bin` 执行，~/.local/bin 正常可用 |
| **P5** | `Phase 0.4` 容器烟雾返回 `rc: 1`（`docker.sock: permission denied`） | VS Code Remote 常驻 `vscode-server` 继承了加组前的旧进程组列表，未刷新 `docker` 附加组 | 当前终端立即用 `newgrp docker` 生效；永久修复需在 VS Code 触发 `Kill VS Code Server on Host` 后重连 |
| **P6** | 裸名 `deepseek-v4-1-flash` 预检返回 `HTTP 503` 及 `LiteLLM` 远程单价表告警 | 内部网关要求带渠道前缀的完整模型 ID；`LiteLLM` 默认尝试无代理外连拉取单价表 | 先经 `/v1/models` + `preflight_alw.sh` 锁死全名 `ExternalAPI-Physical-Design-deepseek-v4-1-flash-volcengine-public`；设 `HARBOR_TELEMETRY=0 LITELLM_LOCAL_MODEL_COST_MAP=True` |

### 2.2 逐项根因与实操详解

1. **P1：`docker pull ghcr.io/...` 超时（`dial tcp 20.205.243.164:443: i/o timeout`）**
   - **根因**：仅配 `docker.service` 代理对使用 containerd image store 的新版本无效，实际拉取连接由 `containerd.service` 发出；此外若代理密码含 `%` 或 `$`，直接用 `cat <<EOF` 或写 systemd `Environment=` 会触发 bash 变量展开或 systemd `%` 占位符解析错误（例如 `%U` 等）。
   - **标准解法**：用 Python 脚本以字面字符串写入 `/etc/docker/daemon.json` 的 `"proxies"`（`http-proxy`/`https-proxy`/`no-proxy`），同时为 `/etc/systemd/system/{docker,containerd}.service.d/proxy.conf`（`root:root 0600`）写入将 `%` 转义为 `%%` 的 `Environment=` 行，最后 `sudo systemctl daemon-reload && sudo systemctl restart containerd docker`。

2. **P2：`DOCKER_BUILDKIT=1 docker build` 报 `buildx component is missing`**
   - **根因**：`Dockerfile.agent` 使用 `# syntax=docker/dockerfile:1.7` 与 `--mount=type=secret`，必须启用 BuildKit；而 Ubuntu 24.04 的 `docker.io` 包默认不含 `buildx`。
   - **标准解法**：补装 `sudo apt-get install -y docker-buildx docker-compose-v2`，再运行 `docker buildx version` 确认就绪。

3. **P3：BuildKit 镜像内 `npm install` 报 `SELF_SIGNED_CERT_IN_CHAIN`**
   - **根因**：在企业 HTTPS 拦截代理下，仅设 `NODE_TLS_REJECT_UNAUTHORIZED=0` 仍会被 `npm` 自身的 `strict-ssl=true` 拦截；同时外部传入的 `NODE_USE_ENV_PROXY=1` 与 BuildKit 临时网桥环境存在代理分流冲突。
   - **标准解法**：在 `container/alw/Dockerfile.agent` 的 `--mount=type=secret,id=build_proxy` 分支内执行 `unset NODE_USE_ENV_PROXY`、`export npm_config_strict_ssl=false NODE_TLS_REJECT_UNAUTHORIZED=0` 并为 `npm install` 加 `--strict-ssl=false`，构建命令加 `--network=host`。代理凭据仅存在于临时挂载文件，零写入镜像层。

4. **P4：子 Shell 虚拟环境路径错误与 `pyenv: cannot rehash`**
   - **根因**：不同终端会话对工作目录假设不同易写死旧路径；同时共享 `/usr/local/pyenv/shims` 属系统只读目录，执行 `pip install` 后触发 `pyenv rehash` 会报 `cannot rehash`（无害但易干扰脚本判断），且用户自定义工具装于 `~/.local/bin`。
   - **标准解法**：脚本统一用 `$(whoami)` 构造 `/localdata/$(whoami)/...` 并用 Python `import analoglayoutworld` 反查 `ALW_REPO` 根路径；直接使用 `/usr/local/pyenv/shims/python3.12 -m venv .venv` 激活虚拟环境，通过 `.venv/bin/{python,pytest,ssap,alw,harbor}` 调用，无需改动系统只读 pyenv。

5. **P5：`Phase 0.4` 容器烟雾 `rc: 1`（`docker.sock` `permission denied`）**
   - **根因**：`sudo usermod -aG docker <USER>` 修改 `/etc/group` 后，宿主机上常驻的 VS Code Remote 后台进程（`vscode-server`）仍持有旧凭据令牌，所有从它派生的集成终端都缺少 `docker` 附加组。
   - **标准解法**：当前窗口可立即执行 `newgrp docker` 临时获取 `docker` 组并跑通 `alw-concordance --docker yes`；要让之后所有新终端永久生效，在 VS Code 命令面板执行 `Remote-SSH: Kill VS Code Server on Host` 后重连（或彻底重新 SSH 登录）。

6. **P6：裸模型名 `HTTP 503` 与 `LiteLLM` 远程单价表外连告警**
   - **根因**：内部网关 `<GATEWAY_URL>` 对外暴露的路由模型名带渠道前缀（如 `ExternalAPI-Physical-Design-deepseek-v4-1-flash-volcengine-public`），而非裸缩写 `deepseek-v4-1-flash`；`preflight_alw.sh` 的 2 秒 8-token 实测探针提前将其拦在 Phase 0 门外。此外 Harbor 内部调用的 `LiteLLM` 在未配外网代理的宿主机控制环境会尝试联网拉取单价表并打印告警。
   - **标准解法**：先向 `<GATEWAY_URL>/v1/models` 查询精确完整模型 ID 并通过 `preflight_alw.sh` 实测 `POST /v1/messages` 返回 `HTTP 200`；在运行环境固定注入 `export HARBOR_TELEMETRY=0 LITELLM_LOCAL_MODEL_COST_MAP=True`（若使用国内 PyPI/npm 镜像，可按 `<INTERNAL-MIRROR>` 占位配置构建期索引）。

---

## 3. 复用检查清单（换新服务器或重开会话 5 项必查）

1. **会话组与虚拟环境**：执行 `groups` 确认含 `docker`（VS Code 旧会话先 `newgrp docker` 或重启 Server）；确认当前激活的是 `/localdata/$(whoami)/AnalogLayoutWorld/.venv` 且 `python --version` 为 `3.12.x`，`$SSAP_STATE` 位于 `/localdata/$(whoami)/...`，`Swap: 0B` 宿主机保持 `--parallel 4`（上限 `6`）。
2. **双服务代理与 Buildx**：确认 `/etc/docker/daemon.json` `"proxies"` 与 `/etc/systemd/system/{docker,containerd}.service.d/proxy.conf`（`chmod 0600`、`% -> %%`）均已生效；`docker buildx version` 与 `docker compose version` 正常；`ssap-alw-agent:2.1.302` 与原始固定摘要 EDA 镜像均已就位。
3. **网关模型全名与 8 槽同质化**：用 `/v1/models` 取回精确渠道前缀全名（如 `ExternalAPI-Physical-Design-...`），跑通 `preflight_alw.sh`（返回 `200` 且核对响应 `"model"` 字段，如 `deepseek-v4-1-flash-261017`）；保持 `HARBOR_TELEMETRY=0 LITELLM_LOCAL_MODEL_COST_MAP=True` 与 `config.server.json` 8 槽锁死。
4. **Phase 0.4 零模型费回归**：任何代码更新或切换新机器后，必跑 `ssap alw-check-split`、`pytest` 及 `ssap alw-concordance --offline-smoke-container --docker yes`，确认双 `"ok": true` 后再放行模型实验。
5. **凭据脱敏与工作树洁净**：确认容器 `NO_PROXY` 含完整回环集合 `host.docker.internal,172.17.0.1,localhost,127.0.0.1,::1,0.0.0.0`（对齐 `configs/alw/config.server.json`），`git status` 中绝不混入 `var/`、`jobs/`、`docs/server-info/` 或含真实 `<USER>`/内网地址/代理密码的文件。

---

## 4. Phase 1b（phase1b-3tasks，3 题 × 3h H_base）实测与 pre-Phase-1c 锁定修复

### 4.1 运行配置（当时配置，非现行）

- `[事实]` 3 个 `dev` 题各跑 1 次 `H_base`（`seed = 0`，`key_slot = 0`，镜像 `ssap-alw-agent:2.1.302`，请求模型 `ExternalAPI-Physical-Design-deepseek-v4-1-flash-volcengine-public`，上游回报 `deepseek-v4-1-flash-261017`）；Harbor job config 位于 `/localdata/<USER>/ssap-alw-state/jobs/phase1b-3tasks/configs/`。
- `[事实]` 每个 Trial 的 `fork_meta.watermarks` 与 `artifact_summary.watermarks` 均为 **当时配置** `T_max = 10800s`、`T_warn = 9000s`、`T_soft = 9900s`、`Delta_T_rep = 900s`、`Delta_S_rep = 15` 轮、`min_remaining = 180s`、`tail_buffer = 120s`、`kill_grace = 5s`。本节凡出现 `9000` / `15` 轮，均指 Phase 1b 实际运行值；现行值见 §4.4 修复 (2)。

### 4.2 逐题实测表（脚本抽取）

下表由一段只读 Python 脚本从 `docs/server-info/out.json` 与 `docs/server-info/forensic.json` 逐字段抽取生成（`%` 与比值为脚本算术派生；`gateway_wait / wall` 以容器 `cost_ledger.wall_sec` 为分母）。列名省略了 `sky130-` 前缀与 `-layout` 后缀。

> **事后脱敏说明**：Phase 1b 的 3 题中有 1 题事后属于 `split.json` 冻结的内部盲簇。为遵守盲簇隔离红线，本节已追溯脱敏：原表中该题一列整列删除，正文中它的逐次数值改为聚合表述或注明隐去；3 题合计量（如 §4.3 (a) 的 token 与请求合计）保留。下文以“1 个内部冻结盲簇题（逐题明细按盲簇隔离红线隐去）”指代该题。

| 指标 | `adb-ota-5t-gain40-pm60-noise50uv-pvt` | `adb-buf-ppsf-bs-gain1-sfdr90` |
|---|---|---|
| exit_code (launcher) | `0` | `0` |
| proxy_requests | `226` | `363` |
| input_tokens | `12036810` | `26317116` |
| output_tokens | `1623678` | `1337439` |
| cached_tokens | `2108288` | `3613696` |
| gateway_wait_seconds | `795.0` | `1215.5` |
| llm_stream_seconds | `8760.6` | `7495.9` |
| http_429 / http_5xx / retries | `0 / 0 / 0` | `0 / 0 / 0` |
| unrecovered_gateway_error (errors) | `True (1)` | `True (1)` |
| model_swap | `True` | `True` |
| upstream_models_seen | `deepseek-v4-1-flash-261017` | `deepseek-v4-1-flash-261017` |
| phase_a seconds | `9900.029` | `9734.064` |
| phase_a timed_out / returncode | `True / 143` | `False / 0` |
| phase_a_elapsed_sec | `9900.083` | `9734.118` |
| remaining_sec (at fork) | `899.917` | `1065.882` |
| repair_eligible | `True` | `True` |
| granted_repair_budget_sec / turns | `779.917 / 15` | `900.0 / 15` |
| phase_b returncode / timed_out | `1 / False` | `1 / False` |
| phase_b seconds | `167.831` | `200.844` |
| phase_b used % of budget | `21.5` | `22.3` |
| phase_b unused % of budget | `78.5` | `77.7` |
| best_sha256 changed | `False` | `False` |
| m_pre | `None` | `[1, 0, 1, 0, 0, 1]` |
| m_post | `None` | `[1, 0, 1, 0, 0, 1]` |
| gate_triggered / reasons | `True / m_best_not_all_certified` | `True / m_best_not_all_certified` |
| ratchet_promoted / promote_reason | `False / no_new_candidate` | `False / no_new_candidate` |
| infra_error (summary) | `False` | `False` |
| outer_metrics.Y_star (summary) | `None` | `None` |
| pre==post==submission gds sha | `True` | `True` |
| verifier status | `missing_submission` | `evaluated` |
| verifier score | `0.0` | `0.1` |
| score_components | `gds=0.0,lvs=0.0,drc=0.0,area=0.0,quality=0.0,postsim=0.0` | `gds=0.1,lvs=0.0,drc=0.0,area=0.0,quality=0.0,postsim=0.0` |
| drc_pass / lvs_pass / pex_success | `False / False / False` | `True / False / False` |
| container wall_sec | `10067.992` | `9941.971` |
| tool_cpu_sec | `381.163` | `1028.113` |
| peak_ram_mb | `462.9` | `527.3` |
| gateway_wait / wall | `7.9%` | `12.2%` |
| handoff status | `None` | `in_progress` |
| lvs (forensic) | `None` | `False (LVS mismatch)` |
| #gds files | `7` | `50` |
| watermarks t_warn / delta_s_rep | `9000.0 / 15` | `9000.0 / 15` |

### 4.3 观测与根因（事实 / 判断分列）

**(a) 代理计量与 `unrecovered_gateway_error`**
- `[事实]` 3 次运行 `exit_code = 0`，`http_429_count = 0`、`http_5xx_count = 0`、`retries = 0`，但 `unrecovered_gateway_error = true`（`unrecovered_gateway_errors = 1`）三题全中。合计 `proxy_requests = 908`，`input_tokens = 59350623`，`output_tokens = 4365216`，`cached_tokens = 8404864`（脚本对 `lr[*].proxy` 求和）。
- `[事实]` `gateway_wait / wall` 范围为 `7.9% – 12.2%`（3 题），均低于 `theta_gw_max = 0.35`，未触发 `gateway_stall`；`model_swap = true` 且 `upstream_models_seen` 单一，无上游模型漂移。
- `[事实]` 容器内 `artifact_summary` 写的是 `infra_error = false`，`cost_ledger.{input,output,cached}_tokens = null`，`outer_metrics.Y_star = null`：容器内摘要看不到宿主机代理计量和外层 Verifier。
- `[判断]` 根因：旧版 `MeteringProxy._relay` 只对上游读取做了异常处理，没有保护对客户端 socket 的 `send_response` / `end_headers`，也没有保护 chunked 结尾 `0\r\n\r\n` 的写入。客户端（`claude`）在 SSE `message_stop` 之后立即退出（Phase B `--max-turns` 用尽），或在 `T_soft` 被 SIGTERM，都会在这几处抛 `BrokenPipe` / `ConnectionReset`。这些异常落进上游错误分支，被记成 `unrecovered_gateway_error`。按 `infra_reasons` 的规则，`infra_ledger` 会把这 3 次有效运行整体判为 `infra_error` 并丢弃，这与 `H_base` 中“基础设施故障与任务失败分账”的不变式冲突。“每题恰好 1 次”与“每个 Trial 结束时恰好 1 次客户端断开”相符，`429` / `5xx` / 重试全为 0 也排除了真实网关故障。

**(b) Phase A 收尾与 `T_warn`**
- `[事实]` `adb-ota-5t-…` 的 Phase A 在 `T_soft` 处被终止：`phase_a.timed_out = true`，`returncode = 143`，`seconds = 9900.029`。`adb-buf-ppsf-…` 自行结束：`timed_out = false`，`returncode = 0`，`seconds = 9734.064`，在 `T_warn = 9000s` 之后 `734.1s`；盲簇题的 Phase A 收尾明细隐去。`adb-buf-ppsf-…` 收尾时清理了 `stray_processes_terminated = 3` 个残留进程。
- `[事实]` 当时 `T_warn = 9000s` 到 `T_soft = 9900s` 只有 `900s = 15 min`。`adb-buf-ppsf-…` 的 `handoff.status` 为 `"in_progress"`。`adb-ota-5t-…` 的工作区里没有 `submission/` 和 `.mcr/best/` 下的 GDS（`forensic.gds` 只有 7 个 `work/` 文件），因此 `m_pre = null`，Verifier 结果为 `missing_submission`，`0.0` 分。
- `[判断]` 15 分钟的预警窗口太短，智能体来不及在 `T_soft` 前把草稿提升为合法提交，也来不及写完交接。把 `T_warn` 提前到 `8400s` 后，离 `T_soft` 有 25 min，离 `T_max` 有 40 min。

**(c) Phase B 修复调用**
- `[事实]` 3 次 Phase B 均以 `returncode = 1`、`timed_out = false` 结束；可见两题用时 `167.831s / 200.844s`（盲簇题明细隐去）。按 `granted_repair_budget_sec`（`779.917 / 900.0`）计，可见两题未用掉的预算分别是 `612.1s / 699.2s`（`78.5% / 77.7%`）。`best_sha256_before == best_sha256_after`，`ratchet_promoted = false`（`promote_reason = "no_new_candidate"`）。`adb-buf-ppsf-…` 的 `fork_meta.pending_promotion = {"promoted": false, "reason": "same_tier_no_improvement"}`。
- `[事实]` 三题 Phase B 的 stderr 完全相同：`[claude-code:unrecognized_model] {"model":"ExternalAPI-Physical-Design-deepseek-v4-1-flash-volcengine-public","query_source":"sdk"}`。`adb-ota-5t-…` 的 Phase B stream-json 尾部 assistant 记录的 `model` 为 `deepseek-v4-1-flash-261017`；三题最后一条记录均带 `"stop_reason":"tool_use"`。
- `[事实]` `fork_meta.phase_a_argv` 含 `--settings /tmp/claude-code-settings/settings.json`。这是 Harbor 的临时路径，Phase B 原样沿用了它。
- `[判断]` `unrecognized_model` 只是 CLI 对非 Anthropic 模型名的提示，不是失败原因：请求实际已到达上游并返回。Phase B 是被 `--max-turns 15` 截断的，不是超时：轮数先用完，客户端在最后一条工具调用后以 `rc = 1` 退出。`[未核验]` 由于 `pb` 中每行只保留了 250 字符，结果记录的 `subtype`（是否为 `error_max_turns`）看不到。可见两题 15 轮只用掉约 `2.8–3.3 min`，大部分 `Delta_T_rep = 900s` 时间预算闲置。

**(d) 签核结果**
- `[事实]` `adb-buf-ppsf-…`：`status = "evaluated"`，`score = 0.1`（只有 `gds = 0.1`，其余分量为 0），`drc_pass = true`，`lvs_pass = false`，`pex_success = false`，`m_pre = m_post = [1, 0, 1, 0, 0, 1]`，且 pre / post / submission 三个 GDS 的 sha256 相同；`forensic.lvs` 的失败原因为 `"LVS mismatch"`。盲簇题的签核明细按红线隐去。
- `[事实]` `adb-buf-ppsf-…` 是 `drc_pass = true` 但 `score_components.drc = 0.0`（对齐 `alw-mcr-instantiation.md` §1.1 核验的 `_scoring_core.py` 公式 $0.10\,I_{\mathrm{G}} + I_{\mathrm{L}}(0.10 + 0.10\,I_{\mathrm{D}})$：`lvs_pass = false` 时 DRC 分项按规则门控归零）。

### 4.4 pre-Phase-1c 锁定修复

以下 5 项根据 §4.3 的观测确定，已在代码、配置与测试中实现并先于 Phase 1c 锁定。它们都不改变 `docs/mcr-formal.tex` 的不变式：不做步级强制回滚，棘轮只沿镜像认证序推进，`infra_error` 与任务失败分账。

1. **代理客户端断开不再记为网关故障**（[harbor_runner.py](../src/ssap/targets/alw/harbor_runner.py) `MeteringProxy._relay`）：
   - `send_response` / `end_headers`、逐块写入、chunked 结尾 `0\r\n\r\n` 这三类客户端 socket 写操作全部加了保护。客户端断开只记入信息性计数 `client_disconnects`，永不计为 `infra_error`。
   - 只有客户端仍在接收、上游读取失败时，才记 `unrecovered_gateway_error`。上游读取被截断时不再补发 chunk 结尾，避免把残缺响应伪装成完整响应。
   - 新增单元测试 `test_proxy_client_closes_mid_sse_stream_is_not_gateway_error` 与 `test_proxy_client_closes_before_chunked_terminator_is_not_gateway_error`（[test_alw_proxy_and_env.py](../tests/test_alw_proxy_and_env.py)）。
2. **水位线**：`T_warn = 8400s`（140 min），`Delta_S_rep = 80` 轮，`configs/alw/config.server.json` 与 `Watermarks` 默认值（[watermarks.py](../src/ssap/kernel/watermarks.py)）同步修改。其余不变：`T_max = 10800s`，`T_soft = 9900s`，`Delta_T_rep = 900s`，`min_remaining = 180s`，`tail_buffer = 120s`，`kill_grace = 5s`。`T_warn` 到 `T_soft` 从 15 min 增至 25 min，到 `T_max` 为 40 min。新增 CLI 覆盖参数 `--delta-s-rep`。
3. **Phase B / Path 1 调用**（[repair.py](../src/ssap/checkers/alw/repair.py)、[agent_wrapper.py](../src/ssap/targets/alw/agent_wrapper.py)）：
   - `phase_b_args` 把 `/tmp/claude-code-settings/` 下的 `--settings` 改写为 `/opt/harness/settings.json`，其他自定义 `--settings` 路径原样保留。
   - 离线 `build_stateless_repair_invocation` 的轮数上限改为读取归档的 `granted_repair_turns`：先查 `fork_meta`，再查 `gate_report`，都没有则用 `80`。不再硬编码 `15`。
4. **`ssap alw-harvest`**（[harvest.py](../src/ssap/targets/alw/harvest.py)）：`ssap alw-harvest --tag <tag> | --job-dir <dir> [--out-runs runs.jsonl] [--out-archive archive.json] [--out-samples samples.jsonl] [--backfill-summaries]`。它把一个 Harbor job 目录转成以下产物：
   - 供 `alw-diagnose` / `alw-report` 使用的 runs 记录；
   - Path 1 归档；
   - `B_dev` 终态 `BankSample`；
   - `artifact_summary_merged.{json,md}` 回填（`outer_metrics`、token 成本、`infra_error`），补上 §4.3(a) 中容器内摘要缺失的外层字段。
   其中 `t_soft_stop` 取自 `fork_meta.phase_a.timed_out`。
5. **`docs/server-info/` 已 gitignore**：服务端原始导出（含 `/localdata/<USER>/` 路径）不入库，本文只引用经脱敏、由脚本抽取的数字。

### 4.5 对 Phase 1c 的含义

- `[判断]` 按旧版代理写入 `launch_results.json` 的标志，Phase 1b 的 3 次运行都会被判为 `infra_error`（`alw-harvest` 原样读取已落盘的代理标志，不做篡改）；而从真实流量看（`429` / `5xx` / 重试均为 `0`，`gateway_wait / wall` 均低于 `0.35`），它们属于客户端退出触发的误标。
- `[判断]` Phase 1c 使用 §4.4 (2) 的现行锁定水位线运行，预期代理统计中出现 `client_disconnects >= 1` 而 `unrecovered_gateway_error = false`；若在新版代理下仍出现 `unrecovered_gateway_error = true`，则代表真实的上游读取或 `429/5xx` 未恢复故障，严格按 `infra_error` 配对重跑处理。

---

## 5. Phase 1c（12 题 × 2 种子 H_base 开发集基线与四项零模型费体检）

> 本节所有数字由一段只读 Python 脚本从 `docs/server-info/phase1c_sync.json`（`harvest` / `runs` / `diag`）与 `configs/alw/{catalog.json,split.json}` 抽取或纯算术派生（比值、百分比、求和、均值）；脚本置于仓库之外，不入库。
> **盲簇隔离红线**：`split.json` 冻结的 3 个内部盲簇（共 4 题 × 2 种子 = 8 次运行）在本文中只以聚合计数出现，不给出其题号、题 ID、簇 ID、样本 ID、路径或逐次结果；逐题表只列 8 个可见 `dev` 题（`#2, #7, #11, #12, #13, #18, #21, #27`，16 次运行）。全 24 次的总量（token、漏斗、四分法等）属聚合量，照常列出。

### 5.1 运行配置与数据来源

- `[事实]` Harbor job `phase1c-dev-hbase`，`split = dev`，只有 `arm = H_base` 一个臂（本次同步中没有 `H_raw` 运行），种子 `{0, 1}`（`diag.k0 = 2`），`12` 题 × `2` 种子 = `24` 次 Trial（`harvest.n_trials = 24`，`seed_noise_floor.n_common_tasks = 12`）。
- `[事实]` `diag.module_switches`：`enable_mirror_gate_channel = true`，`enable_endogenous_probe_channel = true`，`enable_checkpoint_ranker = false`，`capability_bound_tasks = []`。
- `[事实]` 容器 `cost.wall_sec` 范围 `5598.728s – 10691.891s`，全部低于 `T_max = 10800s`。
- `[未核验]` 水位线取值：`phase1c_sync.json` 不含 `fork_meta.watermarks`，本节按设计假定运行时使用 §4.4 (2) 的现行锁定值（`T_warn = 8400s`、`Delta_S_rep = 80` 轮），未能在本次同步数据中逐字段核对。

### 5.2 运行健康与成本账本（全 24 次聚合）

| 观测维度 | 实测值（脚本抽取） | 说明 |
|---|---|---|
| 入账与基础设施 | `n_scored = 24 / 24`，`n_infra_error = 0`（`harvest`、`diag` 与逐次 `infra_error` 三处一致），`exit_code = 0` 共 `24` 次，`evaluation_error = false` 共 `24` 次，`score_is_lower_bound = true` 共 `0` 次 | `status = evaluated` `23` 次 + `missing_submission` `1` 次（后者按规则计 `Y* = 0`，属任务失败，不剔除） |
| 网关故障与客户端断开 | `unrecovered_gateway_error = false` 共 `24 / 24` 次；`client_disconnects` 合计 `13`，分布在 `11` 次运行中 | 对照 §4.5 的预期：出现 `client_disconnects >= 1`，且无一次被记为网关故障 |
| 网关排队占比 | `gateway_wait_seconds / cost.wall_sec` 范围 `7.1% – 25.4%` | 全部低于 `theta_gw_max = 0.35`，未触发 `gateway_stall` |
| 模型同质化 | `model_swap = true` 共 `24 / 24` 次 | 与 Phase 1b 三次运行的取值相同（§4.2） |
| 内存 | `peak_ram_mb` 范围 `462.0 – 739.3 MB` | 远低于单 Trial 硬配额 `32 GiB`，无 OOM `137` |
| 请求与 token | `proxy_requests` 合计 `9732`；`input_tokens = 755,778,889`，`output_tokens = 34,375,950`，`cached_tokens = 85,396,352` | 每次运行平均 `input ≈ 31,490,787`、`output ≈ 1,432,331`（脚本整除取整） |
| 工具 CPU 与美元 | `tool_cpu_sec` 合计 `38345.2s`；`cost.usd` 全部为 `null` | 网关未回报单价，美元成本留空，不估算 |
| 双水位线与 Phase B | `t_soft_stop = true` `14 / 24`；`gate_triggered = true` `20 / 24`；`ratchet_promoted = true` `5 / 20`（`25.0%`，5 次晋升全部发生在门控触发的运行中） | Phase 1b 为 `0 / 3` 晋升（§4.2） |

### 5.3 逐题实测表（8 个可见 `dev` 题 × 2 种子 + 盲簇合计 1 行）

下表由同一脚本逐字段生成。列名省略 `sky130-` 前缀与 `-layout` 后缀；`profile` 与 `q_max` 取自 `catalog.json`；`d* 漏斗` 依次为 `gds/drc/lvs/pex/postsim`（`⊥` = 外层 `bottom`，即前序门失败后短路未评）；`m` 依次为 `m_gds m_lvs m_shape m_fill m_morph m_drc`（`None` = 无可检 GDS）；`gw/wall` = `gateway_wait_seconds / cost.wall_sec`；三元组列为 `t_soft_stop / gate_triggered / ratchet_promoted`；末列为 `diag.taxonomy` 四分法类别。

| 题 | profile | q_max | seed | Y* | C* | d* 漏斗 | d*_shape | m | requests | gw/wall | peak_ram_mb | t_soft / gate / promoted | 四分法 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `#2` `adb-buf-ppsf-bs-gain1-sfdr90` | continuous | `4` | 0 | `0.1` | `0` | `1/1/0/⊥/⊥` | `1` | `101001` | `415` | `12.9%` | `673.5` | `True / True / True` | `public_mirror_execution_gap` |
| `#2` `adb-buf-ppsf-bs-gain1-sfdr90` | continuous | `4` | 1 | `0.1` | `0` | `1/0/0/⊥/⊥` | `1` | `101000` | `776` | `25.4%` | `604.9` | `True / True / False` | `public_mirror_execution_gap` |
| `#7` `adb-complementary-folded-cascode-ab-opamp-pvt` | continuous | `3` | 0 | `0.1` | `0` | `1/1/0/⊥/⊥` | `1` | `101001` | `398` | `13.0%` | `512.5` | `False / True / False` | `public_mirror_execution_gap` |
| `#7` `adb-complementary-folded-cascode-ab-opamp-pvt` | continuous | `3` | 1 | `0.1` | `0` | `1/0/0/⊥/⊥` | `0` | `101000` | `464` | `14.2%` | `662.1` | `True / True / True` | `public_mirror_execution_gap` |
| `#11` `adb-fd-sampling-feedback-ota-gain1-settling40ns-noise300uv-pvt-mc` | binned | `3` | 0 | `0.1` | `0` | `1/1/0/⊥/⊥` | `1` | `101001` | `361` | `10.4%` | `739.3` | `False / True / False` | `public_mirror_execution_gap` |
| `#11` `adb-fd-sampling-feedback-ota-gain1-settling40ns-noise300uv-pvt-mc` | binned | `3` | 1 | `0.1` | `0` | `1/1/0/⊥/⊥` | `1` | `101001` | `674` | `23.3%` | `463.5` | `False / True / False` | `public_mirror_execution_gap` |
| `#12` `adb-fd-sampling-feedback-ota-gain8-settling10ns-noise1mv-pvt-mc` | continuous | `3` | 0 | `0.1` | `0` | `1/1/0/⊥/⊥` | `1` | `101001` | `412` | `13.4%` | `481.9` | `True / True / False` | `public_mirror_execution_gap` |
| `#12` `adb-fd-sampling-feedback-ota-gain8-settling10ns-noise1mv-pvt-mc` | continuous | `3` | 1 | `0.1` | `0` | `1/1/0/⊥/⊥` | `1` | `101001` | `436` | `14.4%` | `462.9` | `True / True / True` | `public_mirror_execution_gap` |
| `#13` `adb-fd-two-stage-miller-opamp-gain60-pm60-noise50uv-pvt` | continuous | `4` | 0 | `0.1` | `0` | `1/0/0/⊥/⊥` | `1` | `101000` | `408` | `12.3%` | `475.0` | `True / True / True` | `public_mirror_execution_gap` |
| `#13` `adb-fd-two-stage-miller-opamp-gain60-pm60-noise50uv-pvt` | continuous | `4` | 1 | `0.1` | `0` | `1/1/0/⊥/⊥` | `1` | `101001` | `445` | `14.5%` | `485.5` | `True / True / False` | `public_mirror_execution_gap` |
| `#18` `adb-low-power-line-driver-pvt` | continuous | `4` | 0 | `0.1` | `0` | `1/1/0/⊥/⊥` | `1` | `101001` | `396` | `12.3%` | `478.8` | `True / True / False` | `public_mirror_execution_gap` |
| `#18` `adb-low-power-line-driver-pvt` | continuous | `4` | 1 | `0.1` | `0` | `1/0/0/⊥/⊥` | `1` | `101000` | `321` | `9.6%` | `463.4` | `True / True / False` | `public_mirror_execution_gap` |
| `#21` `adb-ota-5t-gain40-pm60-noise50uv-pvt` | continuous | `3` | 0 | `0` | `0` | `0/⊥/⊥/⊥/⊥` | `⊥` | `None` | `146` | `7.1%` | `462.0` | `False / True / False` | `missing_or_invalid_gds` |
| `#21` `adb-ota-5t-gain40-pm60-noise50uv-pvt` | continuous | `3` | 1 | `0.1` | `0` | `1/0/0/⊥/⊥` | `1` | `101000` | `302` | `8.1%` | `462.4` | `True / True / False` | `public_mirror_execution_gap` |
| `#27` `adb-tia-zt8k-bw750m-noise5p-sfdr70-power5m-pt` | binned | `4` | 0 | `0.1` | `0` | `1/1/0/⊥/⊥` | `1` | `101001` | `557` | `17.3%` | `463.9` | `True / True / False` | `public_mirror_execution_gap` |
| `#27` `adb-tia-zt8k-bw750m-noise5p-sfdr70-power5m-pt` | binned | `4` | 1 | `0.1` | `0` | `1/0/0/⊥/⊥` | `1` | `101000` | `394` | `13.5%` | `464.9` | `False / True / False` | `public_mirror_execution_gap` |
| 3 个内部冻结盲簇合计（4 题 × 2 种子 = 8 次运行，逐题明细按盲簇隔离红线隐去） | — | — | — | 均值 `0.304423` | 合计 `1` | 计数 `8/6/5/5/1` | — | — | 合计 `2827` | — | — | 计数 `3 / 4 / 1` | — |

- `[事实]` 可见 16 次小计：`Y*` 均值 `0.093750`，`C* = 1` 共 `0` 次，漏斗计数 `gds/drc/lvs/pex/postsim = 15/9/0/0/0`，`proxy_requests` 合计 `6905`，`t_soft / gate / promoted` 计数 `11 / 16 / 4`。可见 + 盲簇合计与 §5.2 全集总量一致（`6905 + 2827 = 9732` 请求；`4 + 1 = 5` 次晋升）。
- `[事实]` 可见 8 题中 7 题两种子 `Y*` 均为 `0.1`（只拿到 `gds` 分量）；`#21` 的 seed 0 为 `missing_submission`（`Y* = 0`，`m = None`），seed 1 为 `0.1`。

### 5.4 签核分数、五级通过漏斗与种子噪声地板

- `[事实]` 全 24 次 `mean_Y_star = 0.163974`（`harvest.mean_Y_star` 与脚本对 `runs[*].Y_star` 重算一致）；种子均值 `seed 0 = 0.179713`、`seed 1 = 0.148235`。`Y*` 计数分布：`= 0` 共 `1` 次，`= 0.1` 共 `18` 次，`= 0.2` 共 `1` 次，`> 0.2` 共 `4` 次（后三档全部位于盲簇合计行，逐次值不列）。
- `[事实]` 外层 `d*` 五级通过漏斗（全 24 次，计 `d*_k = 1`）：

| 级 | `gds` | `drc` | `lvs` | `pex` | `postsim`（即 `C* = 1`） |
|---|---|---|---|---|---|
| 通过次数 | `23 / 24`（`95.8%`） | `15 / 24`（`62.5%`） | `5 / 24`（`20.8%`） | `5 / 24`（`20.8%`） | `1 / 24`（`4.2%`） |

- `[事实]` 镜像侧：`m` 六项全为 `1` 的运行共 `4` 次，`m_lvs = 1` 共 `5` 次；可见 16 次中 `m_lvs = 1` 为 `0` 次。
- `[事实]` `diag.seed_noise_floor`：`sigma_seed = 0.022258`，`mean_within_task_sd = 0.055708`（`n_common_tasks = 12`），`advisory_reference_only = true`，`note = "reference scale for reading gains; never a search or confirmation veto"`。

### 5.5 四项零模型费体检（`diag`，0 LLM token）

对照 [alw-progressive-plan.md](alw-progressive-plan.md) Phase 1 第 4 步的四项体检，本次同步的覆盖情况如下。

**(1) 残余失败四分法（`diag.taxonomy`）**
- `[事实]` 未通过运行 `n_not_passed = 23`（= `24 - 1` 次 `C* = 1`）。计数与占比：

| 类别 | 计数 | 占 23 次未通过 |
|---|---|---|
| (a) `missing_or_invalid_gds` | `1` | `4.3%` |
| (b) `public_mirror_execution_gap` | `19` | `82.6%` |
| (c) `electrical_or_hidden_info_gap` | `3` | `13.0%` |
| (d) `capability_bound` | `0` | `0.0%` |

- `[事实]` (c) 按 `runtime_profile` 拆分：`continuous = 3`，`binned = 0`，`unknown = 0`。可见 16 次中 (a) `1` 次（`#21` seed 0），(b) `15` 次，(c) `0` 次；故 (c) 的 `3` 次与 (b) 的另外 `4` 次均位于盲簇合计行。

**(2) 验证器稳定性与镜像一致率（`diag.mirror_concordance`、`diag.verifier_repeatability`）**
- `[事实]` 镜像一致率（脚本按 `runs[*].m` 与 `runs[*].d_star` 重算，与 `diag.mirror_concordance` 逐项相同；`excluded` = 外层 `bottom` 或无 `m`）：

| 检查器 | agree | excluded | `m=0, d*=1` 误拒 | `m=1, d*=0` 误认证 |
|---|---|---|---|---|
| `m_gds` | `23` | `1` | `0` | `0` |
| `m_lvs` | `23` | `1` | `0` | `0` |
| `m_shape` | `21` | `1` | `0` | `2` |
| `m_fill` | `5` | `19` | `0` | `0` |
| `m_morph` | `5` | `19` | `0` | `0` |
| `m_drc` | `23` | `1` | `0` | `0` |

- `[事实]` `m_shape` 的 `2` 次 `m = 1, d* = 0` 所在题在 `catalog.json` 中均为 `q_max = 3.0`；其中 `1` 次为可见 `#7` seed 1（见 §5.3：`m_shape = 1`，`d*_shape = 0`），另 `1` 次位于盲簇合计行。按定义 `m_shape = 1` 意味着 `q <= 4.0`（`epsilon_3 = 0` 固定），`d*_shape = 0` 在 `q_max = 3.0` 题上意味着 `q > 3.0`，由此推出两次均落在 `q in (3.0, 4.0]`。`[未核验]` `phase1c_sync.json` 不含 `q` 原值与 `advisory_flags`，两次运行是否挂出 `advisory_aspect_gt_3` 未能逐字段核对。
- `[事实]` `verifier_repeatability.measured = false`（`artifacts = []`，`deterministic = null`，`max_spread = null`）：本次同步没有做离线 `alw verify` 双跑抖动测量；`score_is_lower_bound = true` 为 `0 / 24`。

**(3) 双种子噪声地板（`diag.seed_noise_floor`）**
- `[事实]` 数值见 §5.4：`sigma_seed = 0.022258`，`mean_within_task_sd = 0.055708`；两种子均值差 `0.031478`（脚本对两个种子均值相减）。

**(4) 轨迹内检查点选择余量（`diag.checkpoint_gap`）**
- `[事实]` `n_runs_measured = 0`，`n_runs_positive_gap = 0`，`max_gap = 0.0`，`mean_gap = null`，`runs = []`；`enable_checkpoint_ranker = false`。本次同步没有离线重评草稿里程碑 GDS。
- `[判断]` 由于 `n_runs_measured = 0`，`max_gap = 0.0` 不能读作“测得检查点余量为零”，第 (4) 项视为未测量。

### 5.6 观测与判断（事实见 §5.2–§5.5，本小节只放 `[判断]`）

**(a) 代理修复与分账**
- `[判断]` §4.4 (1) 的代理修复在 Phase 1c 上表现符合 §4.5 的预期：`13` 次客户端断开全部只记入信息性计数，`24 / 24` 次 `unrecovered_gateway_error = false`、`n_infra_error = 0`，没有有效运行因客户端退出被误判为 `infra_error` 而丢弃。`#21` seed 0 的 `missing_submission` 按任务失败计 `0` 分保留，符合 `H_base` “基础设施故障与任务失败分账”的不变式。

**(b) 资源与并发**
- `[判断]` 峰值内存最高 `739.3 MB`、网关排队占比最高 `25.4%`（低于 `0.35`），说明当前并发下资源与网关都不是瓶颈。排队占比上沿高于 Phase 1b 的 `12.2%`；两个最高值（`#2` seed 1 `25.4%`、`#11` seed 1 `23.3%`）也是可见运行中请求数最多的两次（`776`、`674`），更可能来自高请求量下的累计排队，而非网关故障。

**(c) 主瓶颈是可公算的 LVS 执行缺口**
- `[判断]` 23 次未通过中 `19` 次属 (b) `public_mirror_execution_gap`，漏斗从 `drc 15/24` 骤降到 `lvs 5/24`，可见 16 次 `lvs` 全为 `0`。按 [alw-progressive-plan.md](alw-progressive-plan.md) Phase 1 体检信号表，这对应“类 (b) 占比高”一行：Round 1 优先投入第 I 类镜像门控升级与第 III 类 KLayout/Magic/Netgen 报错压缩翻译，重点在 LVS。
- `[判断]` 可见 8 题 `Y*` 全部停在 `0.1`（或 `0`），过 LVS 前 `drc` 分项按规则门控归零（§4.3 (d)），因此可见题上的任何增益都要以首次过 LVS 为前提，分数呈台阶式而非连续变化。

**(d) 电气 / 隐藏信息缺口证据少且只在盲簇**
- `[判断]` 过 LVS 的 `5` 次中只有 `1` 次过 `postsim`，(c) 类 `3` 次全部为 `continuous` 且全部位于盲簇。可见侧没有 (c) 类样本，提案方能拿到的只有聚合计数；现阶段不足以支撑为 (c) 定制第 II 类探针，需等 LVS 缺口收窄后再看。

**(e) 镜像一致率**
- `[判断]` 六个检查器均为 `0` 误拒，`m_gds/m_lvs/m_drc/m_fill/m_morph` 均为 `0` 误认证，与“只有通过一致性核验的镜像检查器持有覆写权”的要求一致。`m_shape` 的 `2` 次误认证是 `q_max = 3.0` 题上 `q in (3.0, 4.0]` 的设计内保护带情形（全局必要上界 `q <= 4.0`、`epsilon_3 = 0` 固定，该区间由第 II 类 `advisory_aspect_gt_3` 提示），不是镜像缺陷，也不得送入 `epsilon_3` 校准。
- `[判断]` `m_fill` / `m_morph` 只有 `5` 个可比样本（其余 `19` 次因外层 LVS 失败短路为 `bottom`），`m_lvs` 的正样本也只有 `5` 个，`S+_k` 覆盖偏薄。仅凭终态样本难以对这几项给出有力的一致性证据，需要 Phase 2 由裁判侧对 `B_dev` 终态版图合成 0-token 变体来补足正负样本。

**(f) Phase B 晋升**
- `[判断]` 晋升率从 Phase 1b 的 `0 / 3` 升到 `5 / 20`（`25.0%`），与 §4.4 (2)(3) 的 `Delta_S_rep = 80` 轮与 `--settings` 修复方向一致。`[未核验]` `phase1c_sync.json` 不含晋升前后的 `Y*` 对照，这 `5` 次晋升对最终分数的贡献无法从本次数据归因，不能把晋升率读成分数增益。

**(g) 噪声地板的读法**
- `[判断]` 可见 8 题中 7 题两种子 `Y*` 完全相同，`mean_within_task_sd = 0.055708` 的大部分来自 `#21` 与盲簇。`sigma_seed = 0.022258` 只作为后续读增益的参照尺，按 `docs/mcr-formal.tex` 的 `C1` 协议不作为任何搜索或确认的否决门槛。

### 5.7 对后续阶段的含义

- `[判断]` Phase 1c 的可停机交付物（`dev` 集 `H_base` 双种子基线成绩单 + 体检报告）已基本具备：24 次全部入账、`infra_error = 0`。四项体检中第 (1)(3) 项完整，第 (2) 项只有镜像一致率部分、缺验证器重放抖动，第 (4) 项未测量。
- `[判断]` 在第 (4) 项测量之前，`enable_checkpoint_ranker = false` 只是默认取值，不是“余量近零、关闭排序器”的实测结论；若后续要据此定案，需先离线重评草稿里程碑 GDS。
- `[判断]` 本次只有 `H_base` 臂，`dev` 上看不到 `H_raw -> H_base` 的保底收益；正式三段归因（`raw -> H_base -> H*`）仍按规划在密封 `test` 上以 `>= 3` 种子完成。
- `[判断]` 下一步优先级：先用 Phase 2 合成变体扩充 `B_dev` 并跑 Stage 0 `H_base` 一致性筛查（补 `m_lvs` / `m_fill` / `m_morph` 的 `S+_k` 覆盖），再按 §5.6 (c) 把 Round 1 的提案重心放在 LVS 执行缺口上。盲簇在这一过程中继续只回传聚合计数。

---

## 6. Phase 2（主动裁判库 B_dev 合成与 Stage 0 一致性核验，0 LLM Token）

> 本节数字全部由脚本核对自 `docs/server-info/phase2_bank_report.json`；严守盲簇隔离红线，不列出任何盲簇题号、簇 ID、样本 ID 或路径。

### 6.1 裁判库规模与合成变体覆盖 `[事实]`

- **终态入库**：`build_bank` 从 Phase 1c 接收 `24` 条开发集终态记录（`n_samples = 24, written = 24`）。
- **零 Token 合成与双端验证**：`synthesize_bank` 从 `3` 个独立开发簇（`2` 个盲簇、`1` 个可见簇，`n_source_errors = 0`）生成 `25` 个确定性变体，**`25 / 25` 全部完成断网 `alw verify` 与容器内 `mcr_check` 双端检验（`n_skipped_infra = 0`）**，将 `B_dev` 扩充至 **`49` 条样本**（`terminal = 24`、`synthetic_neg = 16`、`synthetic_pos = 9`），`S_plus`（`C* = 1`）由 `1` 条扩充至 `3` 条（原版 + `grid_shift` + `rotate90`）。
- **各变体预期标签核对**：`drc_sliver`（`3/3 agree`）、`fill_low_ring`（`2/2 agree`）、`lvs_missing_label`（`2/2 agree`）、`shape_q_over_4`（`3/3 agree`）、`shape_q_advisory`（`3/3 agree`）、`grid_shift`（`3/3 agree`）、`rotate90`（`3/3 agree`）；仅 `gds_truncated`（`3`）与 `gds_renamed_top`（`3`）在旧版 `metrics_reader` 下出现 `disagree = 3`（见 §6.2）。

### 6.2 Stage 0 一致性与 `metrics_reader` 几何有效性对齐修复 `[事实 + 判断]`

- **`[事实]` 已通过项**：`m_lvs`、`m_drc`、`m_shape`、`m_fill`、`m_morph` 五项第 I 类镜像检查器均为 `0` 方向性违例、`0` 双向违例（`ok = true`），`calibrate_eps("m_fill")` 校准得 **`eps_fill = 0.0`**；第 II 类探针 `advisory_aspect_gt_3` 在 `S_plus` 上 **`k_FP_S_plus = 0`**、盲簇上 **`k_TP_blind = 4`**（`class2_ok = true`）。
- **`[事实 + 判断]` `m_gds` 6 例偏差根因与修复**：外层 `evaluate.py` 对普通文件先置 `submission_present = true`（对应内层 Tier 0 `file_present = 1`），随后在 `stages.area` 对截断流或错 `top_cell` 置 `geometry_valid = false`（对应内层 Tier 1 `m_gds = 0`）。旧版 `metrics_reader.d_star` 仅读取 `submission_present` 而忽略 `geometry_valid = false`，将这 `6` 个损坏/错名变体误标为 `d*_gds = 1`。已在 [metrics_reader.py](../src/ssap/targets/alw/metrics_reader.py) 对齐 `present and geometry_valid`（同时当 `d*_gds != 1` 时将 `d*_shape` 短路为 `bottom`），并新增 `ssap alw-concordance --refresh-bank-metrics` 直接从已缓存 `metrics.json` 原子刷新 `B_dev`，无需重跑 Docker。

---

## 7. Phase 3（外层循环 Round 1–2）与留出评测方案变更

> 数字由脚本核对自 `docs/server-info/round1.txt`（Round 1 脱敏战报）与 `docs/server-info/round_report_redacted.json`（Round 2 脱敏战报）；盲簇只列聚合值。

### 7.1 两轮结果 `[事实]`

| 项目 | Round 1 | Round 2 |
|---|---|---|
| 候选 | `repair-cert-diagnosis`（Class 4，Path 1） | `repair-lvs-artifacts`（Class 4，Path 1） |
| 过 Stage 0 的尝试 | 第 6 次（第 2–5 次：2 次超时、1 次 80 轮上限、1 次 `proposal_missing_or_invalid_json`） | 第 3 次（第 1–2 次 `proposal_missing_or_invalid_json`） |
| trial / 发散 | 24 / 20 | 24 / 20 |
| 总分差 `sum_delta_Y` | `+0.055073`（晋升） | `+0.011594`（晋升） |
| 可见 16 个 trial | `+0.121739`；Rescued 2，Neutral 14 | `-0.055073`；Damaged 2，Neutral 14 |
| 盲簇 8 个 trial（4 发散） | `-0.066667` | `+0.066667` |
| 模型调用 / 验证器调用 | 2792 / 18 | 3032 / 21 |

- 非零可见 trial 两轮都是 `ota-5t` 种子 0（`+0.033333`，`-0.033333`）与 `tia-zt8k` 种子 0（`+0.088406`，`-0.021739`）。
- 两轮累计相对 `H_base` 为 `+0.066667`；Stage 1 Round 1 墙钟约 13.5 h（60 次串行抢修）。
- 当前擂主 `H_base+repair-cert-diagnosis+repair-lvs-artifacts`，两处改动都只在 `repair.py`（终局抢修提示词）。

### 7.2 观测 `[判断]`

- Round 2 的对照分数是 Round 1 候选 3 个抢修种子的均值（晋升时刷新的存档）。盲簇与 `ota-5t` 两轮精确反转，累计增益只来自 `tia-zt8k` 种子 0 一个 trial；这符合抢修随机性，不足以说明两处改动有效。
- 改抢修提示词会改变门控签名里的提示词哈希，所有触发抢修的 trial 都判为发散，每轮约 60 次抢修。Stage 1 抢修是串行执行的，`--parallel` 对 Path 1 不起作用。
- 两轮可见 14 个 trial 都是 Neutral，LVS 瓶颈未动；15 分钟抢修窗口改不了前 165 分钟没画对的 LVS。

### 7.3 留出评测方案变更（用户决定，2026-11-26）

- **不跑验证集 C1**：`H*` 直接定为当前擂主，在任何留出运行之前写入 `STATE/test/manifest.json`（`H*` 与 `H_base` 源码树哈希）；之后 `H*` 改动会被拒绝。
- **每臂 1 个种子**（协议要求 `>= 3`），因此报告不给出种子噪声地板；`alw-report` 需显式加 `--accept-fewer-seeds`。
- **不跑诚实基线**（一页人工说明、同预算 Best-of-k）。
- **`H*` 用抢修重放**：两处改动都不影响前 165 分钟，`H*` 在 `H_base` 测试运行的 `pre_repair` 存档上用 `H*` 代码重跑一次无状态 Phase B，与 dev Path 1 同一机制；未触发抢修的槽位与 `H_base` 相同。
- `H_base` 臂从干净的 `STATE/loop/h_base_src` 暂存（搜索起点），`H_base -> H*` 只差两处晋升改动。
- 并行 6；入口 `ssap alw-test`（`src/ssap/eval/holdout.py`）。

### 7.4 密封测试集结果（14 题 × 1 种子，2026-11-27 揭盲）

> 均分、成本、区间由脚本核对自 `docs/server-info/test_export/{test_report.json, replay_summary.json, manifest.json}`。逐题与漏斗数字由脚本核对自 `docs/server-info/test_export/{test_runs.jsonl, replay.jsonl}`。

| 指标 | `H_raw` | `H_base` | `H*` |
|---|---|---|---|
| 平均 `Y*` | `0.0571` | `0.1453` | `0.1453` |
| `C* = 1` | 0 / 14 | 1 / 14 | 1 / 14 |
| 有效 GDS | 6 / 14 | 14 / 14 | 14 / 14 |
| DRC 通过 | 2 / 14 | 10 / 14 | 11 / 14 |
| LVS 通过 | 1 / 14 | 1 / 14 | 1 / 14 |
| 后仿通过 | 0 / 14 | 1 / 14 | 1 / 14 |
| 输入 / 输出 token（合计，M） | `381.0 / 22.7`（15 次，含 1 次基础设施重跑） | `376.9 / 20.2`（14 次） | `H_base` + 重放 `33.3 / 1.2` |

`[事实]`
- `H_raw -> H_base`：`+0.0882`，逐题 8 胜 5 平 1 负。8 胜中 7 题是 `0 -> 0.1`（`H_raw` 未交出有效 GDS），1 题 `0 -> 0.7344`（`ajc-schmitt`，全场唯一 `C* = 1`）；1 负是 `fct-residue-amplifier`（`H_raw` 过 LVS 得 `0.3`，`H_base` 得 `0.1`）。去掉 `ajc-schmitt` 后其余 13 题的均差为 `+0.0385`。
- `H_base -> H*`：`0.0000`，0 胜 0 负；簇自助法区间 `[0, 0]`。重放判定：13 个槽位重跑了抢修，1 个（`ajc-schmitt`）两臂都未触发抢修、直接沿用。
- 13 个重放槽位两臂的 `Y*` 都是 `0.1`（LVS 均未过）。DRC 分项有 3 题不同：`ajc-rc-osc`、`local-two-stage-ota` 为 `0 -> 1`，`two-stage-miller-opamp-gain60` 为 `1 -> 0`。
- `infra_error`：最终未解决 0；`H_raw` 有 1 个槽位首跑为基础设施故障，重跑后入账。
- `H_raw` 的 `wall_sec`、`tool_cpu_sec`、`peak_ram_mb` 为 0 是因为该臂没有 MCR 快照可读，不是实测值。

`[判断]`
- 自动优化在测试集上没有可测的增益。这与 §7.2 对 dev 两轮涨分的读法一致：`Y*` 在过 LVS 之前停在 `0.1` 的台阶上，抢修阶段的改动动不了 LVS，分数就不变。
- DRC 分项 2 升 1 降、每槽位只有 1 次抢修，不能据此说 `H*` 的抢修提示词更会修 DRC。
- `H_raw -> H_base` 的增益里，“交出有效 GDS”这部分有 8 / 14 对 0 / 14 的空白卷差距支撑；`C* = 1` 这部分只来自 1 题 1 个种子（占总增益的约六成），需要更多种子才能确认。
- `[未核验]` `H_raw` 在本环境的 `0.0571` 明显低于论文同配置的 `0.1974`（`paper_reference.json` `table1[2]`，40 题 × 3 遍；有效 GDS 6 / 14）。`[事实]` `test_runs.jsonl` 中 `H_raw` 得 0 分的 8 题状态全部是 `missing_submission`，各自有 `150–376` 次模型请求（服务器终端输出，2026-11-27），即运行正常进行但结束时 `submission/` 里没有版图；结束方式见 §7.5。论文与其仓库（单提交、无 release）没有公布逐题结果，README 注明开源模型用 SGLang 自部署、结果随 API 或部署方式变化；本实验走企业网关到 `deepseek-v4-1-flash-261017`。在差距原因查清之前，`+0.0882` 应读作“相对本环境的裸跑”，不宜直接当作保底壳在论文环境下的增益。
- 每次运行的 token 用量两臂接近（`H_base` `28.4M`；`H_raw` 按 15 次算 `26.9M`、按 14 次算 `28.8M`），不能得出保底壳多耗或少耗 token 的结论。

### 7.5 `H_raw` 空白卷的结束方式（轨迹探查，2026-11-27）

> 由只读脚本在服务器上读取 `jobs/test-H_raw*/…/agent/trajectory.json` 得到，输出存于 `docs/server-info/test_export/hraw_probe.txt`；下列分类依据每次运行的最后两步和起止时间戳。

`[事实]`
- 两臂轨迹都带思考内容：每次运行 `reasoning_content` 合计 `3.2M–6.2M` 字符。`--effort max` 在本环境确实生效。
- `H_raw` 8 次 `missing_submission` 的最后一步：
  - 4 次是客户端合成消息 `API Error: Claude's response exceeded the 32000 output token maximum`，会话随即结束：`ajc-schmitt`（开跑后 1 h 50 min）、`programmable-icc`（2 h 17 min）、`unregulated-charge-pump`（2 h 48 min）、`local-two-stage-ota`（2 h 49 min）。
  - 2 次是 `Output token limit hit. Resume directly…` 的续写提示（`miller-opamp-gain70` 2 h 57 min、`ajc-rc-osc` 2 h 59 min）。
  - 2 次是在 3 小时处仍在调用工具（`beta-multiplier`、`local-fc-ota`）。
- `H_raw` 6 次有分数的运行：3 次以自己的收尾总结结束（`fct-residue-amplifier` 重跑、`ring-vco`、`regulated-charge-pump`），3 次在 3 小时处仍在调用工具。
- 上述以 API Error 结束的运行，最后一步之前的思考里写着“已过约 2 小时，需要尽快把版图搭出来”一类的话（`programmable-icc`、`unregulated-charge-pump`、`miller-opamp-gain70`）。
- `replay.jsonl`：13 次 `H*` 抢修重放中 4 次触发了棘轮晋升，平均每次 `43.9` 次模型请求。`H_base` 14 次中 10 次在 `T_soft` 被停、13 次触发抢修门、6 次在自己的 Phase B 晋升；网关等待占墙钟 `11.4%`。

`[判断]`
- 空白卷的直接机制：整个过程没有把版图写进 `submission/`（见下方第二次探查），其中 4 次是因为单次回复超过客户端 `32000` 输出 token 上限、会话中止而没来得及写。
- `[未核验]` 论文的 SGLang 自部署是否有同样的输出上限、思考量是否同样大，不知道；这是目前最具体的“部署差异”候选，但还不能断定它解释了与论文 `0.1974` 的差距。各臂的统计见下方第二次探查。

第二次探查（`docs/server-info/test_export/limit_probe.txt`，同为只读脚本读取轨迹）：

`[事实]`
- 输出超限的续写提示在所有臂都普遍出现：测试集 `H_raw` 14 / 14 次运行、共 133 次；测试集 `H_base` 14 / 14、共 113 次；dev `H_base` 22 / 24、共 125 次。以 API Error 结束的运行：`H_raw` 4 次，测试集 `H_base` 0 次，dev `H_base` 1 次。
- 每次运行都有单步思考约 10 万字符的情况（`think_max` `95k–109k`），超过 4 万字符的思考步每次运行有 `19–53` 个。
- `H_raw` 命令里第一次出现 `.gds` 的时间中位数是开跑后 `7.8` min；第一次出现 `submission/layout.gds` 的时间，6 次有分数的运行是 `47–179` min（其中 5 次在 `147` min 之后），8 次空白卷全程没有出现。空白卷集合与“全程未写 `submission/layout.gds`”的集合完全一致。

`[判断]`
- 上一条判断里“版图动工太晚”的说法不准确：早期就有 GDS 文件，但那可能只是器件级试验；能确定的是“交付太晚”，题面要求的“尽早保存到 `submission/`”没有被执行。
- 输出超限不是 `H_raw` 特有的；`H_base` 同样频繁撞到，只是有草稿送检和独立抢修兜住了后果。
- 这些现象是事后靠三轮手写探查才看到的：`H_base` 设计时、两轮提案时都没有这些信息，分数本身（多数停在 `0.1`）也反映不出卡点。LVS 为什么不过（13 / 14）至今没有分析过。
