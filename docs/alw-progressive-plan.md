# ALW 渐进式上机迭代方案、代码架构与算力预算规划（MCR v2）

> **证据分层约定**（遵循 [AGENTS.md](../AGENTS.md)）：
> - **`[事实]`**：直接核验自 `docs/mcr-formal.tex`、`docs/mcr-plain.md`、`docs/alw-mcr-instantiation.md`、`external/AnalogLayoutWorld/repo`（提交 `d5236c8`）、`method-v5/experiments/alw/paper_reference.json` 及 `method-v5/docs/review-2026-11.md` 的源码或解析数值；
> - **`[判断]`**：面向可复现工程落地与 MCR v2 协议的架构选型与判据设计；
> - **`[暂定]`**：尚未在真实 Docker + 大模型服务端上机实测的运行期参数与资源消耗预估（如实际平均耗时、触发率、平均修复 token 等），须在 Phase 0/1 实测后按观测校准锁定。

---

## 0. 前置对齐契约与 `method-v5` 遗留机制取舍

本方案与形式化实例规范 [alw-mcr-instantiation.md](alw-mcr-instantiation.md) 及宿主机环境规范 [alw-server-env.md](alw-server-env.md) 共享完全一致的接口契约，是该规范的可执行工程落地路线图。

### 0.1 已锁定的跨文档对齐参数 `[事实 + 判断]`

| 接口维度 | 锁定契约（与 `alw-mcr-instantiation.md` 100% 一致） |
|---|---|
| **任务切分与防泄漏** | `configs/alw/split.json`（种子 `ssap-alw-v2-19`），按 32 个保守簇（严格覆盖 35 个硬簇）零跨切分泄漏划分：`dev = 12` 题（`G_dev = 10` 簇，`binned=3`，`magic_netgen=1 [#2]`：`2, 7, 11, 12, 13, 15, 16, 18, 19, 21, 27, 38`）；`val = 14` 题（`G_val = 14 硬 / 12 保守`，`binned=3`：`1, 3, 5, 6, 8, 17, 20, 22, 25, 26, 34, 35, 36, 37`）；`test = 14` 题（`G_test = 11 硬 / 10 保守`，`binned=3`，`magic_netgen=1 [#10]`：`4, 9, 10, 14, 23, 24, 28, 29, 30, 31, 32, 33, 39, 40`）。因 $G_{\mathrm{dev}}=10 \ge 8$，Stage 0 启用**内部盲簇模式（冻结 3/10 个 `dev` 簇）**。 |
| **保底镜像向量 $\mathbf{m}_{\mathrm{base}}$** | 6 维第 I 类镜像组件（唯一具覆写权，附 Tier 0 非空文件保底与 `T=0` `.mcr/trusted_task/` 只读隔离；详见 [fable-5.2-plan-review.md](fable-5.2-plan-review.md)）：Tier 1 字典序 `m_1`（`m_gds`，无 symlink + KLayout **38** 制造层正面积 bbox 非空 + 精确 `top_cell`；`src/*.py` 缺失列为第 II 类告警以确保 $C^\star=1\implies m_1=1$）；Tier 2 字典序 `m_2`（`m_lvs`，由 `eda_env.sh` 是否导出 `ALB_NETGEN_BIN` 区分 KLayout `sky130.lvs` vs `#2/#10` Magic+Netgen，并由 Harbor `stdin` 捕获的 `.mcr/task_prompt.txt` 与引号/层次感知 `circuit.spice` 互校确定 `top_cell/ports/lvs_sub`）；Tier 3 帕累托序 `m_3..m_6`：`m_3`（`m_shape`: $q \le 4.0-\epsilon_3$，另设第 II 类建议 `advisory_aspect_gt_3` 提醒 $3.0<q\le 4.0$）、`m_4`（`m_fill`: $\hat{\rho}\ge 0.10+\epsilon_4$，按提取 `WL+AS+AD` 计算）、`m_5`（`m_morph`: 无标注 `meaningless` 筛查，`magic_netgen` 为 1）、`m_6`（`m_drc`: `sky130A_mr.drc` 违例 $=0$）；同层级按 `mcr-formal.tex` 规则 2(b) 非严格 `>=`（已过二值门裕量饱和于 `0.0`）晋升，允许后仿真修复自由更新。 |
| **容器工作区与快照路径** | 自由草稿区 `/alb-workspace/work/draft/{layout.gds, src/}`（零步级强制回滚）；规范提交区 `/alb-workspace/submission/{layout.gds, src/}`（`layout.gds` 经 `os.replace()` 原子替换，`src/` 经暂存目录 `src -> src.old -> 改名 -> 清理` 切换）；内核状态 `/alb-workspace/.mcr/{input_hashes.json, task_prompt.txt, best/, staging/, handoff.json, gate_report.json}`；宿主工具挂载 `/opt/harness/{settings.json, bin/{mcr_entrypoint.py, mcr_hook.py, mcr_check, mcr_promote}, python/ssap/}`；导出快照 `/logs/agent/mcr_snapshot/{pre_repair/, post_repair/, artifact_summary.json, artifact_summary.md}`（Harbor 导出至 `<trial>/agent/mcr_snapshot/`，可选配宿主机挂载防 `SIGKILL` 丢失）。 |
| **双水位线与无状态补救** | 已核验容器硬上限 `T_max = 10800s`、外层 Verifier `3600s`（内部软预算 `ALB_VERIFIER_BUDGET_SEC=3300`）、单并发 `6 CPU / 32 GiB`。暂定 `H_base` 水位线：`T_warn = 9000s`（150 min）、`T_soft = 9900s`（165 min），预留 `Delta_T_rep = 900s`、`Delta_S_rep = 15` 轮。Phase A 结束且 `T_max - elapsed >= 180s` 时，若 `m_base(w_best) != (1,1,1,1,1,1)` 或存在未晋升新版草稿或命中 `advisory_aspect_gt_3`，启动独立无状态 `claude -p`（**禁用 `--resume`**）进入 Phase B。 |
| **`infra_error` 与纯 `C1` 红线** | `infra_error` 判据：OOM（`exit_code == 137`）、Docker/Harbor 异常、`evaluation_error == true` 或非提交故障的 `tool_error/interrupted`、未恢复网关 `5xx/429`、`proxy_requests == 0`、或 `gateway_wait_seconds / wall_seconds > theta_gw_max`（暂定 `0.35`）。任务失败记 `0.0` 分从不剔除。**严禁**任何装饰性统计否决门（零 `W_i`、零 `0.49/|B|`、零 McNemar、零 `n_10<=1`、零 `p<=0.05` 门槛、零 `P-SHA`）。 |

### 0.2 相对于 `method-v5` 的保留与删除清单 `[事实 + 判断]`

