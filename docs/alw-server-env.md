# ALW 评测服务器环境、三组模型网关与并发/代理安全规范

> **证据分层与保密约定**（遵循 [AGENTS.md](../AGENTS.md)）：
> - **`[事实]`**：直接来自目标评测宿主机实测探针输出与 `method-v5/experiments/alw/paper_reference.json`；
> - **`[判断]`**：基于实测资源与 MCR v2 协议锁定的并发上限、目录放置、子模型同质化与代理隔离方案；
> - **`[待核对]`**：尚未完成实测确认的 3 项前置核对（网关响应 `model` 字段静默换模、主实验模型最终拍板、`dockerd` 代理拉取与回环实测）。
> - **脱敏红线**：本文档严格使用 `<USER>`、`<INTERNAL-GROUP>`、`<GATEWAY_URL>` 占位符，**严禁写入任何真实主机名、IP、用户名、API Token 或含凭据的代理 URL**。

---

## 1. 宿主机硬件、存储与工具链实测档案

### 1.1 宿主机实测基线 `[事实]`

| 维度 | 实测观测结果 | 对 ALW 实验的工程约束 |
|---|---|---|
| **系统与 CPU** | `Linux 6.8.0-41-generic x86_64 GNU/Linux`；`Intel(R) Xeon(R) Platinum 8378A CPU @ 3.00GHz`，1 颗物理 CPU，`32` 物理核 / `64` 逻辑线程（`CPU(s): 64`） | 支持 `linux/amd64` 原生跑 ALW EDA 镜像；超线程 64 线程，物理核 32 核 |
| **共享负载与内存** | `up 123 days`，`20 users`，`load average: 2.28, 2.31, 2.37`；`Mem: 493Gi total, 234Gi used, 85Gi free, 143Mi shared, 179Gi buff/cache, 259Gi available`；**`Swap: 0B`（无交换分区！）** | **`259Gi` 可用且 `Swap: 0B`** 意味着一旦超出物理可用内存会**直接触发 OOM Killer（`exit_code == 137`）**，必须留足共享宿主机余量 |
| **磁盘分区** | `/`（`/dev/sda2`）：`492G` 总量，`13G` 已用，**`459G` 可用**（3%）；<br>`/home`（`/dev/sdb1`）：`4.0T` 总量，`761G` 已用，`3.0T` 可用（20%）；<br>`/localdata`（`/dev/sdc`）：`1007G` 总量，`537M` 已用，**`1007G` 可用**（1%） | `/localdata` 为近乎全空的 `1007G` 专用本地数据盘，最适合存放高 I/O 的 `$SSAP_STATE` 与 `$ALW_REPO` 任务运行产物 |
| **用户与基础工具** | `uid=1005 groups=<USER> sudo <INTERNAL-GROUP>`（具 `sudo` 权限）；已装 `git`、pyenv `python3.12`/`3.13`（默认 shim `3.13.14`）、`python3.11`、`uv`、`node v22.23.2`、`npm`、`curl`、`claude` | 须在安装脚本中显式调用 `python3.12` 建 venv，避免落入 `3.13` 默认环境或引发依赖差异 |
| **缺失关键组件** | **`docker`：MISSING**、`harbor`：MISSING、`alw`：MISSING；`docker0 not found`；`docker_access: NEEDS_PASSWORD_OR_STOPPED`；`ufw/firewalld` 状态未启用或需密码 | **Phase 0.0 必须先完成 Docker Engine + Compose v2 安装、`<USER>` 入 `docker` 组及 `harbor`/`alw` 安装** |
| **出网连通与代理** | `401 https://ghcr.io/v2/`（Registry 需匿名/鉴权挑战响应，网络可达）、`200 https://registry.npmjs.org/`、`200 https://pypi.org/simple/harbor/`、`200 https://github.com/`；宿主机环境已设 **含明文用户名密码的 `http_proxy` / `https_proxy`**、`no_proxy`/`NO_PROXY`（含 `localhost,127.0.0.1,::1,0.0.0.0`）、`NODE_TLS_REJECT_UNAUTHORIZED=0`、`NODE_USE_ENV_PROXY=1` | 存在带凭据出口代理与 TLS 拦截环境；必须做 3 层代理隔离与凭据脱敏，严防代理密码泄漏入容器日志或 Git |

### 1.2 共享宿主机并发上限与存储布局规划 `[判断]`

