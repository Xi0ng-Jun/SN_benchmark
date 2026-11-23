# ALW 服务端部署实录、全链路验证与排障手册（Phase 0.0 $\to$ Phase 1b）

> **文档说明与保密约定**（遵循 [AGENTS.md](../AGENTS.md)）：
> - 本文记录 `ssap`（分支 `feat/ssap`，提交 `ae14d9f`）与 `AnalogLayoutWorld`（提交 `d5236c8`）在目标评测服务器上从零完成 **Phase 0.0（环境补齐）$\to$ Phase 0.4（零模型费真容器验证）$\to$ Phase 1a（`smoke10m-run1` 10 分钟带电全链路烟雾）$\to$ Phase 1b（`phase1b-3tasks` 3 题 × 3h `H_base` 实测与 pre-Phase-1c 锁定修复，§4）** 的完整实战过程与 6 个真实故障的标准解法。
> - **严格脱敏**：文中所有宿主机名、账号、内网组名、镜像源及网关/代理地址均使用占位符 `<USER>`、`<HOST>`、`<INTERNAL-GROUP>`、`<INTERNAL-MIRROR>`、`<GATEWAY_URL>`，零真实用户名、零密钥、零内网 IP（仅保留标准 Docker 网段与公开 `ghcr.io` 报错原话中的公网端点）。
> - `Phase 1a`（`smoke10m-run1`）实测数据均逐字段核对自 `docs/server-info/{launch_results.json,artifact_summary.json,metrics.json}`。
> - `Phase 1b`（`phase1b-3tasks`，3 题 × 3h `H_base`，见 §4）实测数据由脚本逐字段抽取自 `docs/server-info/out.json`（`lr` / `asum` / `met`）与 `docs/server-info/forensic.json`（`fork` / `trace` / `err` / `hand` / `lvs` / `gds`）；`docs/server-info/` 为服务端原始导出，已 gitignore，不入库。
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

| 指标 | `adb-ota-5t-gain40-pm60-noise50uv-pvt` | `local-comparator` | `adb-buf-ppsf-bs-gain1-sfdr90` |
|---|---|---|---|
| exit_code (launcher) | `0` | `0` | `0` |
| proxy_requests | `226` | `319` | `363` |
| input_tokens | `12036810` | `20996697` | `26317116` |
| output_tokens | `1623678` | `1404099` | `1337439` |
| cached_tokens | `2108288` | `2682880` | `3613696` |
| gateway_wait_seconds | `795.0` | `979.3` | `1215.5` |
| llm_stream_seconds | `8760.6` | `7697.5` | `7495.9` |
| http_429 / http_5xx / retries | `0 / 0 / 0` | `0 / 0 / 0` | `0 / 0 / 0` |
| unrecovered_gateway_error (errors) | `True (1)` | `True (1)` | `True (1)` |
| model_swap | `True` | `True` | `True` |
| upstream_models_seen | `deepseek-v4-1-flash-261017` | `deepseek-v4-1-flash-261017` | `deepseek-v4-1-flash-261017` |
| phase_a seconds | `9900.029` | `9525.751` | `9734.064` |
| phase_a timed_out / returncode | `True / 143` | `False / 0` | `False / 0` |
| phase_a_elapsed_sec | `9900.083` | `9525.804` | `9734.118` |
| remaining_sec (at fork) | `899.917` | `1274.196` | `1065.882` |
| repair_eligible | `True` | `True` | `True` |
| granted_repair_budget_sec / turns | `779.917 / 15` | `900.0 / 15` | `900.0 / 15` |
| phase_b returncode / timed_out | `1 / False` | `1 / False` | `1 / False` |
| phase_b seconds | `167.831` | `57.565` | `200.844` |
| phase_b used % of budget | `21.5` | `6.4` | `22.3` |
| phase_b unused % of budget | `78.5` | `93.6` | `77.7` |
| best_sha256 changed | `False` | `False` | `False` |
| m_pre | `None` | `[1, 0, 1, 0, 0, 1]` | `[1, 0, 1, 0, 0, 1]` |
| m_post | `None` | `[1, 0, 1, 0, 0, 1]` | `[1, 0, 1, 0, 0, 1]` |
| gate_triggered / reasons | `True / m_best_not_all_certified` | `True / m_best_not_all_certified` | `True / m_best_not_all_certified` |
| ratchet_promoted / promote_reason | `False / no_new_candidate` | `False / no_new_candidate` | `False / no_new_candidate` |
| infra_error (summary) | `False` | `False` | `False` |
| outer_metrics.Y_star (summary) | `None` | `None` | `None` |
| pre==post==submission gds sha | `True` | `True` | `True` |
| verifier status | `missing_submission` | `evaluated` | `evaluated` |
| verifier score | `0.0` | `0.1` | `0.1` |
| score_components | `gds=0.0,lvs=0.0,drc=0.0,area=0.0,quality=0.0,postsim=0.0` | `gds=0.1,lvs=0.0,drc=0.0,area=0.0,quality=0.0,postsim=0.0` | `gds=0.1,lvs=0.0,drc=0.0,area=0.0,quality=0.0,postsim=0.0` |
| drc_pass / lvs_pass / pex_success | `False / False / False` | `True / False / False` | `True / False / False` |
| container wall_sec | `10067.992` | `9583.487` | `9941.971` |
| tool_cpu_sec | `381.163` | `840.959` | `1028.113` |
| peak_ram_mb | `462.9` | `464.2` | `527.3` |
| gateway_wait / wall | `7.9%` | `10.2%` | `12.2%` |
| handoff status | `None` | `in_progress` | `in_progress` |
| lvs (forensic) | `None` | `False (klayout returned 1)` | `False (LVS mismatch)` |
| #gds files | `7` | `24` | `50` |
| watermarks t_warn / delta_s_rep | `9000.0 / 15` | `9000.0 / 15` | `9000.0 / 15` |