| 组件 / 机制（参考 `method-v5/experiments/alw/RUNBOOK.md`） | `ssap` 处理 | 技术依据（来源） |
|---|---|---|
| 请求级录制重放代理（`RecordingProxy` 缓存、`keys.py`、`session.py`、`O(T^2)` SQLite blob 库） | **彻底删除** | ALW 工具读写有状态磁盘、KLayout 双线程非确定、后处理钩子可在无新模型请求下改写 GDS（`review-2026-11.md` #11/#21/#29/#31）；且重放不省工具耗时。 |
| `E0 replay-check` 与 `E2` 轨迹二分修补（~40 次全量运行） | **彻底删除** | 替换为零调用 `Phase 0` 离线镜像一致性核查 + `Phase 1` 零调用四项体检 + `Phase 3` 终局快照分叉重放。 |
| `enforce_virtual_deadline` 虚拟时间累计 | **彻底删除** | 并发调用导致虚拟计时失真（`review-2026-11.md` #10）；改用 `mcr_entrypoint.py` 真实墙钟双水位线。 |
| `c=1.64` + 符号检验 `p<=0.10` / 混合分 `W_i` / McNemar 门槛 | **彻底删除** | 替换为纯净 `C1` 配对选模（$\hat{\Delta}_{\mathrm{val}}(Y^\star) > 10^{-6}$）+ 密封 `test` 三段归因。 |
| `method-v5/experiments/alw/split.json` | **废弃替换** | 旧切分含 3 组硬拓扑跨切分泄漏且 `dev` 含 `0/12` 个 `binned` 题；替换为 `ssap-alw-v2-19`。 |
| `preflight.sh` 宿主机硬件/Docker/网关鉴权自检 | **保留升级** | 升级为 `scripts/preflight_alw.sh`，增设 `sudo` Docker 检测、盘空间检查与无缓存网关探活。 |
| `compose.host-gateway.yaml` 与 `launch_env_clear` | **轻量保留（去缓存）** | 仅用于可选的**零缓存透传计量代理**（隔离宿主机 API 密钥防泄漏入容器 [复审 #22]，并精确记录 `gateway_wait_seconds` 与 token 账本，不做任何请求缓存/重放）。 |
| `catalog.py`、`metrics.py`、`paper_reference.json`、`VERIFIER_ENV` | **保留重实现** | 仅读取 ALW 仓库公开配置与 `metrics.json` 数据字段，严禁从 `ssap` 包 `import eda` 或 `import analoglayoutworld`。 |

---
## 1. 从 0 到跑通第 1 轮迭代的渐进路线图（Phase 0 $\to$ Phase 3）

设计原则：**先零模型费核通容器与签名，再单题短超时探活，再全量 `dev` 体检并按诊断开关裁减模块，最后仅针对高收益位点运行第 1 轮闭环迭代**。任一阶段结束均可直接停机并交付可验证产出。

### Phase 0：不调模型容器烟雾与环境核查（0 LLM Token）

- **目标**：在花出第 1 个模型 token 前，彻底打通宿主机 Docker 权限、EDA 镜像、`harbor` 与 `alw verify` 链路，并在本地/离线容器内验证 `H_base` 镜像检查器、原子写穿透棘轮与切分防泄漏不变量。
- **核心动作**：
  0. **Phase 0.0 前置环境补齐（仅针对首次上机宿主机，`[事实 + 判断]`）**：按 [alw-server-env.md](alw-server-env.md) 第 3 节先补齐当前缺失的原生 Docker Engine + Compose v2、将 `<USER>` 加入 `docker` 组、配置仅 `root 0600` 可读的 `dockerd` systemd 代理 drop-in（使 `docker pull` 走通 `ghcr.io` 且不向容器透漏代理凭据），并固定使用 `python3.12` 在 `/localdata/<USER>/`（`1007G` 数据盘）初始化 `harbor==0.24.0` 与 `alw`。
  1. **宿主机与 Docker 权限核查**：运行 `scripts/preflight_alw.sh`，确认 Linux/amd64 内核、Docker daemon（原生 `docker` 组或显式配置免密 `sudo docker` / rootless）、Docker Compose v2、Python $\ge 3.12$、宿主机可用 CPU $\ge 6N$ 核及可用内存 $\ge 32N\text{ GiB}$（含系统开销余量；当前共享宿主机 `259Gi` 可用且 `Swap: 0B`，默认取 `N=4`，安全上限 `N=6`，严禁 `N=8`）、`ghcr.io` / `registry.npmjs.org` / `pypi.org` 连通性及网关鉴权方式（`Authorization: Bearer` 与 `x-api-key` 均已验证 `200`）。
  2. **拉取并校验固定摘要 EDA 镜像**：拉取 `ghcr.io/nova-silicon/analoglayoutworld@sha256:f922e79ebbac026ed9d15987924867ba6fef4270a5c4994579500e9138bf8338`（`[事实]` 压缩约 `595 MB`，展开约 `1.88 GB`），并在 `--network none` 下运行镜像内置 `check_runtime.py`（核验 KLayout `0.30.12`、Magic `8.3.685`、Netgen `1.5.324`、ngspice-46 KLU、continuous/binned SKY130 模型库、gdsfactory `9.45.0`、Node `v22.23.3`）。
  3. **Harbor + ALW `nop` 烟雾（`dev` 题 `#21`）**：执行 `python scripts/run_harbor.py --task sky130-adb-ota-5t-gain40-pm60-noise50uv-pvt-layout --agent nop --attempts 1 --concurrency 1 --job-name smoke-nop`，验证 Harbor 0.24.0 能正常建容器、移交空提交并在独立断网验证容器生成 `verifier/evaluation/metrics.json`（预期 `status="missing_submission"`、`score=0.0`、`score_valid=true`、`evaluation_error=false`）。
  4. **切分校验与 `H_base` 离线单元测试 + 容器烟雾**：运行 `ssap alw-check-split`（验证 35 硬簇 / 32 保守簇零跨切分泄漏、`G_dev=10 >= 8` 及静态禁用 `val/test` ID 黑名单）与 `pytest tests/test_alw_*.py`；同时在离线测试容器挂载 `/opt/harness/`（含 `/opt/harness/python/ssap/`），用人工构造的 toy GDS 测试 `mcr_promote` 的 `layout.gds` 原子 `os.replace()` 与 `src/` 暂存目录安全互换、直接覆写回退保护及 $\mathbf{m}_{\mathrm{base}}$（`m_1..m_6`）输出，并用 `alw verify` 交叉比对一次（零模型费用）。
- **与 `method-v5` 差异**：本阶段**完全跳过**原 `RUNBOOK.md` 第 4 步的 `mv5 alw replay-check`（不再录制和重放请求日志，也不测调用命中率）。
- **可停机交付物**：**可复用的容器环境基座 + 经离线与容器双验证的 `H_base` 保底内核包（0 模型费）。**

---

### Phase 1：单题短超时 $\to$ 小批量 `dev` 基线与四项免费体检

所有 Phase 1 运行**严格限定在 `dev` 集 12 题内**（绝对不触碰 `val` 或 `test`）。

1. **Phase 1a：单题 10 分钟短超时通路验证（1 次运行）**
   - 选 `dev` 最小题 **`#21`（`sky130-adb-ota-5t-gain40-pm60-noise50uv-pvt-layout`，6 器件，`continuous`）**，以 `--agent-timeout 600`（`claude-code 2.1.302`）在 `H_base`（烟雾配置 `T_warn=300s, T_soft=390s`，确保到达软截止线时仍余 `210s >= 180s` 足够实地演练 Phase B 无状态 `claude -p` 触发）下跑 1 次。
   - **验收目的**：验证容器内 `claude-code` 安装、透传计量代理/网关连通、`/opt/harness` 只读挂载、`settings.json` 钩子触发、`handoff.json` 更新、`mcr_snapshot/{pre_repair/, artifact_summary.*}` 导出至 `<trial>/agent/mcr_snapshot/`，以及外层 `metrics.json` 完整落盘。
2. **Phase 1b：代表性 3 题全预算（`10800s`）小批量验证（3 次运行）**
   - 抽取覆盖两套 LVS 后端与不同器件规模的 3 道 `dev` 题：**`#21`**（6 器件，`klayout`）、**`#38`**（`sky130-local-comparator-layout`，17 器件，`klayout`，论文附录 E.2 案例）、**`#2`**（`sky130-adb-buf-ppsf-bs-gain1-sfdr90-layout`，39 器件，`magic_netgen` 后端）。
   - **验收目的**：核实 `T_warn=9000s` / `T_soft=9900s` 在 3 小时满预算下的实际耗时分布、无状态 `claude -p` 终局补救在真超时/门控触发时的行为、`magic_netgen` 镜像校验耗时及内存峰值（防 32 GiB OOM 137）。据此实测分布复核（必要时固化）$\Delta T_{\mathrm{rep}}$，一旦锁定不再调参。
3. **Phase 1c：全量 `dev` 基线（`12` 题 $\times K_0=2$ 种子 = `24` 次 `H_base`；可选加测 `12` 次 `H_raw`）**
   - 在 12 道 `dev` 题上运行 $H_{\mathrm{base}}$ 独立 2 种子（`24` 次完整尝试），同步归档全部 `pre_repair/` / `post_repair/` 容器快照、草稿里程碑 GDS 与 `artifact_summary.json`。
   - 若预算允许，可同阶段跑 `1` 种子（`12` 次）裸宿主 `H_raw`（空挂载）以在开发阶段即观测 `H_raw -> H_base` 保底收益（正式三段归因仍在最终密封 `test` 上以 3 种子完成）。
4. **四项零大模型调用免费体检（`ssap alw-diagnose`，0 LLM Token）**
   - 仅消费 `H_base` $\times 2$ 种子归档产物与离线 `alw verify` 调用，产出 `dev_diagnostics.json/.md`：
     1. **残余失败四分法**：将所有未满分运行（`Y* < 1` 或 `C* = 0`）统计占比：**(a) 超时/未交有效 GDS**（`m_1=0` 或 `missing_submission`）、**(b) 已交 GDS 但失分在可公算镜像项**（$d^\star_{\mathrm{drc}}/d^\star_{\mathrm{lvs}}/d^\star_{\mathrm{shape}}/d^\star_{\mathrm{fill}}/d^\star_{\mathrm{morph}}$）、**(c) 已交 GDS 但失分在非公开/电气项**（$d^\star_{\mathrm{pex}}/d^\star_{\mathrm{postsim}}$ 或 $Q_t/\rho_t$ 缺口，含 `binned` vs `continuous` 子组）、**(d) 能力越界题**（全轨迹所有草稿快照均未产出有效LVS或接近可行中间态）。
     2. **验证器稳定性与镜像一致率审计**：抽取若干含 `postsim` 与 `lower_bound` 的 `dev` 快照，离线重跑 `alw verify` 两次测 $\mathcal{E}^\star$ 重放抖动；核对 $\mathbf{m}_{\mathrm{base}}$ 与外层 $\mathbf{d}^\star$ 一致率及 $k_{\mathrm{FP}}$。
     3. **双种子噪声地板 $\hat{\sigma}_{\mathrm{seed}}$**：计算 12 题上 `H_base` 2 种子单题标准差均方根及全集均值标准差——**仅作后续报告参照尺，严禁作为搜索或确认的统计否决门槛**。
     4. **轨迹内检查点选择余量**：离线用 `alw verify` 重评 Phase A 过程中 `work/draft/` 留存的里程碑 GDS，计算 $\max_t Y^\star(w_t) - Y^\star(w_{\mathrm{best}})$。

| 体检实测信号 | 机械触发的 MCR 模块开关与 Phase 3 优先级 `[判断]` |
|---|---|
| 类 **(a) 超时/坏档** $> 0$ 或草稿覆盖倒退 | 检查 `T_warn/T_soft` 预留是否充足；加固 `mcr_promote` 触发时机与 `handoff.json` 自动刷新（仍禁步级强制回滚）。 |
| 类 **(b) 可公算执行缺口** 占比高（如 `q_max`、DRC、LVS 错用参考网表） | **Round 1 主力开启**：第 I 类镜像门控升级（含 `m_pex` / 公开 `m_postsim` 资格考察）、第 II 类 `advisory_aspect_gt_3` 补救优化、第 III 类 KLayout/Magic/ngspice 报错压缩翻译器。 |
| 类 **(c) 电气/隐藏测试台缺口** 占比高（LVS+DRC 全过但 $F_t<1$ 或 $Q_t$ 低） | **开启**第 II 类内生自测探针（如 PVT 角落扫描/寄生敏度警示，零覆写权）；若 `m_6=1` 后 `postsim` 仍高频失败，将 `m_pex` / 公开 testbench `m_postsim` 纳入第 I 类扩展候选。 |
| **类 (d) 能力越界题**（全程无接近合格态） | 标注为冻结模型能力边界题，**不为其定制特设规则**，避免过拟合。 |
| **检查点余量** $\max_t Y^\star - Y^\star(w_{\mathrm{best}}) \approx 0$ | **关闭**多检查点公开排序器与额外中途选优分支，保持零参数 `H_base` 棘轮即可。 |
| **验证器不稳定 / `score_is_lower_bound` 高发** | 标明受 `3300s` 内部预算截断的高复杂度题（记录 `solver_disagreement`），解读增益时单列对照。 |

- **可停机交付物**：**`dev` 集 `H_base` 双种子基线成绩单 + 四项诊断体检报告 + 可复用的初始快照档案（此时停机已具备完整基线与瓶颈量化）。**

---
### Phase 2：主动裁判库 $\mathcal{B}_{\mathrm{dev}}$ 构建（0 大模型 Token）

由于 ALW 无官方参考版图 $w_{\mathrm{ref}}(x)$，且基准模型单题通过率有限（`paper_reference.json` 中 DeepSeek V4.1 Flash + Claude Code 仅 `8.3%` 完全验收），必须通过受控正负变异扩充 $\mathcal{B}_{\mathrm{dev}}$，否则 Stage 0 无法检验新检查器在多簇拓扑上的误杀率。

1. **归档来源收集（仅限 `dev` 集 12 题）**：
   - 将 Phase 1 产出的全部 `pre_repair/`、`post_repair/` 终态快照及有效 `work/draft/` 中间快照汇入 `$SSAP_STATE/bank/raw/`。
   - **按簇独立冻结 3 个内部盲簇**：因锁定切分中 $G_{\mathrm{dev}} = 10 \ge 8$，在进入变异与提议前，用确定性哈希从 10 个 `dev` 簇中冻结 **3 个内部盲簇**（约占 $1/4$–$3/10$，对应 3–4 题，映射保存于 `$SSAP_STATE/bank/blind_clusters.json`）。提议与实现 Agent 全程物理不可见盲簇的任何任务 ID、GDS、日志或逐题结果。
2. **零模型费确定性正/负样本合成（`ssap.bank.synthesize`）**：
   - **受控负例注入**（从已知各阶段合格或部分合格 `dev` 产物出发，精准破坏单一项）：
     - 破坏 `m_1` / `d*_gds`：写入空 `src/`、更改 GDS 顶层 cell 名、清空制造层或注入 symlink；
     - 破坏 `m_2` / `d*_lvs`：删除一个顶层金属端口文本标签、用 `met1` 短接两条信号线、或断开一根器件栅极连线；
     - 破坏 `m_3` / `d*_shape`：在版图远端放置孤立 `met1` 小块将外接矩形拉长至 $q \in (3.2, 3.8]$（在 $q^{\max}=3.0$ 题上用于测第 II 类 `advisory_aspect_gt_3` 触发与 $d^\star_{\mathrm{shape}}=0$ 标签，**严禁送入 $\epsilon_3$ 校准**）及 $q > 4.2$（专测 Class-1 `m_3=0`）；
     - 破坏 `m_4` / `d*_fill`：外围加宽空金属环扩大包围盒面积使 $\hat{\rho} < 0.08 < 0.10$；
     - 破坏 `m_5` / `d*_morph`：生成 5 条平行长总线 + 单排高长宽比器件布局触发 `meaningless=true`；
     - 破坏 `m_6` / `d*_drc`：插入 `0.05 um` 亚最小宽度 `met1` 细条或间距违规缺口。
   - **等价正例合成**（检验检查器不依赖脆弱坐标巧合）：对已通过某分项的版图做 5 nm 整数网格刚体平移、`90°` 旋转、非制造辅助层（如注释层）增改或合法供电加宽。
3. **离线真值重签核与三值分项打标（0 LLM Token）**：
   - 全部原始与合成产物在断网 Docker 中调用 `alw verify` 跑真实签核器 $\mathcal{E}^\star$，按 `metrics.json` 严格写入三值向量 $\mathbf{d}^\star \in \{0,1,\bot\}^8$：
     - **严守 $\bot$ 隔离规则**：当 `lvs_pass == false` 时，`d*_fill`、`d*_morph`、`d*_pex`、`d*_postsim` 均标为 $\bot$，严禁归入对应分项的 $\mathcal{S}^+_k$ 或 $\mathcal{S}^-_k$；当 `pex_success == false` 或 `score_is_lower_bound == true` 下未测完项全过时，`d*_postsim` 标为 $\bot$。
   - 构造全局干净集 $\mathcal{S}^+$、分项干净集 $\mathcal{S}^+_k$ 与当前卫冕者 $H_{\mathrm{inc}}$ 残余失真集 $\mathcal{S}^-_k$，所有覆盖度统计一律以 **`dev` 独立簇数（上限 10）** 计账。
4. **Stage 0 零模型调用筛选契约（`ssap alw-concordance` / Stage 0）**：
   - **静态安全扫描**：禁止外网连接、禁止越权读取 `/tests` 或外部路径、禁止出现任何 `task_id` 字符串或特定题目常数黑名单。
   - **第 I 类（镜像检查器）**：在 $\mathcal{S}^+_k \sqcup \mathcal{S}^-_k$ 上核验单向/双向一致率（`m_1, m_4, m_5, m_6` 须满足 $m_k=1 \implies d^\star_k=1$ 且 $\mathcal{S}^+_k$ 零误拦；`m_2` 须满足 $m_2=1 \implies d^\star_{\mathrm{lvs}}=1$ [相对外层私有 `dummy_purge` 单侧保守]；`m_3` 须满足 $d^\star_{\mathrm{shape}}=1 \implies m_3=1$ [零误拒 $C^\star=1$ 真解]）。**关键保护带约束**：`m_3`（全局必要上界 $q \le 4.0$）保持 $\epsilon_3 = 0.0$ 固定（或仅限 $q_t^{\max}=4.0$ 样本校准），**绝不**将 $q_t^{\max}=3.0$ 题目上的 $q \in (3.0, 4.0]$ 失配算作 $\tilde{u}_3 \ge 0 \land d^\star_{\mathrm{shape}}=0$ 来抬高 $\epsilon_3$（否则 $\epsilon_3$ 会膨胀至 $\sim 1.0$ 并误杀 $q_t^{\max}=4.0$ 真解，破坏不变量 2；该区间改由第 II 类 `advisory_aspect_gt_3` 提示）；其余如 `m_4` 按 $\epsilon_4 = \max\{\tilde{u}_4 : \tilde{u}_4 \ge 0 \land d^\star_{\mathrm{fill}} = 0\} + 10^{-6}$ 机械校准并验证 $\mathcal{S}^+_k$（含 3 个内部盲簇）零误拦（$k_{\mathrm{FP}} = 0$），盲簇仅回传聚合通过/误拦/捕获计数。
   - **第 II 类（启发型与内生探针）**：在 $\mathcal{S}^+$ 全集上回归 **$k_{\mathrm{FP}} = 0$**，且在 **3 个内部盲簇** 的残余负例上取得 $k_{\mathrm{TP}} > 0$；严禁进入 $\mathbf{m}$ 或拥有任何覆写权。
   - **第 III 类（观测映射/报错翻译器）**：在归档历史 KLayout/Magic/ngspice 日志集上验证故障根因行保留率 $100\%$、输出长度压缩上限（如 $\le 2\text{ KB}$）与定位命中率。
- **可停机交付物**：**经 $\mathcal{E}^\star$ 三值标定的 $\mathcal{B}_{\mathrm{dev}}$ 主动裁判库 + 3 盲簇隔离边界 + Stage 0 零成本自动化验收流水线。**

---

### Phase 3：首轮迭代（终局门控 + 报错解析器）与纯净 `C1` 留出交付

1. **为什么 ALW Round 1 聚焦 Class I/II 终局门控与 Class III 报错解析器（而非全局提示词搜索）？ `[事实 + 判断]`**
   - ALW 属于典型的 **重型小簇（Heavy, $G_{\mathrm{dev}}=10$）** 场景：单题 3 小时、`11M–38M` token，且同题多种子方差高（`alw-plan.tex` 估算单次独立对比标准误可达 $\sim 0.11$）。若修改全局指令/技能（第 IV 类，Path 3），每测 1 个候选须重跑全集 12 题 $\times 3\text{ h}$，采样噪声极易淹没真实增益（`mcr-formal.tex` §7.3）。
   - 同时，ALW 论文与源码直接揭示了高确定性的执行缺口杠杆：（a）`90.3%` 的 DRC 首次通过与 `66.8%` 的 LVS 首次通过均发生在首次自检之后（`paper_reference.json` §5.1）；（b）#9 任务 GPT-6 Astra 过 DRC/LVS/后仿却因 $q=22.63\text{--}26.31 > 4$ 仅得 `0.3` 分；（c）KLayout/Magic/ngspice 原始日志动辄数万行极易淹没关键失配网络与浮空端口。
   - 因此 Round 1 按“收益密度/方差比”严格限定单点原子变异范围：**（1）将经 Stage 0 一致率核验的 `m_pex` / 公开 testbench `m_postsim` 升格为第 I 类终局门控组件或扩充第 II 类终局告警；或（2）引入第 III 类 KLayout LVS/DRC + Magic PEX + ngspice 报错压缩翻译器**。

2. **分叉对齐评估与救回事件账本（`ssap alw-eval` / `ssap alw-loop`）**：
   - **Path 1（仅限 `scope="terminal_only"` 的第 I/II 类终局门控候选，主力路径；若改动中途 `mcr_promote` 则归入 Path 2）**：
     - 分叉点精准落于 Phase A 退出瞬间完整归档 `/alb-workspace/` 的 `pre_repair/` 快照（`fork_meta.json` 固化 `phase_a_elapsed_sec` 与 `granted_repair_budget_sec`）：
       - **未触发分叉任务**（候选门控决策及诊断与 $H_{\mathrm{inc}}$ 一致）：**0 次模型调用、0 次验证器调用**，相对 $H_{\mathrm{inc}}$ 效应差精确记为 $\Delta Y^\star \equiv 0$；
       - **触发分叉任务**：直接挂载完整归档 `pre_repair/` 工作区与候选 `gate_report.json` + 原 `handoff.json`，在隔离容器内运行无状态 `claude -p --no-session-persistence` 修复子调用（独立 `CLAUDE_CONFIG_DIR`，$R_{\mathrm{rep}} = 3$ 种子取均值，预算沿用 `granted_repair_budget_sec` 与 $\Delta S_{\mathrm{rep}} \le 15$），经棘轮判定后调用 `alw verify`，并以 **卫冕者终态 $Y^\star(x_e, w_{\mathrm{inc\_final}})$**（防止重复计入 $H_{\mathrm{inc}}$ 自身 Phase B 已救回分值）为基准计算 $\Delta Y^\star_e = Y^\star(x_e, w_{\mathrm{cand\_post}}) - Y^\star(x_e, w_{\mathrm{inc\_final}})$。
     - 记入确定性 **救回事件账本（`event_ledger.jsonl`）**：
       - **救回（Rescued）**：$\Delta Y^\star_e > 0$；
       - **无效（Neutral）**：$\Delta Y^\star_e = 0$（包括修坏被 `H_base` 棘轮安全挡回原备份的情形）；
       - **损坏（Damaged）**：$\Delta Y^\star_e < 0$（强制逐条回传分析原因，用于增补 $\mathcal{B}_{\mathrm{dev}}$ 正例回归或加宽 $\epsilon_k$）。
   - **Path 2（中途检查器改动 / 第 III 类报错解析器 / 条件钩子）**：
     - 须在 `dev`（12 题）上从头配对运行并执行**非触发前缀泄漏审计**（统计双臂均未触发钩子的子集均分；若偏移则改归 Path 3 池化均值比较）。
   - **晋级与清理规则**：每次仅允许相对于 $H_{\mathrm{inc}}$ 的**单点原子改动**；Path 1 候选在事件账本总净变 $\sum_e \Delta Y^\star_e > 0$ 时晋级为新 $H_{\mathrm{inc}}$（Path 2/3 须在全量 `dev` 12 题上完赛并严格高于 $H_{\mathrm{inc}}$ 历史池化均值）。若某个已采纳门控在连续 $K_{\mathrm{idle}} = 2$ 轮事件账本中 **救回次数 $= 0$**，自动从 $H_{\mathrm{inc}}$ 移除防膨胀。

3. **何时与如何运行纯净 `C1` 验证及封存测试集单次揭盲**：
   - **何时验证**：完成 Round 1（或预算/残余集收口后任意周期结束）准备冻结阶段成果时，将唯一 `dev` 卫冕者 $H_{\mathrm{inc}}$ 与固定 $H_{\mathrm{base}}$ 送入封存 $\Dval$（14 题）。
   - **纯净 `C1` 验证选模（仅运行 1 次配对比较）**：计算 $\hat{\Delta}_{\mathrm{val}}(Y^\star) = \frac{1}{14}\sum_{x\in\Dval}\bigl(Y^\star(x, H_{\mathrm{inc}}) - Y^\star(x, H_{\mathrm{base}})\bigr)$。**当且仅当 $\hat{\Delta}_{\mathrm{val}}(Y^\star) > 10^{-6}$ 时令 $H^\star = H_{\mathrm{inc}}$，否则保留 $H^\star = H_{\mathrm{base}}$**（绝对禁用 `W_i`、McNemar、`p <= 0.05` 或其他门槛；且仅回传胜出版本 ID，严禁逐题结果回流）。
   - **封存测试集 $\Dtest$ 单次多种子揭盲（14 题 $\times R \ge 3$ 种子）**：锁定仓库提交哈希后，在 $\Dtest$ 上同配置独立运行 $R=3$ 种子（共 `42` 次/臂）：
     1. **三段式归因臂**：`H_raw`（裸宿主）、`H_base`（固定保底内核）、`H*`（最终选定版）；自动优化仅认领 `H_base -> H*` 段增益；
     2. **两项诚实基线**：（a）一页人工编写 `instructions.md`（把公开 `GUIDE.md` 与公开基准论文协议中的早存 GDS、`lvs_reference.spice` 区分、紧凑长宽比/填充率及衬底接触规范总结成静态提示）；（b）同资源公开镜像特征非盲 $\text{Best-of-}k(H_{\mathrm{base}})$ 曲线（按 `H*` 相对 `H_base` 额外花掉的 token 折算同预算抽样）；
     3. **强制报告列**：多种子均值、种子间噪声地板 $\hat{\sigma}_{\mathrm{seed}}$、逐题胜负翻转 $(n_+, n_-)$、按簇（$G_{\mathrm{test}}=10\text{–}11$，须显式标注“簇数接近小样本边界，同时列出 10 簇逐簇原始净差”）自助法区间、**$C^\star$ 翻转贡献 vs 未翻转部分质量贡献拆账**、四项原始物理成本账本（input/output/cached tokens + 美元、工具 CPU 秒、峰值内存、总墙钟耗时含离线搜索）以及 `infra_error` 独立分账表（含 `delta_worst` 敏感性下界）。
- **可停机交付物**：**经过纯净 `C1` 验证的 `H*` 发布版本与（若揭盲）可发表级三段归因测试报告。**

---
## 2. 模块边界与 `ssap` 代码目录规划

### 2.1 `ssap` 仓库目录树规划 `[判断]`

所有运行时数据、Harbor job 目录与快照均写入环境变量 `$SSAP_STATE`（或 `.gitignore` 已忽略的 `var/`），**严禁提交入 Git**；`ssap` 源码**零 `import`** `external/AnalogLayoutWorld/repo`（仅以只读数据形式读取其公开 `tasks/` 与 `configs/paper-v1/`）。

```text
ssap/
├── pyproject.toml                       # 暴露命令行入口 `ssap = "ssap.cli:main"`（Python >= 3.12）
├── .gitignore                           # 已忽略 var/、runs/、.venv/、*.sqlite*
├── AGENTS.md
├── docs/
│   ├── mcr-formal.tex / mcr-formal.pdf / mcr-plain.md
│   ├── alw-mcr-instantiation.md         # MCR -> ALW 形式化实例化与 H_base 规范
│   └── alw-progressive-plan.md          # 本文档
├── configs/alw/
│   ├── catalog.json                     # 40 题只读元数据（devices, q_max, u_target, profile）
│   ├── clusters.json                    # 35 硬簇与 32 保守簇映射表及依赖证据
│   ├── split.json                       # 锁定切分（seed: ssap-alw-v2-19; 12/14/14）
│   ├── paper_reference.json             # 解析自 arXiv 2611.16991v1 的对照基准数值
│   ├── config.server.json               # 服务端 Harbor/模型/网关/代理配置模板（仅引用 ${ENV}，零明文密钥）
│   └── compose.host-gateway.yaml        # 可选宿主机网关透传代理 host-gateway 覆盖文件
├── scripts/
│   ├── preflight_alw.sh                 # Phase 0 宿主机只读体检（Docker/sudo/CPU/内存/磁盘/网关鉴权）
│   ├── make_catalog.py                  # 从 ALW 只读生成 configs/alw/catalog.json
│   ├── make_split.py                    # 按保守簇生成 configs/alw/split.json 与 clusters.json
│   └── make_paper_reference.py          # 从论文文本解析生成 paper_reference.json
├── container/alw/                       # 经 Harbor mounts 只读挂载至容器 `/opt/harness/`
│   ├── settings.json                    # Claude Code 原生钩子配置（PreToolUse/PostToolUse/Stop）
│   ├── bin/                             # 均注入 PYTHONPATH=/opt/harness/python，零容器内 pip 安装
│   │   ├── mcr_entrypoint.py            # 容器主入口：锁输入哈希、转储 .mcr/task_prompt.txt、Phase A 计时(T_soft)、Phase B(claude -p)、归档
│   │   ├── mcr_hook.py                  # 钩子：PostToolUse 覆写防护、T_warn=9000s 提醒、Stop 检查
│   │   ├── mcr_check                    # 调用 checkers/alw 产出 m_base, u_base 与 gate_report.json
│   │   └── mcr_promote                  # 调用 kernel/promote 执行暂存校验 + 原子/安全目录切换晋升
│   ├── python/ssap/                     # 构建挂载包时由 agent_wrapper.py 从 src/ssap/{kernel,checkers/alw} 同步暂存
│   └── parsers/                         # Phase 3 新增：第 III 类 KLayout/Magic/ngspice 报错翻译器
├── src/ssap/
│   ├── cli.py                           # 子命令：`alw-check-split`, `alw-concordance`, `alw-diagnose`,
│   │                                    #         `alw-eval`, `alw-loop`, `alw-report`
│   ├── kernel/                          # 【基准无关通用内核】
│   │   ├── ratchet.py                   # 零参数三层棘轮偏序（Tier1-2 字典序 -> Tier3 帕累托 -> 同层裕量单调）
│   │   ├── promote.py                   # staging/ 完整性核验 + layout.gds 原子 os.replace() / src 暂存目录互换 + direct 快照保护
│   │   ├── handoff.py                   # handoff.json 读写与 4 KB 体积校验
│   │   ├── snapshot.py                  # pre_repair/ 与 post_repair/ 归档收集
│   │   ├── watermarks.py                # T_warn / T_soft / Delta_T_rep 计时与触发判断
│   │   └── artifact_summary.py          # review-2026-11 #21：artifact_summary.json/.md 生成与分叉比对
│   ├── checkers/alw/                    # 【ALW 本地容器检查器，独立重实现】
│   │   ├── gds_geom.py                  # m_1 (m_gds): T=0 只读隔离 + 无 symlink + klayout.db 38 制造层 bbox
│   │   ├── lvs.py                       # m_2 (m_lvs): 读 eda_env.sh (ALB_NETGEN_BIN) 判 KLayout vs Magic+Netgen；由 .mcr/task_prompt.txt + circuit.spice（含多 subckt 消歧与 NMOS 体端主网表）定 top/ports/lvs_sub
│   │   ├── area_fill.py                 # m_3 (m_shape) & m_4 (m_fill): q<=4.0, advisory_aspect_gt_3, rho_hat>=0.10
│   │   ├── morphology.py                # m_5 (m_morph): 无标注 meaningless 四条件联合复现
│   │   ├── drc.py                       # m_6 (m_drc): KLayout sky130A_mr.drc 违例统计
│   │   ├── pex.py                       # Phase 2/3 候选：Magic ngspice() RC 提取与独立端口一致性镜像
│   │   └── registry.py                  # Class I 镜像检查器 vs Class II 启发探针权限注册表
│   ├── bank/                            # 【主动裁判库与 Stage 0】
│   │   ├── referee_bank.py              # B_dev 持久化与 S+ / S+_k / S-_k（严格排除 bot）索引
│   │   ├── synthesize.py                # 0 模型费确定性正/负样本变异器（LVS/DRC/q/rho/morph）
│   │   ├── concordance.py               # 离线容器单测烟雾（Phase 0.4）、一致率验证与 eps_k 校准
│   │   └── blind_clusters.py            # 3/10 dev 内部盲簇冻结、权限隔离与聚合计数接口
│   ├── eval/                            # 【分叉评估与纯净 C1】
│   │   ├── fork_eval.py                 # Path 1 无状态快照修复重放（R_rep=3）、Path 2 前缀泄漏审计、Path 3 全量
│   │   ├── event_ledger.py              # 救回/无效/损坏事件账本与 K_idle=2 零救回自动清理
│   │   ├── c1_protocol.py               # 纯净 C1 验证选模（Delta_val > 1e-6）与 test 三段归因/C*拆账
│   │   └── infra_ledger.py              # infra_error 分账、theta_gw_max=0.35 判定与 delta_worst 下界
│   └── targets/alw/                     # 【ALW Harbor 适配层】
│       ├── catalog.py                   # 只读解析 task.toml / circuit.spice / constraints.json
│       ├── splits.py                    # 5 条防泄漏切分规则校验与静态 AST 黑名单扫描
│       ├── harbor_runner.py             # 生成 Harbor 0.24 job_config、可选零缓存透传计量代理与执行调度
│       ├── metrics_reader.py            # 解析 verifier/evaluation/metrics.json 与 8 维三值向量 d*
│       └── agent_wrapper.py             # 宿主机打包 /opt/harness（将 src/ssap/{kernel,checkers/alw} 同步入 /opt/harness/python/ssap/）与 Phase B 补救容器
└── tests/
    ├── test_alw_split.py                # 验证 35/32 簇零泄漏、dev=12(G=10)>=8、binned=3/3/3
    ├── test_alw_ratchet.py              # 验证三层偏序、原子替换防半写、Class II 零覆写权
    ├── test_alw_mirror.py               # 验证 m_1..m_6、lvs 失败短路 d*_k=bot、advisory_aspect_gt_3
    ├── test_alw_concordance.py          # 验证 eps_k 校准公式、k_FP=0 契约、盲簇计数隔离
    ├── test_alw_artifact_summary.py     # 验证 Item 21 JSON/MD 生成（含无模型分叉但 GDS 改变场景）
    └── test_alw_c1_protocol.py          # 验证纯 C1 >1e-6 阈值、infra_error 隔离、C* 翻转拆账
```

### 2.2 核心模块边界与数据契约 `[判断]`

1. **边界隔离原则**：
   - `kernel/` 严禁出现任何 `sky130`、`klayout` 或 `gds` 领域硬编码；棘轮仅消费 `registry.py` 声明的 `(m_vector, u_vector, advisory_flags)`。
   - `checkers/alw/` 与 `container/alw/` 严禁读取 `val/test` 清单或任何外部 `tests/task/` 文件；**注意 ALW `environment/Dockerfile` 仅将 `GUIDE*.md`、`DEVICE_API*.md`、`eda_env.sh` 与 `task/` 拷入 `/alb-workspace/`，容器内磁盘上并不存在 `instruction.md` 或 `constraints.json`**。因此所有任务参数仅来自 `/alb-workspace/{eda_env.sh, task/circuit.spice, task/testbench/}` 与 `mcr_entrypoint.py` 捕获 Harbor 初始提示写入的 `/alb-workspace/.mcr/task_prompt.txt`（并在 `#37` 等含多未引用 `.subckt` 时结合调用图与提示/testbench 消歧）；`agent_wrapper.py` 负责将 `src/ssap/{kernel,checkers/alw}` 预打包至 `/opt/harness/python/ssap/`，使容器内脚本经 `PYTHONPATH=/opt/harness/python` 直接运行而无需宿主机/容器内 `pip install ssap`。
   - **零缓存透传计量代理与环境防污染**（位于 `harbor_runner.py`，模型运行时强制启用）：替代 `method-v5` 的 `RecordingProxy`。仅做宿主机鉴权头注入（防容器泄漏真 key [复审 #22]，容器侧同时接受 `x-api-key` 与 `Authorization: Bearer` 临时令牌，锁定 `-spec`/`-special` 模型时按单次 Trial 粘性均衡绑定 `claude-spec` 与 `claude-mine` 双 Key，严禁混用常规模型与 `-spec`/`-special` 组，并逐请求记录响应体 `"model"` 字段）、累计内存态 `input/output/cached_tokens`、统计 `proxy_requests` 与 `gateway_wait_seconds`（请求发出至首字节到达 TTFB + `429/5xx` 重试等待秒数），**绝不写入请求 blob 缓存、绝不做响应回放**。容器启动前采用白名单环境剥离宿主机 `ANTHROPIC_*`、`CLAUDE_*`、`AWS_*`、`MAX_THINKING_TOKENS`、`DISABLE_PROMPT_CACHING` 及全部 `*_PROXY`/`*_proxy`，将 `Claude Code` 全部 8 个模型槽位（`ANTHROPIC_MODEL`, `ANTHROPIC_SMALL_FAST_MODEL`, `ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS}_MODEL`, `CLAUDE_CODE_{SUBAGENT,AUTO_MODE,BG_CLASSIFIER}_MODEL`）统一钉死为单实验模型 `$SSAP_MODEL`，附设 `DISABLE_NON_ESSENTIAL_MODEL_CALLS=1`、`CLAUDE_CODE_TWO_STAGE_CLASSIFIER=false`、`API_TIMEOUT_MS=3000000` 与容器 `NO_PROXY=host.docker.internal,172.17.0.1,localhost,127.0.0.1,::1,0.0.0.0`，禁止向 Harbor `env` 传入含凭据 `http_proxy`，并在落盘任何日志/配置/摘要前执行宽协议 URL 与密钥值双重脱敏（详见 [alw-server-env.md](alw-server-env.md) 第 2–3 节与 [fable-5.2-plan-review.md](fable-5.2-plan-review.md)）。
2. **关键数据契约（JSON Schema 概要）**：
   - **交接单 `/alb-workspace/.mcr/handoff.json`**（$\le 4\text{ KB}$）：`{schema_version, task_id, top_cell, status, draft_gds_path, draft_src_dir, best_cert_summary: {m, u}, last_checks, known_blockers, next_steps, updated_at_utc}`。
   - **门控报告 `/alb-workspace/.mcr/gate_report.json`**：`{schema_version, evaluated_at_utc, m_base, u_base, advisory_flags, gate_triggered, trigger_reasons, target_gds_path, diagnostic_excerpt}`（其中 `gate_triggered=true` 当且仅当 `T_max - elapsed >= 180s` 且 `[m_base != (1,1,1,1,1,1) | draft_newer_than_best | "advisory_aspect_gt_3" in advisory_flags]`）。
   - **产物摘要 `/logs/agent/mcr_snapshot/artifact_summary.json`（及 `.md`，落实 `review-2026-11.md` 第 21 条）**：
     ```json
     {
       "task_id": "...", "seed": 0, "version_id": "...",
       "pre_repair_gds_sha256": "...", "post_repair_gds_sha256": "...",
       "submission_gds_sha256": "...", "submission_src_tree_sha256": "...",
       "draft_latest_gds_sha256": "...",
       "m_pre": [1,1,0,1,1,1], "u_pre": [0,0,-0.42,0.18,1,0],
       "m_post": [1,1,1,1,1,1], "u_post": [0,0,1.20,0.20,1,0],
       "gate_triggered": true, "trigger_reasons": ["m_shape_fail"],
       "repair_ran": true, "ratchet_promoted": true, "promote_reason": "tier3_pareto",
       "model_request_diverged": false, "submission_artifact_diverged": true,
       "infra_error": false, "infra_reason": null,
       "outer_metrics": {"Y_star": 0.68, "C_star": 0, "d_star": [1,1,1,1,1,1,1,0],
                         "score_is_lower_bound": false, "evaluation_error": false},
       "cost_ledger": {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
                       "usd": 0.0, "tool_cpu_sec": 0.0, "peak_ram_mb": 0, "wall_sec": 0.0}
     }
     ```
     > **Item 21 审计意义**：即使 `model_request_diverged == false`，只要 `submission_artifact_diverged == true`（由钩子或 Phase B 补救引起），评估器依然强制判定该任务“受候选触及”并独立记账。
   - **基础设施账本 `$SSAP_STATE/ledgers/infra_ledger.jsonl`**：记录每次 `infra_error` 事件的 `{run_id, arm, task_id, seed, category, exit_code, gateway_wait_ratio, paired_rerun_status}`，与 `0` 分任务失败彻底分账。
   - **救回事件账本 `$SSAP_STATE/ledgers/event_ledger.jsonl`**：记录每次 Path 1 触发 `{round, candidate_id, task_id, rep_seed, Y_pre, Y_post, delta_Y, verdict: "Rescued|Neutral|Damaged", promote_reason}`。

### 2.3 分阶段最小可行构建顺序（MVP Build Order） `[判断]`

- **MVP-0（阻塞 Phase 0 / Phase 1 之前必须完成，核心可运行最小集合）**：
  1. `configs/alw/{catalog.json, clusters.json, split.json, paper_reference.json, config.server.json, compose.host-gateway.yaml}` 与 `scripts/preflight_alw.sh`；
  2. `targets/alw/{catalog.py, splits.py, metrics_reader.py, harbor_runner.py, agent_wrapper.py}`；
  3. `kernel/{ratchet.py, promote.py, handoff.py, snapshot.py, watermarks.py, artifact_summary.py}` 与 `container/alw/{settings.json, bin/*}`；
  4. `checkers/alw/{gds_geom.py, lvs.py, area_fill.py, morphology.py, drc.py, registry.py}`；
  5. `bank/concordance.py`（最小 `--offline-smoke-container` 离线容器烟雾模式）、`eval/infra_ledger.py`、`cli.py`（`alw-check-split`, `alw-concordance --offline-smoke-container`, `alw-eval` 的 `smoke-10m/subset/baseline` 模式, `alw-diagnose`）及 4 个核心单测 `test_alw_{split,ratchet,mirror,artifact_summary}.py`。
  *(此时即可上机跑完 Phase 0 与 Phase 1 全量 `dev` 基线与四项免费体检！)*
- **MVP-1（进入 Phase 2 / Phase 3 之前补齐）**：
  1. `bank/{referee_bank.py, synthesize.py, blind_clusters.py}` + `bank/concordance.py`（完整 `B_dev` 建库与 $\epsilon_k$ 校准）+ `checkers/alw/pex.py`；
  2. `eval/{fork_eval.py, event_ledger.py, c1_protocol.py}` + `cli.py`（`alw-concordance` 完整模式, `alw-eval` 分叉重放与 `c1-val`, `alw-loop`, `alw-report`）；
  3. `tests/test_alw_{concordance,c1_protocol}.py`。

---
## 3. 评测服务器渐进上机命令清单、验收判据与排障手册

### 3.1 宿主机环境初始化（每个新 Shell 执行，严禁写入代码库）

> 首次上机若宿主机尚无 `docker` / `harbor` / `alw`（如当前评测机实测状态），先按 [alw-server-env.md](alw-server-env.md) 第 3.1–3.2 节完成 **Phase 0.0**（安装 Docker Engine + Compose v2、加 `<USER>` 入 `docker` 组、配置 `root 0600` 的 `dockerd` 代理 drop-in）。

```bash
export ALW_REPO="/localdata/<USER>/AnalogLayoutWorld"
export SSAP_REPO="/home/<USER>/silicon-notebook/benchmark-deepeval/.worktrees/ssap"
export SSAP_STATE="/localdata/<USER>/ssap-alw-state"      # 置于 1007G 专用数据盘，严禁放在工作树内
export SSAP_GATEWAY="<GATEWAY_URL>"                        # 不带 /v1（Bearer 与 x-api-key 均通）
export SSAP_MODEL="deepseek-v4-1-flash"                    # 须在 Phase 1a 前按 alw-server-env.md §4 核对锁定
read -r -s -p 'Gateway API Key: ' SSAP_UPSTREAM_KEY; echo
export SSAP_UPSTREAM_KEY
mkdir -p "$SSAP_STATE"
```

### 3.2 分阶段可执行命令清单（Copy-Pasteable）

```bash
# === [Phase 0.1] 固定提交安装（显式使用 Python 3.12 + Harbor 0.24.0 + ssap） ===
git clone https://github.com/Nova-Silicon/AnalogLayoutWorld.git "$ALW_REPO"
git -C "$ALW_REPO" checkout d5236c886fefb5f5cedb0c755f3b3a949f1f2423
cd "$ALW_REPO" && /usr/local/pyenv/shims/python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[harbor]'
python -m pip install -e "$SSAP_REPO"
alw list | wc -l                                         # 预期：40

# === [Phase 0.2] 宿主机只读预检与固定摘要 EDA 镜像校验 ===
# 注意：Harbor 内部直接调用 `docker` CLI；若宿主机默认需 sudo，先配置免密 docker 组或 sudo 包装：
docker info --format '{{.ServerVersion}}' || sudo -n docker info --format '{{.ServerVersion}}'
cd "$SSAP_REPO"
bash scripts/preflight_alw.sh > "$SSAP_STATE/preflight.txt" 2>&1
cat "$SSAP_STATE/preflight.txt"
# 若需预先拉取镜像并离线检测全套 EDA 工具（KLayout/Magic/Netgen/ngspice/SKY130）：
EDA_IMG="ghcr.io/nova-silicon/analoglayoutworld@sha256:f922e79ebbac026ed9d15987924867ba6fef4270a5c4994579500e9138bf8338"
docker pull "$EDA_IMG"
docker run --rm --network none "$EDA_IMG" \
  python -I -B /opt/analog-layout/build-checks/check_runtime.py --out /tmp/eda-check

# === [Phase 0.3] Harbor + ALW nop 烟雾（dev 题 #21，0 模型费） ===
cd "$ALW_REPO"
python scripts/run_harbor.py \
  --task sky130-adb-ota-5t-gain40-pm60-noise50uv-pvt-layout \
  --agent nop --attempts 1 --concurrency 1 --job-name smoke-nop
cat jobs/smoke-nop/*/verifier/evaluation/metrics.json

# === [Phase 0.4] 切分防泄漏检查 + 单元测试 + 容器内 H_base 离线棘轮/镜像交叉检验 ===
cd "$SSAP_REPO"
ssap alw-check-split --config configs/alw/config.server.json
pytest tests/test_alw_*.py
# 在断网 EDA 容器内挂载 /opt/harness 测试 toy GDS 原子棘轮写穿透与 m_1..m_6 一致性：
ssap alw-concordance --config configs/alw/config.server.json --offline-smoke-container

# === [Phase 1a] 单题 10 分钟真实模型烟雾（dev #21，claude-code 2.1.302） ===
# 烟雾水位线取 T_warn=300s、T_soft=390s（600-390=210s >= 180s），以真实触发 Phase B 无状态修复通路：
ssap alw-eval --config configs/alw/config.server.json --mode smoke-10m \
  --arm H_base --task sky130-adb-ota-5t-gain40-pm60-noise50uv-pvt-layout \
  --timeout-s 600 --t-warn 300 --t-soft 390

# === [Phase 1b] 3 题全预算（10800s）代表性抽测（klayout 大小各一 + magic_netgen） ===
ssap alw-eval --config configs/alw/config.server.json --mode subset \
  --arm H_base --parallel 3 \
  --tasks sky130-adb-ota-5t-gain40-pm60-noise50uv-pvt-layout \
          sky130-local-comparator-layout \
          sky130-adb-buf-ppsf-bs-gain1-sfdr90-layout

# === [Phase 1c] 全量 dev 基线（12 题 x 2 种子 H_base，+ 可选 1 种子 H_raw）与四项免费体检 ===
ssap alw-eval --config configs/alw/config.server.json --mode baseline \
  --split dev --arm H_base --seeds 2 --parallel <N>
# 可选（若预算允许，提前在 dev 测 H_raw -> H_base 保底增量）：
# ssap alw-eval --config configs/alw/config.server.json --mode baseline --split dev --arm H_raw --seeds 1 --parallel <N>
ssap alw-diagnose --config configs/alw/config.server.json --split dev

# === [Phase 2] 构建主动裁判库 B_dev（正负变异 + 离线真值打标 + 3/10 盲簇冻结 + Stage 0） ===
ssap alw-concordance --config configs/alw/config.server.json \
  --build-bank --mutate --freeze-blind 3 --calibrate-eps

# === [Phase 3] 第 1 轮迭代（门控/解析器）-> 纯净 C1 验证 -> 密封测试集单次 3 种子揭盲 ===
ssap alw-loop --config configs/alw/config.server.json --rounds 1 --parallel <N>
# 确认候选锁定后，仅访问 val 一次做纯净 C1 选模（Delta_val > 1e-6）：
ssap alw-eval --config configs/alw/config.server.json --mode c1-val --split val --parallel <N>
# 锁定 git commit hash 后，在密封 test 上单次运行 3 种子并输出完整三段归因与成本报告：
ssap alw-report --config configs/alw/config.server.json --split test --seeds 3 --parallel <N> --with-baselines
```

### 3.3 各阶段预期输出路径与验收判据

| 阶段 | 关键预期产物路径 | 硬性通过判据（Pass） | 阻断判据（Fail / Stop） |
|---|---|---|---|
| **Phase 0** | `$SSAP_STATE/preflight.txt`<br>`$ALW_REPO/jobs/smoke-nop/*/verifier/evaluation/metrics.json` | `docker info` 正常；可用核 $\ge 6N$、内存 $\ge 32N\text{ GiB}$；`check_runtime.py` 退出码 `0`；`smoke-nop` 生成 `metrics.json`（`status="missing_submission"`, `score=0.0`, `evaluation_error=false`）；`alw-check-split` 与 `pytest` 全绿 | 无 Docker daemon 权限；镜像拉取失败；`evaluation_error=true`；`split` 或 `test_alw_*` 任意报错 |
| **Phase 1a** | `$SSAP_STATE/jobs/<run>/.../agent/mcr_snapshot/artifact_summary.json`<br>`.../verifier/evaluation/metrics.json` | `proxy_requests > 0`；`claude-code 2.1.302` 正常生成工具调用；`artifact_summary.json` 完整存在；`evaluation_error=false` 且 `infra_error=false` | `proxy_requests == 0`；网关 HTTP `401/403/5xx`；容器找不到 `/opt/harness/bin/mcr_hook.py` |
| **Phase 1b** | `$SSAP_STATE/snapshots/<run_id>/pre_repair/` & `post_repair/`<br>`$SSAP_STATE/ledgers/infra_ledger.jsonl` | 3 题无 OOM `137`；`#2`（`magic_netgen`）内层 `m_2` 正常识别并产出 `m_base`；若触发补救则 `post_repair` 严密受限于 `T_max=10800s` 内结束 | OOM `exit_code == 137`；Phase B 超时被 Harbor `SIGKILL` 致 `artifacts/submission/` 丢失；`m_base` 对真解或合法草稿出现误判 |
| **Phase 1c** | `$SSAP_STATE/reports/dev_diagnostics.{json,md}`<br>`$SSAP_STATE/bank/raw/` | 24 次 `H_base` 执行全部终态入账（允许偶发 `infra_error` 按同规则重跑）；四项诊断表完整；所有镜像项 $\mathcal{S}^+_k$ 上 $k_{\mathrm{FP}}=0$ | `infra_error` 频发（如 `gateway_wait_ratio > 0.35` 连续出现）导致无法收敛基线；存在 `m_k=1` 但外部 $d^\star_k=0$ 未加保护带 |
| **Phase 2** | `$SSAP_STATE/bank/{bank.sqlite, blind_clusters.json, eps_calibration.json}` | `B_dev` 覆盖全部 `10` 个 `dev` 簇；3 盲簇哈希锁定；$\bot$ 样本严格未混入 $\mathcal{S}^+_k/\mathcal{S}^-_k$；校准后 $\mathbf{m}_{\mathrm{base}}$ 对 $\mathcal{S}^+_k$ 满足 $k_{\mathrm{FP}}=0$ | 样本按重试膨胀而非按簇计数；盲簇明细被写入提议上下文；`lvs=0` 样本错标为 `d*_postsim=0` |
| **Phase 3** | `$SSAP_STATE/ledgers/event_ledger.jsonl`<br>`$SSAP_STATE/reports/{val_c1.json, test_final_report.md}` | Stage 0 筛除违约候选；Path 1 未触发题 `0` 模型费且 $\Delta Y^\star=0$；`val` 仅访问 1 次；`test` 3 种子三段归因 + $C^\star$ 拆账 + 物理成本账本完整 | 出现任意装饰性统计门槛（McNemar/$p\le 0.05$/$W_i$）；Path 2 未触发子集发生分数漂移；`val/test` 题号泄漏入提示或代码 |

### 3.4 常见故障排查手册（Failure-Triage Playbook）

1. **Docker 无权限或 daemon 不可达**：若宿主机仅允许 `sudo docker` 而无普通用户 `docker` 组，先确认是否配置无密码 `sudo` 包装脚本或把运行账号加入 `docker` 组；注意 `harbor` 内部直接调用 `docker` CLI，必须在非 root 交互与子进程下均可无密码执行 `docker info`（参考 `method-v5` 早期因评测机无 Docker 权限被迫搁置的教训）。
2. **容器报 `proxy_requests == 0` 或连接宿主机失败**：Linux 上必须启用 `configs/alw/compose.host-gateway.yaml`（向 `main` 服务注入 `host.docker.internal:host-gateway`），并核对宿主机防火墙（`ufw`/`iptables`）放行代理端口（如 `8791`）来自 `docker0` 网桥的入站连接。
3. **网关返回 HTTP 401/403（鉴权头不匹配）**：根据 `preflight_alw.sh` 输出，若仅 `Authorization: Bearer` 返回 200，修改 `configs/alw/config.server.json` 中 `upstream_auth_header: "Authorization"` 与 `upstream_auth_prefix: "Bearer "`；注意同时清理宿主机遗留 `ANTHROPIC_*`/`CLAUDE_*` 变量（`launch_env_clear`）。
4. **频繁 OOM（`exit_code == 137`）或网关排队（`gateway_wait_ratio > 0.35`）**：立即降低 `--parallel N`（如从 `4` 降至 `2`），确保宿主机真实预留 $\ge 32\text{ GiB}/$并发及额外 `16 GiB` 宿主机缓冲；全部受影响运行记入 `infra_ledger.jsonl` 按同等规则成对重跑，不计 0 分也不丢弃真失败。
5. **Phase B 补救在 10800s 被强杀**：检查 `pre_repair/` 与 `post_repair/` 中的时间戳，若 `alw` 内部 DRC/LVS/PEX 占用超过 `900s`（如复杂 `magic_netgen` 题），在进入 Phase 1c 之前依 Phase 1b 实测 $P_{95}$ 一次性上调 $\Delta T_{\mathrm{rep}}$（如将 `T_soft` 设为 `9600s`, $\Delta T_{\mathrm{rep}}=1200\text{ s}$），此后锁死。

---
## 4. 算力、Token、磁盘与墙钟估算表

### 4.1 已核验常量 `[事实]` 与运行期暂定参数 `[暂定]` 分离表

| 参数类别 | 参数名 | 数值 / 范围 | 状态 | 依据来源 |
|---|---|---|---|---|
| 容器硬件配额 | 单并发 CPU / RAM | `6 CPU` / `32 GiB` | `[事实]` | ALW `task.toml`（全 40 题一致） |
| 墙钟硬上限 | Agent / Verifier 上限 | `10800s`（3.0 h）/ `3600s`（内部 `3300s`） | `[事实]` | ALW `task.toml` & `evaluate.py` |
| 切分与盲簇规模 | `dev / val / test` 题数 | `12`（`G=10`, 盲簇 `3`）/ `14` / `14` | `[事实]` | `configs/alw/split.json`（`ssap-alw-v2-19`） |
| 镜像磁盘占用 | EDA 基础镜像大小 | 压缩 `595 MB`，展开 `1.88 GB` | `[事实]` | ALW `docs/images.md`（固定 digest） |
| 单次模型 Token | DeepSeek V4.1 Flash (CC) | 均值 `13.2M tokens/次`（`134.0` 工具调用） | `[事实]` | `paper_reference.json`（表 1，40 题 $\times 3$ 次均值） |
| 跨模型 Token 区间 | 表 1 全配置单次区间 | `11.2M`（GPT-6 Astra）– `38.0M`（GLM-5.3 Flash） | `[事实]` | `paper_reference.json`（表 1） |
| 日志密度基线 | 每 100 调用轨迹体积 | 原始 ~`50 MB`，压缩 ~`23 MB` | `[事实]` | `review-2026-11.md` 实测（`ssap` 不存 `O(T^2)` blob） |
| 实际单次耗时 | 典型全量尝试墙钟 | Agent ~`1.5 h` + Verifier ~`0.3 h` = **`~1.8 h`**（上界 `4.0 h`） | `[暂定]` | Phase 1b/1c 实测后校准 |
| 终局补救成本 | 单次 Phase B 补救（`claude -p`） | 墙钟 `~0.43 h`（`0.18 h` 补救 + `0.25 h` 复签，上界 `1.25 h`）；约 **`1.5M tokens`**（~`11%` 全程） | `[暂定]` | 受限 $\Delta T_{\mathrm{rep}}=900\text{ s}, \Delta S_{\mathrm{rep}}=15$ |
| Round 1 触发率 | Path 1 门控在 `dev` 触发比 | $p_{\mathrm{trig}} \approx 4/12$（`33.3%`），取 $R_{\mathrm{rep}}=3$ 种子 | `[暂定]` | 参考论文低 LVS/高 `q` 占比估计 |
| 单次磁盘沉淀 | 每 Trial 快照 + 报告 | **`~200 MB / trial`**（含 `pre_repair/`, `post_repair/`, `verifier/`） | `[暂定]` | 含 GDS、提取网表与日志 |

### 4.2 跨并发度 $N \in \{1, 2, 4, 8\}$ 分阶段预算与墙钟表

> 注：墙钟格格式为 **`暂定典型均值 / [最坏满超时上界]`**；所有波次按 $\lceil \text{runs}/N \rceil$ 整数波次计算。Token 按 `DeepSeek V4.1 Flash + Claude Code` 论文均值 `13.2M / 全量尝试` 及 `1.5M / 终局补救` 换算（若换用 `GLM-5.3` `34.5M` 则等比乘以 `2.61×`）。**当前实测宿主机约束**（见 [alw-server-env.md](alw-server-env.md)）：`20` 用户共享、`259Gi` 可用内存且 **`Swap: 0B`**，故**当前宿主机默认取 `N = 4`、安全天花板 `N = 6`（`36C / 192GiB`），严禁运行 `N = 8`**（下表 `$N=8$` 列仅作 $\ge 320\text{ GiB}$ 独享高配机参考）。

| 阶段 / 事项 | 执行规模（运行次数） | Token 预估（M） | 磁盘增量 | $N=1$<br>(`6C/32G`) | $N=2$<br>(`12C/64G`) | $N=4$<br>(`24C/128G`) | $N=8$（仅 $\ge 320\text{G}$ 专用机）<br>(`48C/256G`) |
|---|---|---|---|---|---|---|---|
| **Phase 0：环境与 `nop` 烟雾** | `1` 次 `nop` + 容器自检 + 单测 | **`0`** | `~3.0 GB`（含镜像） | `0.4 h` / `[0.5 h]` | `0.4 h` / `[0.5 h]` | `0.4 h` / `[0.5 h]` | `0.4 h` / `[0.5 h]` |
| **Phase 1a：单题 10m 烟雾** | `1` 次（`#21`, `600s` 限） | `~2.0M` `[暂定]` | `~0.2 GB` | `0.4 h` / `[1.2 h]` | `0.4 h` / `[1.2 h]` | `0.4 h` / `[1.2 h]` | `0.4 h` / `[1.2 h]` |
| **Phase 1b：3 题全预算抽测** | `3` 次（`#21, #38, #2`） | `~39.6M` | `~0.6 GB` | `5.4 h` / `[12.0 h]` | `3.6 h` / `[8.0 h]` | `1.8 h` / `[4.0 h]` | `1.8 h` / `[4.0 h]` |
| **Phase 1c：`dev` `H_base` 基线 + 体检** | `24` 次（`12` 题 $\times 2$ 种子）+ 0 费体检 | `~316.8M` | `~5.0 GB` | `43.2 h` / `[96.0 h]` | `21.6 h` / `[48.0 h]` | `10.8 h` / `[24.0 h]` | `5.4 h` / `[12.0 h]` |
| *（可选 `Phase 1c+`：`dev` `H_raw` 1 种子）* | *`12` 次（提前看 `H_raw -> H_base`）* | *`~158.4M`* | *`~2.4 GB`* | *`21.6 h` / `[48.0 h]`* | *`10.8 h` / `[24.0 h]`* | *`5.4 h` / `[12.0 h]`* | *`3.6 h` / `[8.0 h]`* |
| **Phase 2：$\mathcal{B}_{\mathrm{dev}}$ 与 Stage 0 验钞** | ~`60` 次离线 `alw verify`（变异/重签） | **`0`** | `~1.5 GB` | `6.0 h` / `[15.0 h]` | `3.0 h` / `[7.5 h]` | `1.5 h` / `[4.0 h]` | `0.8 h` / `[2.0 h]` |
| **Phase 3 (Path 1)：1 个终局门控候选** | **`12` 次有界补救**（4 触发 $\times 3$ 种子；非触发 8 题 = 0） | **`~18.0M`** `[暂定]` | `~1.2 GB` | `5.2 h` / `[15.0 h]` | `2.6 h` / `[7.5 h]` | **`1.3 h`** / `[3.8 h]` | `0.9 h` / `[2.5 h]` |
| *对照：若做朴素全量 `dev` 重跑* | *`36` 次完整 `3h` 重跑（`12` 题 $\times 3$ 种子）* | *`~475.2M`* | *`~7.2 GB`* | *`64.8 h` / `[144.0 h]`* | *`32.4 h` / `[72.0 h]`* | *`16.2 h` / `[36.0 h]`* | *`9.0 h` / `[20.0 h]`* |
| **Phase 3 (Path 2)：1 个第 III 类解析器** | `12` 次配对重跑（对比 `H_inc` 池化均值） | `~158.4M` | `~2.4 GB` | `21.6 h` / `[48.0 h]` | `10.8 h` / `[24.0 h]` | `5.4 h` / `[12.0 h]` | `3.6 h` / `[8.0 h]` |
| **Validation（纯净 `C1` 单次选模）** | `28` 次（`val 14` 题 $\times$ [`H_base`, `H_inc`]） | `~369.6M` | `~5.6 GB` | `50.4 h` / `[112.0 h]` | `25.2 h` / `[56.0 h]` | `12.6 h` / `[28.0 h]` | `7.2 h` / `[16.0 h]` |
| **Sealed Test（单次揭盲 $\times 3$ 种子）** | `168` 次（`14` 题 $\times 3 \times 4$ 臂：`H_raw, H_base, H*, 1页人工`）$^\dagger$ | `~2217.6M` | `~33.6 GB` | `302.4 h` / `[672.0 h]` | `151.2 h` / `[336.0 h]` | `75.6 h` / `[168.0 h]` | `37.8 h` / `[84.0 h]` |

$^\dagger$ 注：若 `val` 选模未采纳新版本（即 $H^\star = H_{\mathrm{base}}$），测试集 `H*` 复用 `H_base` 3 种子结果，运行次数降至 `126` 次（`~1663.2M tokens`）。同预算 $\text{Best-of-}k(H_{\mathrm{base}})$ 曲线若需 $k > 3$ 可按 `H_base` 额外补跑对应尝试数，仅当 $k \le 3$ 时可零额外执行直接复用 `H_base` 3 种子。

### 4.3 分叉点评估节省效果与推荐机位配置 `[事实 + 判断]`

1. **分叉点评估净节省（Round 1 单门控候选）**：相较朴素全量 `dev` 3 种子重跑（`~475.2M` tokens、`16.2 h @ N=4`），Path 1 终局快照补救重放仅耗时 **`~18.0M` tokens（节省 ~`96.2%`）** 与 **`1.3 h @ N=4`（节省 ~`92.0%`）**，且完全消除了 8 道未触发题的采样随机噪声对净增量评估的淹没。
2. **至跑通 Round 1（含 Phase 0 $\to$ 1 $\to$ 2 $\to$ 3 的 2 个 Path 1 门控 + 1 个 Path 2 解析器）累计预算**：共约 **`~552.8M tokens`**（不到基准论文评测 1 组 40 题 $\times 3$ 次基线 `1584M tokens` 的 `35%`），在当前共享宿主机推荐的 **$N=4$ 默认并发（`24C / 128 GiB`，`$SSAP_STATE` 置于 `/localdata` `1007G` 数据盘）** 下约 **`~22.9 小时`** 典型墙钟；低负载时段升至安全天花板 **$N=6$（`36C / 192 GiB`）** 约 `~16–18 小时` 典型墙钟（当前 `259Gi` 可用且 `Swap: 0B` 严禁 `N=8`）。若在 $\ge 320\text{ GiB}$ 专用机跑 $N=8$，则可在 `~14.2 小时` 内跑完至 Round 1，并在约 `~2.5 天`（`59.2 h`）内走完 `val` 与 4 臂 3 种子密封 `test` 揭盲。