1. **严格并发上限（禁止 `N = 8`）**：
   - `[事实]` ALW 单并发 Trial 默认硬配额为 **`6 CPU / 32 GiB RAM`**（见全 40 题 `task.toml`）。
   - `[判断]` 考虑到当前宿主机处于 **20 人共享、`available ≈ 259Gi` 且 `Swap: 0B`** 的客观约束：
     - **默认推荐并发 `N = 4`**：占用 `24 CPU / 128 GiB RAM`，为其余 19 位在线用户、Harbor/Python 控制进程与宿主机内核缓冲稳定留出 `~131 GiB`（约 `50%` 可用内存余量）与 `40` 线程余量。
     - **安全并发天花板 `N = 6`**：占用 `36 CPU / 192 GiB RAM`，仍留 `~67 GiB` 缓冲，仅在宿主机低负载时段且观察 Phase 1b 实测峰值稳定后启用。
     - **严禁运行 `N = 8`**：`8 × 32 GiB = 256 GiB` 已紧贴 `259Gi` 可用上限，在 `Swap: 0B` 下任意 KLayout/ngspice 瞬时峰值或他用户突发负载都将直接引发批量 `OOM 137`（既污染 `infra_ledger`，又浪费算力）。
2. **存储与环境布局**：
   - 将 `$ALW_REPO` 与 `$SSAP_STATE` 全部置于 `/localdata/<USER>/`（`1007G` 空盘）：
     - `$ALW_REPO = /localdata/<USER>/AnalogLayoutWorld`
     - `$SSAP_STATE = /localdata/<USER>/ssap-alw-state`
   - `/home/<USER>/...` 仅保留轻量的 Git 工作树 `.worktrees/ssap`（绝对不存放 `var/` 或 Harbor `jobs/`）。
   - Docker `data-root` 默认使用 `/var/lib/docker`（位于 `/` 分区，`459G` 可用空间对 `1.88 GB` EDA 基础镜像及若干任务薄镜像绰绰有余）；若后续需构建多个本地包装镜像且 `/` 占用逼近 50%，再在 `/etc/docker/daemon.json` 中迁移至 `/localdata/docker`。

---
## 2. 模型网关、三组 API 档案与科学防污染规范

### 2.1 模型网关 `<GATEWAY_URL>` 与可用模型清单 `[事实]`

1. **协议与连通实测**：
   - 协议地址：明文 `http` 双协议网关 `<GATEWAY_URL>`，同时支持 Anthropic `/v1/messages` 与 OpenAI `/v1/chat/completions`。
   - 鉴权头：`Authorization: Bearer` 与 `x-api-key` 均返回 HTTP `200`（`GET /v1/models` 延迟 `~0.2s`，`POST /v1/messages` 延迟 `~1.3–1.5s`）。
   - 核心特性核验：`POST /v1/messages` + `stream: true` 返回真实 SSE（`event:` / `data:`）；`tools` + `tool_use` 透传正常；`POST /v1/chat/completions` 返回 HTTP `200`。
2. **模型目录（`/v1/models` 共返回 12 个模型，含 11 个已命名家族 + `spec/mine` 专属别名）**：
   - 通用家族 ID：`deepseek-v3.2`、`deepseek-v4-1-flash`、`deepseek-v4-flash`、`deepseek-v4-pro`、`doubao-seed-2.0-code`、`doubao-seed-2.0-pro`、`glm-5.3-flash`、`glm-5.2`、`kimi-k3`、`MiniMax-M3`、`qwen3.7-flash`。
   - `spec`/`mine` 专用别名：`GLM-5.3-spec`、`DeepSeek-V4-Flash-special`、`DeepSeek-V4.1-Flash-special`。
3. **三组宿主机预设 API 组（由 Shell 启动脚本 / 别名注入）**：