### 4.3 观测与根因（事实 / 判断分列）

**(a) 代理计量与 `unrecovered_gateway_error`**
- `[事实]` 3 次运行 `exit_code = 0`，`http_429_count = 0`、`http_5xx_count = 0`、`retries = 0`，但 `unrecovered_gateway_error = true`（`unrecovered_gateway_errors = 1`）三题全中。合计 `proxy_requests = 908`，`input_tokens = 59350623`，`output_tokens = 4365216`，`cached_tokens = 8404864`（脚本对 `lr[*].proxy` 求和）。
- `[事实]` `gateway_wait / wall` 为 `7.9% / 10.2% / 12.2%`，均低于 `theta_gw_max = 0.35`，未触发 `gateway_stall`；`model_swap = true` 且 `upstream_models_seen` 单一，无上游模型漂移。
- `[事实]` 容器内 `artifact_summary` 写的是 `infra_error = false`，`cost_ledger.{input,output,cached}_tokens = null`，`outer_metrics.Y_star = null`：容器内摘要看不到宿主机代理计量和外层 Verifier。
- `[判断]` 根因：旧版 `MeteringProxy._relay` 只对上游读取做了异常处理，没有保护对客户端 socket 的 `send_response` / `end_headers`，也没有保护 chunked 结尾 `0\r\n\r\n` 的写入。客户端（`claude`）在 SSE `message_stop` 之后立即退出（Phase B `--max-turns` 用尽），或在 `T_soft` 被 SIGTERM，都会在这几处抛 `BrokenPipe` / `ConnectionReset`。这些异常落进上游错误分支，被记成 `unrecovered_gateway_error`。按 `infra_reasons` 的规则，`infra_ledger` 会把这 3 次有效运行整体判为 `infra_error` 并丢弃，这与 `H_base` 中“基础设施故障与任务失败分账”的不变式冲突。“每题恰好 1 次”与“每个 Trial 结束时恰好 1 次客户端断开”相符，`429` / `5xx` / 重试全为 0 也排除了真实网关故障。