| API 组 | 激活方式 | 端点与鉴权 | 默认注入的模型槽位映射 |
|---|---|---|---|
| **Group 1（主配置，默认生效）** | Shell 默认环境变量 | `<GATEWAY_URL>` + `ANTHROPIC_AUTH_TOKEN`（Bearer） | `ANTHROPIC_MODEL=glm-5.3-flash`、`ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-1-flash`、`ANTHROPIC_DEFAULT_SONNET_MODEL=glm-5.3-flash`、`ANTHROPIC_DEFAULT_OPUS_MODEL=kimi-k3`、`CLAUDE_CODE_AUTO_MODE_MODEL`、`CLAUDE_CODE_BG_CLASSIFIER_MODEL`；附 `CLAUDE_CODE_TWO_STAGE_CLASSIFIER=false`、`DISABLE_NON_ESSENTIAL_MODEL_CALLS=1`、`API_TIMEOUT_MS=3000000` |
| **Group 2（`claude-spec`）** | `claude-spec` 别名 | 同 `<GATEWAY_URL>` + 独立 `ANTHROPIC_AUTH_TOKEN`（Spec 组 Key） | 覆写 `ANTHROPIC_MODEL`、`ANTHROPIC_SMALL_FAST_MODEL`、`ANTHROPIC_DEFAULT_OPUS_MODEL`、`ANTHROPIC_DEFAULT_SONNET_MODEL` $\to$ 指向 `GLM-5.3-spec` / `DeepSeek-V4-Flash-special` / `DeepSeek-V4.1-Flash-special` |
| **Group 3（`claude-mine`）** | `claude-mine` 别名 | 同 `<GATEWAY_URL>` + 独立 `ANTHROPIC_AUTH_TOKEN`（Mine 组 Key） | 覆写同上四个槽位 $\to$ 指向同一组 `-spec` / `-special` 模型 ID（两把独立 Key 共享同一模型 ID 集合） |

### 2.2 与 ALW 论文基准表的对照定位 `[事实 + 判断]`

对照 `method-v5/experiments/alw/paper_reference.json`（表 1，40 题 $\times 3$ 次均值）：
- **`deepseek-v4-1-flash`（或 `DeepSeek-V4.1-Flash-special`）+ `Claude Code 2.1.302`**：**直接精确对应论文表 1 中的 `DeepSeek V4.1 Flash / Claude Code` 行**（论文均值 `score=0.1974`、`acc=8.3%`、`drc=41.7%`、`lvs=29.2%`、`sim=17.5%`、`13.2M tokens/次`、`134.0` 工具调用）。全量基线与论文逐列可比、上升空间大（`0.1974 -> ~0.8+`）且最省 token。
- **`deepseek-v4-pro` + `Claude Code`**：对应论文 `DeepSeek V4 Pro / Claude Code`（`0.1504`、`23.8M tokens/次`）。
- **`glm-5.3-flash` / `GLM-5.3-spec` / `kimi-k3`**：在论文表 1 中分别测试于 `OpenCode`（`GLM-5.3 Flash`: `0.2555`、`31.7M tokens`，`51/120` 触及 3h 上限；`GLM-5.3`: `0.4779`、`34.5M tokens`）或 `Kimi Code`（`Kimi-K3`: `0.2900`、`12.2M tokens`）；若在本机通过 `Claude Code` 运行，**`H_raw` 与 `H_base` / `H*` 的对照组完全自洽，但不可直接按不同 Harness 跨行等同于论文 `OpenCode` 行**。

### 2.3 科学防污染核心规则：`Claude Code` 子模型同质化与多 Key 分片 `[判断]`

1. **CRITICAL — `Claude Code` 子模型同质化规则（Sub-Model Homogeneity Rule）**：
   - **风险事实**：宿主机 Group 1 默认将 `Haiku/Sonnet/Opus` 分别映射至 **3 个完全不同的底层模型**（`deepseek-v4-1-flash` / `glm-5.3-flash` / `kimi-k3`）；而 Group 2/3 若仅覆写 4 个变量却未先清掉 Group 1 的 `ANTHROPIC_DEFAULT_HAIKU_MODEL`、`CLAUDE_CODE_AUTO_MODE_MODEL`、`CLAUDE_CODE_BG_CLASSIFIER_MODEL`，子代理或后台分类器仍会悄悄落回其它模型。
   - **强制契约**：`ssap` 的 `harbor_runner.py` 与 Phase B `claude -p` 包装器在启动任何容器任务前，必须采用白名单环境清理宿主机继承变量（剥离全部 `ANTHROPIC_*`、`CLAUDE_*`、`AWS_*`、`MAX_THINKING_TOKENS`、`DISABLE_PROMPT_CACHING` 及 `*_PROXY`/`*_proxy`），再将 **全部 8 个模型槽位强制钉死为同一锁定实验模型 `$SSAP_MODEL`**（含 Harbor `claude_code.py:1880` 使用的 `CLAUDE_CODE_SUBAGENT_MODEL`）：
     - `ANTHROPIC_MODEL="$SSAP_MODEL"`
     - `ANTHROPIC_SMALL_FAST_MODEL="$SSAP_MODEL"`
     - `ANTHROPIC_DEFAULT_HAIKU_MODEL="$SSAP_MODEL"`
     - `ANTHROPIC_DEFAULT_SONNET_MODEL="$SSAP_MODEL"`
     - `ANTHROPIC_DEFAULT_OPUS_MODEL="$SSAP_MODEL"`
     - `CLAUDE_CODE_SUBAGENT_MODEL="$SSAP_MODEL"`
     - `CLAUDE_CODE_AUTO_MODE_MODEL="$SSAP_MODEL"`
     - `CLAUDE_CODE_BG_CLASSIFIER_MODEL="$SSAP_MODEL"`
     - 并同步锁定：`DISABLE_NON_ESSENTIAL_MODEL_CALLS=1`、`CLAUDE_CODE_TWO_STAGE_CLASSIFIER=false`、`API_TIMEOUT_MS=3000000`；宿主机必须启用零缓存透传计量代理（容器内同时兼容 Harbor 注入的 `ANTHROPIC_API_KEY` [`x-api-key`] 与 `ANTHROPIC_AUTH_TOKEN` [`Bearer`] 临时每 Trial 令牌，代理向 `<GATEWAY_URL>` 按可配置 `upstream_auth_header`/`upstream_auth_prefix` 注入真实上游密钥；多 Key 按单次 Trial 粘性均衡绑定，并逐请求记录响应体 `"model"` 字段；`gateway_wait_seconds` 精确定义为请求发出至首字节到达 TTFB 加 `429/5xx` 重试等待秒数）。
2. **多 Key 并发分片与跨组混用禁令（Multi-Key Sharding & Anti-Contamination）**：
   - **允许的分片**：`claude-spec`（Group 2）与 `claude-mine`（Group 3）共享完全一致的模型 ID（`GLM-5.3-spec`、`DeepSeek-V4-Flash-special`、`DeepSeek-V4.1-Flash-special`）而各自拥有独立 API Token。当实验锁定使用某一 `-spec`/`-special` 模型时，宿主机零缓存计量代理可按并发槽位或轮询策略在 `SSAP_UPSTREAM_KEYS`（`spec` + `mine` 两把 Key）间分片转发，从而有效压低高并发下的限流排队与 `gateway_wait_seconds`（防误触 `theta_gw_max = 0.35`）。
   - **绝对禁止的混用**：**严禁在同一实验或任意配对比较（`H_raw` vs `H_base` vs `H_inc` vs `H*`）中混用 Group 1 的常规模型 ID（如 `deepseek-v4-1-flash`、`glm-5.3-flash`）与 Group 2/3 的 `-spec`/`-special` 别名（如 `DeepSeek-V4.1-Flash-special`、`GLM-5.3-spec`）**，除非预先经实测核验两者在网关后端指向同一权重与采样参数并书面冻结确认。

---
## 3. Phase 0.0 前置环境补齐与含凭据代理安全隔离清单

### 3.1 Phase 0.0：Docker Engine、Compose v2 与 Python 3.12 环境补齐 `[判断]`

针对实测中 `docker / harbor / alw` 缺失且默认 Python shim 为 `3.13.14` 的现状，在进入 `alw-progressive-plan.md` Phase 0.1 前先执行本节步骤（以下均为交互式 `sudo` 操作，**不要将任何代理凭据写入仓库或日志**）：