**(b) Phase A 收尾与 `T_warn`**
- `[事实]` `adb-ota-5t-…` 的 Phase A 在 `T_soft` 处被终止：`phase_a.timed_out = true`，`returncode = 143`，`seconds = 9900.029`。`local-comparator` 与 `adb-buf-ppsf-…` 自行结束：`timed_out = false`，`returncode = 0`，`seconds = 9525.751 / 9734.064`，分别在 `T_warn = 9000s` 之后 `525.8s / 734.1s`。`adb-buf-ppsf-…` 收尾时清理了 `stray_processes_terminated = 3` 个残留进程。
- `[事实]` 当时 `T_warn = 9000s` 到 `T_soft = 9900s` 只有 `900s = 15 min`。两份 `handoff` 的 `status` 均为 `"in_progress"`。`adb-ota-5t-…` 的工作区里没有 `submission/` 和 `.mcr/best/` 下的 GDS（`forensic.gds` 只有 7 个 `work/` 文件），因此 `m_pre = null`，Verifier 结果为 `missing_submission`，`0.0` 分。
- `[判断]` 15 分钟的预警窗口太短，智能体来不及在 `T_soft` 前把草稿提升为合法提交，也来不及写完交接。把 `T_warn` 提前到 `8400s` 后，离 `T_soft` 有 25 min，离 `T_max` 有 40 min。

**(c) Phase B 修复调用**
- `[事实]` 3 次 Phase B 均以 `returncode = 1`、`timed_out = false` 结束，用时 `57.565s – 200.844s`。按 `granted_repair_budget_sec`（`779.917 / 900.0 / 900.0`）计，未用掉的预算分别是 `612.1s / 842.4s / 699.2s`（`78.5% / 93.6% / 77.7%`）。`best_sha256_before == best_sha256_after`，`ratchet_promoted = false`（`promote_reason = "no_new_candidate"`）。`adb-buf-ppsf-…` 的 `fork_meta.pending_promotion = {"promoted": false, "reason": "same_tier_no_improvement"}`。
- `[事实]` 三题 Phase B 的 stderr 完全相同：`[claude-code:unrecognized_model] {"model":"ExternalAPI-Physical-Design-deepseek-v4-1-flash-volcengine-public","query_source":"sdk"}`。`adb-ota-5t-…` 与 `local-comparator` 的 Phase B stream-json 尾部 assistant 记录的 `model` 为 `deepseek-v4-1-flash-261017`；三题最后一条记录均带 `"stop_reason":"tool_use"`。
- `[事实]` `fork_meta.phase_a_argv` 含 `--settings /tmp/claude-code-settings/settings.json`。这是 Harbor 的临时路径，Phase B 原样沿用了它。
- `[判断]` `unrecognized_model` 只是 CLI 对非 Anthropic 模型名的提示，不是失败原因：请求实际已到达上游并返回。Phase B 是被 `--max-turns 15` 截断的，不是超时：轮数先用完，客户端在最后一条工具调用后以 `rc = 1` 退出。`[未核验]` 由于 `pb` 中每行只保留了 250 字符，结果记录的 `subtype`（是否为 `error_max_turns`）看不到。15 轮只用掉 1–3.5 min，大部分 `Delta_T_rep = 900s` 时间预算闲置。

**(d) 签核结果**
- `[事实]` `local-comparator` 与 `adb-buf-ppsf-…`：`status = "evaluated"`，`score = 0.1`（只有 `gds = 0.1`，其余分量为 0），`drc_pass = true`，`lvs_pass = false`，`pex_success = false`，`m_pre = m_post = [1, 0, 1, 0, 0, 1]`，且 pre / post / submission 三个 GDS 的 sha256 相同。`forensic.lvs` 的失败原因分别为 `"klayout returned 1"` 与 `"LVS mismatch"`。`local-comparator` 交接中的 `known_blockers` 第一条是 “signal routing not implemented”。
- `[事实]` 两题都是 `drc_pass = true` 但 `score_components.drc = 0.0`（对齐 `alw-mcr-instantiation.md` §1.1 核验的 `_scoring_core.py` 公式 $0.10\,I_{\mathrm{G}} + I_{\mathrm{L}}(0.10 + 0.10\,I_{\mathrm{D}})$：`lvs_pass = false` 时 DRC 分项按规则门控归零）。

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