```bash
# 1. 安装官方 Docker Engine + Docker Compose v2（若 apt 需走出口代理，用 sudo -E 或配置 /etc/apt/apt.conf.d/ 私有根权限文件）
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
# 按 Ubuntu 官方 Docker APT 源安装 docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# 或使用已审核的发行版仓库（注意 Ubuntu 24.04 下 docker-buildx 为独立包，BuildKit --secret 与 Harbor docker compose build 均依赖它！）：
sudo apt-get install -y docker.io docker-buildx docker-compose-v2

# 2. 配置非 root 免密访问（Harbor 内部直接调用 `docker` CLI，不可依赖每次交互输密码）
sudo usermod -aG docker <USER>
# （可选：若后续镜像体积逼近 / 空间警戒，可在 /etc/docker/daemon.json 设置 {"data-root": "/localdata/docker"}）

# 3. 安装完成后重新登录 shell（或执行 newgrp docker），验证无密码 docker、buildx、compose 与 docker0 网桥就绪：
docker info --format 'server={{.ServerVersion}} cgroup={{.CgroupVersion}}'
docker buildx version
docker compose version

# 4. 固定目录与 Python 3.12 环境初始化（/localdata 1007G 专用盘）
mkdir -p /localdata/<USER>/ssap-alw-state
git clone https://github.com/Nova-Silicon/AnalogLayoutWorld.git /localdata/<USER>/AnalogLayoutWorld
git -C /localdata/<USER>/AnalogLayoutWorld checkout d5236c886fefb5f5cedb0c755f3b3a949f1f2423
cd /localdata/<USER>/AnalogLayoutWorld
/usr/local/pyenv/shims/python3.12 -m venv .venv
source .venv/bin/activate
python --version                               # 预期：Python 3.12.x（绝非 3.13.14）
python -m pip install -e '.[harbor]'
python -m pip install -e /home/<USER>/silicon-notebook/benchmark-deepeval/.worktrees/ssap
alw list | wc -l                               # 预期：40
```

### 3.2 含凭据 `http_proxy` / `https_proxy` 三层安全隔离规范 `[判断]`

当前宿主机 `http_proxy` / `https_proxy` **内嵌明文代理用户名与密码**，而容器内既要拉取 `ghcr.io` / 安装 npm 包，又要在运行期直连宿主机 `:8791` 透传代理（`<GATEWAY_URL>` 为内网明文 `http` 地址）。必须严格按以下三层隔离，缺一不可：

1. **第一层（`dockerd` + `containerd` 守护进程代理：`root` 专属 `chmod 600` 与 `%`/`$` 转义防护）**
   - **关键防踩坑说明**：在 Ubuntu `docker.io` / containerd 镜像拉取栈中，镜像层拉取同时经过 `dockerd` 与 `containerd.service`；此外若代理密码含 URL 编码 `%`（如 `%40`）或 `$`，未单引号保护的 Shell 会展开 `$`，而 systemd `Environment=` 会将单个 `%` 当作单元占位符（specifier）破坏凭据！
   - 因此统一使用单引号/Python 从当前安全环境变量生成两处 `root:root 0600` 配置（**切勿提交到仓库**）：
     1. `/etc/docker/daemon.json`（权限 `0600`，写入顶层 `"proxies": {"http-proxy": "...", "https-proxy": "...", "no-proxy": "localhost,127.0.0.1,::1,172.17.0.0/16,host.docker.internal"}` —— JSON 原生承载原始字符串，无 `%` 占位符问题；注意仅设守护进程顶层 `proxies`，不要写 `~/.docker/config.json` 的客户端容器注入 `proxies`！）；
     2. `/etc/systemd/system/docker.service.d/proxy.conf` **与** `/etc/systemd/system/containerd.service.d/proxy.conf`（权限均锁死 `root:root 0600`，将密码/URL 中所有 `%` 转义为 **`%%`**）：
        ```ini
        [Service]
        Environment="HTTP_PROXY=http://<PROXY_USER>:<PROXY_PASS_WITH_%%_ESCAPED>@<PROXY_HOST>:<PORT>"
        Environment="HTTPS_PROXY=http://<PROXY_USER>:<PROXY_PASS_WITH_%%_ESCAPED>@<PROXY_HOST>:<PORT>"
        Environment="NO_PROXY=localhost,127.0.0.1,::1,172.17.0.0/16,host.docker.internal"
        ```
   - 执行 `sudo systemctl daemon-reload && sudo systemctl restart containerd docker`，仅用于守护进程拉取 `ghcr.io` EDA 基础镜像；普通容器默认不会继承守护进程的 systemd 环境变量，天然阻断代理凭据进入容器 `/proc/*/environ`。
2. **第二层（容器内 `@anthropic-ai/claude-code@2.1.302` 安装与运行期 `NO_PROXY` 防回环）**
   - **唯一合规方案（预烘焙轻量本地 Agent 包装镜像，严禁向 Harbor `env` 透传含凭据 `http_proxy`）**：
     因 Harbor `utils/env.py` 序列化 `config.json` 时仅脱敏匹配 `KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH` 的键名，若将含 `user:pass@` 的 `http_proxy` 传入 Harbor `agents[].env` 或 `environment.env`（原备选方案 B）会导致明文代理密码直接落盘至 `config.json`（详见 [fable-5.2-plan-review.md](fable-5.2-plan-review.md) A3）。因此**禁止向 Harbor 传递运行期 `http_proxy`/`https_proxy`**；统一基于固定摘要 `ghcr.io/nova-silicon/analoglayoutworld@sha256:f922e79ebbac026ed9d15987924867ba6fef4270a5c4994579500e9138bf8338` 构建本地薄层镜像 `alw-eda-claude:2.1.302`（需确保已装 `docker-buildx`，使用 `DOCKER_BUILDKIT=1 docker build --network=host -f container/alw/Dockerfile.agent --secret id=build_proxy,src=... -t alw-eda-claude:2.1.302 .` 构建：当检测到 `/run/secrets/build_proxy` 非空时，在 `RUN` 子进程内面对企业 TLS 拦截代理同步启用 `npm_config_strict_ssl=false` + `--strict-ssl=false` + `NODE_TLS_REJECT_UNAUTHORIZED=0` 且**不设 `NODE_USE_ENV_PROXY=1`**——因 `npm` 内置 `make-fetch-happen` 显式传入 `rejectUnauthorized: strictSSL` 会覆盖单纯的 `NODE_TLS_REJECT_UNAUTHORIZED=0`；镜像层零残留代理或 TLS 变量，Harbor 配置 `kwargs.version="2.1.302"` 跳过联网安装并通过生成的 Compose 覆盖层设置 `services.main.build.args.ALW_BASE_IMAGE`）。Agent 容器运行期仅保留 `NO_PROXY=host.docker.internal,172.17.0.1,localhost,127.0.0.1,::1,0.0.0.0`；**独立断网 Verifier 容器与离线 `alw verify` 仍原样使用未修改的原始摘要镜像**，保证验收集 100% 等价。
3. **第三层（全链路凭据脱敏与防泄漏清洗）**
   - `ssap` 的 `harbor_runner.py`、`artifact_summary.py` 与 `scripts/preflight_alw.sh` 在落盘 `config.json`、`artifact_summary.{json,md}`、Harbor `agent/` 日志摘要或命令行回显前，必须强制剥离全部 `*_PROXY`/`*_proxy`、`ANTHROPIC_API_KEY`、`ANTHROPIC_AUTH_TOKEN`、`SSAP_UPSTREAM_KEY`、`SSAP_UPSTREAM_KEYS` 等字段，同时执行宽协议凭据正则（`r'([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s@]+@'` $\to$ `\1<REDACTED>@`）与字面量密钥值双重脱敏过滤。
   - 所有运行数据目录（`var/`、`$SSAP_STATE`、Harbor `jobs/`）严禁置于受版本控制的目录中。

---

## 4. 待核对小项 `[待核对]`

1. **`[待核对]` 网关静默换模检验**：用最小 `POST /v1/messages` 请求逐一核查 `deepseek-v4-1-flash`、`DeepSeek-V4.1-Flash-special`、`glm-5.3-flash`、`GLM-5.3-spec` 的响应体 `"model"` 字段及系统指纹，确认是否存在后台静默重定向至异构模型，以及 `deepseek-v4-1-flash` 与 `-special` 是否等价。
2. **`[待核对]` 主实验冻结模型拍板**：在 Phase 1a 开跑前从三者中最终锁定唯一实验模型——若追求与 ALW 论文表 1（`0.1974 / 13.2M tokens`）直接逐列对齐且配额充足，建议优先选 **`deepseek-v4-1-flash`**（或经核对等价且可利用 Group 2+3 双 Key 并发分片的 **`DeepSeek-V4.1-Flash-special`**）；若实验主目标为观测更高基线能力上的提升，备选 **`glm-5.3-flash`** / **`GLM-5.3-spec`**。
3. **`[待核对]` `dockerd` 代理拉取与宿主机到容器回环实测**：完成 Phase 0.0 Docker 安装并配置 `root 0600` 的 `proxy.conf` 后，实测 `docker pull ghcr.io/nova-silicon/analoglayoutworld@sha256:f922e79e...` 能否跳过 `401` 匿名挑战正常下载镜像层，以及容器内 `curl` 访问 `http://host.docker.internal:8791` 是否不走 `http_proxy` 直通宿主机。
