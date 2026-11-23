# MCR v2 在 AnalogLayoutWorld（ALW）上的形式化实例化与保底内核 $H_{\mathrm{base}}$ 规范

> **证据分层约定**（遵循 [AGENTS.md](../AGENTS.md)）：文中所有基准参数、文件契约与源码语义均标注为 **`[事实]`**（直接核验自 `external/AnalogLayoutWorld/repo` 提交 `d5236c8` 与 `method-v5/experiments/alw`）；架构权衡与协议选取标注为 **`[判断]`**；尚未上机测得的运行时统计量（耗时分位数、噪声地板、初始水位线）标注为 **`[暂定]`**。所有评价与选模均严格执行纯净 `C1` 协议，**零装饰性统计否决门**。

---

## 1. MCR $\to$ ALW 实例剖面映射

### 1.1 基准原生签核三元组与评分数学定义 `[事实]`

经核验 `src/analoglayoutworld/{_scoring_core.py, scoring.py, data/paper-v1.json}` 与 `src/eda/{evaluate.py, verify.py, postsim_fraction.py, quality/collect.py, quality/meaningless.py}`：

1. **原生主效用 $Y^\star(x, W) = S_t \in [0, 1]$**（对应 `metrics.json["score"]`，仅当 `score_valid == true` 且 `evaluation_error == false` 时发布至 `reward.json`）：
   $$S_t = 0.10\,I_{\mathrm{G}} + I_{\mathrm{L}}\bigl(0.10 + 0.10\,I_{\mathrm{D}}\bigr) + E_t\Bigl[0.20\min\!\Bigl(1,\frac{\rho_t}{\rho_t^\star}\Bigr)^{\!2} + 0.10\,Q_t + 0.40\,F_t\Bigr]$$
   - $I_{\mathrm{G}}\in\{0,1\}$：`submission/layout.gds` 是否成功冻结为常规文件（`submission_present`；缺失或符号链接/冻结失败记 `missing_submission` / `invalid_submission`，满足 $Y^\star(x,\varnothing)=0$）。
   - $I_{\mathrm{L}}\in\{0,1\}$：LVS 是否通过（`38` 题 KLayout，含 `flag_missing_ports` 顶层端口绑定、`ignore_extraction_errors(true)` 日志审计及失配时 `sky130_dummy_purge_20261103_v2.lvs` 回退；`2` 题 `#2, #10` 为 Magic+Netgen）。**注意 DRC 得分 $0.10\,I_{\mathrm{D}}$ 受 $I_{\mathrm{L}}$ 门控**：LVS 失败时即使 DRC 通过也仅得 $0.10$，且 `quality`、`pex`、`postsim` 阶段全部短路跳过。
   - 资格门 $E_t\in\{0,1\}$（`bonuses_eligible`）：要求 $I_{\mathrm{G}}=I_{\mathrm{L}}=1$、几何与形态判据均已知（`layout_gate_known=true`），且以下三项阻断均不触发：（a）制造层外接矩形长宽比 $q_t = \max(W,H)/\min(W,H) \le q_t^{\max}$（全库 `23` 题 $q_t^{\max}=3.0$，`17` 题 $q_t^{\max}=4.0$）；（b）有效器件面积比 $\rho_t = A_{\mathrm{eff}}/A_{\mathrm{bbox}} \ge u_{\min}=0.10$（`40/40` 题统一）；（c）非退化形态筛查（`meaningless=false`：`klayout` 题需不同时满足 `bus_nets>=5`、`drops>=5`、`largest_row_fraction>=0.7`、`row_elongation>=4.0`；`2` 题 `magic_netgen` 获 `morphology_policy_exempt=true` 豁免）。
   - 面积完成度：$\gamma=2$（`40/40` 题固定），目标密度 $\rho_t^\star \in [0.60, 1.10]$；$A_{\mathrm{eff}}$ 为 LVS 匹配后提取物理器件识别区之和（MOS 为 $WL+A_{\mathrm{S}}+A_{\mathrm{D}}$，R/C 为提取器 `A`，BJT 为 C/B/E 识别区并集）。
   - 工程质量 $Q_t\in[0,1]$：依任务隐藏标注对 `matching/critical/power` 按权重 `(0.40, 0.45, 0.15)` 独立饱和 `(0.80, 0.80, 0.90)` 后乘以 $(1 - p_{\mathrm{coupling}})$；`2` 题 `magic_netgen` 通过 LVS 后直接给 `policy_credit`（$Q_t=1.0$）。
   - 后仿真通过率 $F_t\in[0,1]$（`postsim_fraction.summarize`）：Magic RC PEX 成功（`pex_success=true`）后，必选评分分析项（`evaluation_case_ids`，排除仅用于参考网表的 `qualification_case_ids`）通过数除以总必选数；内部软预算 `ALB_VERIFIER_BUDGET_SEC=3300s` 到期时保留已完成项得分，未完成项留在分母（`score_is_lower_bound=true`）。
2. **完全验收二值指示 $C^\star(x, W)\in\{0,1\}$**（`metrics.json["design_passed"]`）：
   $$C^\star(x, W) = \Ind\bigl[\text{score\_valid} \land \neg\text{score\_is\_lower\_bound} \land E_t=1 \land I_{\mathrm{D}}=1 \land F_t=1\bigr]$$
   注意：`artifact_contract_complete`（`submission/src/*.py` 非空）记入审计字段而不直接一票抹零 `design_passed`，但 `H_base` 仍强制维护其完整。

### 1.2 短路三值分项向量 $\mathbf{d}^\star \in \{0,1,\bot\}^8$ 与 $\bot$ 产生条件 `[事实]`

依据 `evaluate.py:528-668`，ALW 签核诊断向量实例化为 $K=8$ 个正交分项，严格区分未过（`0`）与因前置短路或预算耗尽而未予评估（$\bot$）：

| 分项 $d^\star_k$ | 对应 `metrics.json` 判据 | `1`（通过） | `0`（实测未通过） | $\bot$（未评估 / 严格排除出 $\mathcal{S}^+_k, \mathcal{S}^-_k$） |
|---|---|---|---|---|
| $d^\star_{\mathrm{gds}}$ | `submission_present` | GDS 冻结成功 | `missing_submission` / `invalid_submission` | 仅当冻结前发生 `evaluation_error` |
| $d^\star_{\mathrm{drc}}$ | `stages.drc.status` | `passed` | `failed`（含设计错误/超时） | `skipped`（无合法 GDS）或 `error` / 前序 `budget_exhausted` |
| $d^\star_{\mathrm{lvs}}$ | `stages.lvs.status` | `passed` | `failed` | `skipped`（无合法 GDS）或 `error` / 前序 `budget_exhausted` |
| $d^\star_{\mathrm{shape}}$ | `scoring_inputs.shape_ok` | `geometry_valid` 且 $q\le q_t^{\max}$ | `area.status=="failed"` 或 $q > q_t^{\max}$ | `area.status=="skipped"` 或 `error` / `budget_exhausted` |
| $d^\star_{\mathrm{fill}}$ | `scoring_inputs.low_fill` | `lvs_pass` 且 $\rho_t \ge 0.10$ | `lvs_pass` 且 $\rho_t < 0.10$ | **`lvs_pass == false`（外层不计算有效器件比）** 或 `area` 未测 |
| $d^\star_{\mathrm{morph}}$ | `scoring_inputs.meaningless` | `false` 或 `morphology_policy_exempt` | `meaningless == true` | **`lvs_pass == false`（`quality` 阶段 `skipped`）** 或 `quality` 为 `partial/error` 致 `meaningless == null` |
| $d^\star_{\mathrm{pex}}$ | `stages.pex.status` | `completed` | `failed`（或可归因 `execution/design/timeout`） | **`lvs_pass == false`（`pex` 阶段 `skipped`）** 或 `budget_exhausted` / `verifier_error` |
| $d^\star_{\mathrm{postsim}}$ | `postsim_all_passed` / `coverage` | `pex_success` 且 `coverage==1` 且 `all_passed` | `pex_success` 且（`failed_case_count > 0` 或完整跑完未全过） | **`pex_success == false`（`skipped`）** 或 `budget_exhausted` 下已测项全过但 `coverage < 1.0` |

### 1.3 两类失败缺口解剖与实例剖面定型 `[事实 + 判断]`

- **执行缺口（Execution Gap — ALW 主导缺口）**：（1）超时或末尾覆盖导致交白卷/坏档（基准论文报告总提交率 91.1%、GLM-5.3 Flash 超时 51/120 次）；（2）未按 `GUIDE.md` 正确调用 KLayout DRC/LVS、混淆 `circuit.spice` 与 `lvs_reference.spice`（33/40 题提供）、遗漏顶层端口标签或错误使用 C-only PEX；（3）**可局部计算但未被 Agent 主动校验的隐式资格门槛**：长宽比 $q$（`instruction.md` 40 题均未明文提及 `aspect` 或 `q_max`）、填充率 $\rho \ge 0.10$、总线单排退化形态 `meaningless`（论文 §6.1 典型案例即过 DRC/LVS/仿真却因 $q=22\text{--}26$ 仅得 0.3 分）。
- **信息缺口（Information Gap — 局部存在）**：（1）隐藏测试判据与精确上下限位于 `tests/task/testbench/{measurement.json, processor.py, adapter.py}`（`38/40` 题 `instruction.md` 披露了评测覆盖范围，但具体数值限值与衍生处理脚本对 Agent 不可见，且 `#13, #27` 未列覆盖小节）；（2）工程质量标注 `src/eda/quality/annotations/*.json` 对 Agent 不可见；（3）单题精确 $q_t^{\max}\in\{3,4\}$ 与 $\rho_t^\star\in[0.60,1.10]$ 未写入 `instruction.md`；（4）外层 `sky130_dummy_purge_20261103_v2.lvs` 仅在验收集内存在。

| MCR 剖面维度 | ALW 客观可核验特征 `[事实]` | 机械决定的 MCR 算法工作模式 `[判断]` |
|---|---|---|
| **监督档位** | **无官方参考版图 $w_{\mathrm{ref}}(x)$**（仅固定网表 `circuit.spice`）；暴露多分项 $K=8$（`metrics.json` 含 `d*_k`）；$Y^\star=S_t\in[0,1]$ 为**连续+阶跃分级效用** | **关闭**基于 $w_{\mathrm{ref}}$ 的双端干预合成；**启用**主动裁判库 $\mathcal{B}_{\mathrm{dev}}$（含规则破坏负例与等价版图正例合成），Stage 0 对第 II 类探针保留 $\mathcal{S}^+$ 零误杀与盲簇捕获检验；验证与测试**强制拆报 $C^\star$ 翻转贡献与未翻转进展贡献** |
| **成本与样本档位** | **重型（Heavy）**：单题 Agent 预算 `10800s`（3h）+ Verifier `3600s`（1h），`6 CPU / 32 GiB`，模型消耗约 `11M–38M tokens/次`（DeepSeek V4.1 Flash + Claude Code 论文均值 `13.2M`）；锁定切分下 $G_{\mathrm{dev}}=10 \ge 8$ | **禁用**昂贵全局提示词自动演化和请求级逐步重放；聚焦 **第 I 类镜像检查器、单参数 $\epsilon_k$ 校准、第 III 类日志映射与终局无状态补救重放（Path 1）**；Stage 0 因 $G_{\mathrm{dev}}=10\ge 8$ **启用内部盲簇模式（冻结 3/10 簇）** |
| **宿主交互形态** | **Harbor 0.24.0 Docker 容器内封装黑盒 CLI（`claude-code 2.1.302`）**，通过 `settings.json` 挂载钩子、`files/` 只读挂载至 `/opt/harness`、容器退出后独立断网校验 | **禁用**会话中途状态反序列化与 `--resume`；统一采用**自由草稿区 + 写穿透原子棘轮 `H_base` + 主进程退出快照上的独立无状态子调用补救** |

| MCR 模块 / 通道 | 启用状态 | ALW 实例化决策与理由 |
|---|---|---|
| 固定保底内核 $H_{\mathrm{base}}$（双水位线 + 写穿透棘轮） | **强制启用** | 防 3h 超时丢分与末尾改坏（`91.1% -> ~100%` 合法 GDS 交付基础） |
| 主动裁判库 $\mathcal{B}_{\mathrm{dev}}$ + 合成正负样本 | **强制启用** | 无 $w_{\mathrm{ref}}$ 时靠破坏 `q/rho/DRC/LVS` 合成负例与偏移/翻转等价改写正例扩充校验集 |
| 内部盲簇防过拟合（Stage 0） | **强制启用（3/10 簇）** | $G_{\mathrm{dev}}=10 \ge 8$，冻结 3 簇（~3–4 题）仅回传聚合计数 |
| Path 1（终局门控/补救重放）与 Path 2（条件钩子+前缀泄漏审计） | **主力启用** | 直接利用归档快照，免除未触发题重跑方差 |
| Path 3（全局指令/工具全量重跑演化） | **冻结关闭（仅人工对照）** | 重型小样本档下单题 3h+千万 token，全量自动变异方差压过信号；仅保留 1 份人工手写说明作为诚实基线 |
| 双端黑盒参考补丁判别（$w_0, w_{\mathrm{ref}}$） | **禁用** | ALW 无参考 GDS 补丁 |
| 搜索前四项体检中的"轨迹内峰值遗憾" | **按实测决定** | Phase 0/1 对比草稿里程碑与终态 $Y^\star$ 余量，若余量近零则关闭多检查点选优 |

---

## 2. 40 题簇/任务切分与防泄漏核查

### 2.1 对 `method-v5/experiments/alw/{split.json, catalog.json}` 的只读审计 `[事实]`

经只读 Python 脚本核查 `method-v5/experiments/alw/{split.json, catalog.json}` 与 `external/AnalogLayoutWorld/repo/{configs/paper-v1/sources.json, tasks/*/tests/task/*}`，发现旧切分在**簇隔离**与**模型库覆盖**上存在实质性缺陷：

1. **旧切分构造方式**（`method-v5/src/method_v5/targets/alw/catalog.py:182-202`）：`split()` 仅按 `(器件数三分位, source, sha256(seed:task_id))` 在单题粒度上配额切分（`12 / 14 / 14`），**未按底层共享网表、子电路或上游同源项目归簇**。
2. **实测跨切分簇泄漏与分布偏斜**：
   - **硬件/拓扑簇定义（35 个硬簇，30 个单题簇 + 5 个 2 题簇）**：
     1. `cl_ajc_por`（`#32 rc_osc`, `#33 schmitt_trigger`）：同属上游仓库 `ajcci/sky130_ajc_ip__por`（提交 `db8745ef`），且 `#33` 的 8 管施密特触发器原样嵌入 `#32` 内部（`XM1..XM6, XMdum0..1` 尺寸全同）。
     2. `cl_adb_sampling_fb_ota`（`#11 gain1`, `#12 gain8`）：共享顶层 `.subckt sampling_feedback_ota` 与完整电路拓扑，仅规格与并行管数拆分不同（40 vs 45 器件）。
     3. `cl_adb_cp_10mhz`（`#24 charge_pump_regulated`, `#31 charge_pump_unregulate`）：共享完全相同的底层标准单元 `.subckt IVD4A`、`.subckt ND2D1A` 与 4-NVT + 2-MIM 交叉耦合 10 MHz 电荷泵核心。
     4. `cl_adb_miller_opamp`（`#29 gain60-ugb200`, `#30 gain70-ugb20`）：共享顶层 `.subckt two_stage_miller_opamp`、端口契约及 ADB Miller 运放同系规格族（`#30` 第一级增设共源共栅管）。
     5. `cl_adb_gmr_degen`（`#15 constant-gm`, `#16 gain2-bw70m`）：共享顶层 `.subckt gmr_degen`、8 端口接口及 ADB 源极退化 GM-R 放大器族。
   - **保守扩展簇定义（32 个保守簇）**：在 35 个硬簇基础上，进一步合并仅共享经典教材名称与端口族的 `cl_fc_ota_name`（`#14 adb-folded-cascode-ota` 与 `#39 local-fc-ota`）及同批次来源 `cl_loadbench_batch`（`#34, #35, #36`，LOADBench Zenodo 21759759）。
   - **旧切分泄漏清单**：`method-v5/split.json` 下 `G_dev=12, G_val=13, G_test=13`（求和 38 > 35），存在 **3 组跨边界硬泄漏**：`cl_adb_miller_opamp`（`#29` 在 `dev` $\leftrightarrow$ `#30` 在 `val`）、`cl_adb_gmr_degen`（`#16` 在 `val` $\leftrightarrow$ `#15` 在 `test`）、`cl_adb_cp_10mhz`（`#24` 在 `val` $\leftrightarrow$ `#31` 在 `test`）；保守定义下另增 `cl_loadbench_batch`（`dev <-> val`）与 `cl_fc_ota_name`（`val <-> test`）。
   - **PDK / 模型库偏斜**：全库 40 题均使用 SKY130A，但分为 `continuous`（31 题）与 `binned`（9 题）两套 ngspice 库；`method-v5` 的 `dev` 集含 **`0/12` 个 `binned` 题**（全部 9 题落在 `val` 4 题与 `test` 5 题），导致搜索阶段完全盲测不到 `binned` 仿真语义差异（如 `scale` 与库语法）。

### 2.2 `ssap` 锁定簇隔离切分契约（`configs/alw/split.json`，种子 `ssap-alw-v2-19`） `[判断 + 事实]`

在启动任何模型轨迹前，按**32 个保守簇（从而同时严格包含 35 个硬簇）**执行确定性分层切分（按 `binned` 占比 $\times$ 簇均器件数三分位 $\times$ SHA-256 哈希排序配额），锁定切分方案于 `configs/alw/split.json`：

| 切分 | 题数 | 硬簇数 ($G_{\mathrm{hard}}$) | 保守簇数 ($G_{\mathrm{cons}}$) | `binned` / `continuous` | `magic_netgen` / `klayout` | $q^{\max}=3$ 题数 | 器件数 中位（范围） | 包含任务编号（`configs/alw/catalog.json`） |
|---|---|---|---|---|---|---|---|---|
| `dev` | **12** | **10** | **10** | 3 / 9 | 1 (`#2`) / 11 | 8 | 26.5 (6–59) | `2, 7, 11, 12, 13, 15, 16, 18, 19, 21, 27, 38` |
| `val` | **14** | **14** | **12** | 3 / 11 | 0 / 14 | 8 | 21.5 (7–50) | `1, 3, 5, 6, 8, 17, 20, 22, 25, 26, 34, 35, 36, 37` |
| `test` | **14** | **11** | **10** | 3 / 11 | 1 (`#10`) / 13 | 7 | 23.0 (8–118) | `4, 9, 10, 14, 23, 24, 28, 29, 30, 31, 32, 33, 39, 40` |
| **合计** | **40** | **35** | **32** | **9 / 31** | **2 / 38** | **23** | 24.0 (6–118) | 跨切分簇重叠数 = **0**（硬簇与保守簇均严格 disjoint） |

**核心阈值核验与 MCR 行为锁定 `[事实 + 判断]`**：
- **Stage 0 内部盲簇分支**：因 **$G_{\mathrm{dev}} = 10 \ge 8$**（无论按硬簇还是保守簇均 $=10$；仅 `{#11,#12}` 与 `{#15,#16}` 为 2 题同簇，其余 8 题为单题簇），依据 `mcr-formal.tex` 第 3 节机械启用**内部盲簇校验模式**——在 `H_base` 建库前由确定性哈希从 10 个 `dev` 簇中永久冻结 **3 个内部盲簇**（约 25%–30%，即 `3–4` 题）。提议与实现 Agent 全程不可读取盲簇的题目 ID、轨迹、GDS 与日志，Stage 0 仅回传盲簇上的聚合通过/误拦/捕获计数；盲簇仍参与 Stage 1 闭环记分。
- **测试集自助法校准警示线**：$G_{\mathrm{test}} = 11$（硬簇）/ $10$（保守簇），刚好达到 $G_{\mathrm{test}} \ge 10$；报告仍按簇做自助法区间并并列逐簇原始净差。
- **对比说明**：由于 `method-v5` 从未在 ALW 上运行过任何真实模型实验，锁定 `ssap-alw-v2-19` 不存在历史结果作废成本，且消除全部 3 项硬簇泄漏与 `dev` 零 `binned` 盲区。

### 2.3 `ssap` 切分契约与静态防泄漏硬规则 `[判断]`

1. **三集物理锁定**：`configs/alw/{catalog.json, clusters.json, split.json}` 在首次模型调用前写入 Git 并记录 SHA-256 摘要；运行命令 `ssap alw-check-split` 在每次实验启动前校验以下 5 条不变量：
   - （R1）40 题全覆盖且无重漏；`dev/val/test` 间硬簇、保守簇、共享非 PDK `.subckt` 名（排除标准器件库后）、共享非 ADB 上游仓库（`ajc_ip__por`, `LOADBench`）交集全为空；
   - （R2）`val` 与 `test` 任务 ID、电路参数（如特定 `task_id`、特定题目 $q_t^{\max}/\rho_t^\star$ 查表）及 `external/AnalogLayoutWorld/repo/tasks/*/tests/` 路径严禁出现在任何 Harness 源码、提示词或静态常量黑名单中（静态 AST 扫描拦截）；
   - （R3）提议/实现 Agent 的工作提示与诊断输出仅读取 `dev` 非盲簇（7 个可见簇）及盲簇聚合计数；严禁从 `ssap` 包内 `import eda` 或 `import analoglayoutworld`（仅以只读数据形式读取任务配置）；
   - （R4）$\mathcal{B}_{\mathrm{dev}}$ 样本覆盖度一律按 `dev` 独立簇数（上限 10）而非重试次数或快照条数报告；
   - （R5）`val` 仅在阶段末由程序运行纯净 `C1` 单一胜出者比较，仅回传配置标识；`test` 全程封存，代码哈希冻结后单次揭盲并运行 $R\ge 3$ 个随机种子。

---

## 3. 保底内核 $H_{\mathrm{base}}$ 最小实现设计

固定保底内核 $H_{\mathrm{base}}$（对应代码模块 `src/ssap/kernel/` 与 `src/ssap/checkers/alw/`）由宿主机 `agent_wrapper.py` 通过容器只读挂载注入 `/opt/harness/bin/` 与 `/opt/harness/python/ssap/`（容器脚本设置 `PYTHONPATH=/opt/harness/python:/opt/analog-layout/pdks/sky130/python`，无需容器内 `pip install`）；内核人写一次、永不参与外层自动变异，是全实验序列的唯一起点与强制对照基线。

### 3.1 草稿区与规范提交区物理隔离及写穿透安全机制 `[判断]`

1. **目录职责隔离（严禁步级强制回滚）**：
   - **Agent 自由草稿区**：`/alb-workspace/work/draft/`（`layout.gds`、`src/*.py`）及中间实验目录 `/alb-workspace/work/{drc-*,lvs-*,pex-*,sim-*}/`。Agent 可自由写入、重构、实验或暂时破坏任意中间文件；**内核严格禁止任何形式的步级强制自动回滚**（历史检查点回退仅作 Agent 自愿调用工具存在）。
   - **规范提交区**：`/alb-workspace/submission/`（`layout.gds`、`src/*.py`、可选 `README.md`），是 Harbor `task.toml` 结束时唯一拷贝至宿主机 `artifacts/submission/` 的目录。
   - **内核保底与暂存区**：`/alb-workspace/.mcr/`（与 `submission/` 处于同一容器文件系统 `/alb-workspace`，保证单文件 `os.replace()` 满足同文件系统原子性而非 `EXDEV` 跨挂载失败），下设：
     - `input_hashes.json`：容器启动瞬间计算的 `/alb-workspace/task/` 与 `eda_env.sh` 全部文件 SHA-256 基准；
     - `task_prompt.txt`：`mcr_entrypoint.py` 启动 `claude` 时捕获的 Harbor 初始任务提示文本（因全部 40 题 `Dockerfile` 均不复制 `instruction.md` 进容器，且 `constraints.json` 仅在 `tests/task/` 内）；
     - `best/`：当前按棘轮序 $\succ$ 认证最高的历史最优完整提交拷贝（`layout.gds`、`src/`、`cert.json`）；
     - `staging/`：原子写穿透暂存目录。
2. **原子写穿透与直接覆写防护（`/opt/harness/bin/mcr_promote` + `mcr_hook.py`）**：
   - Agent 每当生成阶段性版本，在 `work/draft/` 落盘后调用 `mcr_promote --gds work/draft/layout.gds --src work/draft/src`；
   - **直接覆写保底防线**：Claude Code 的 `PostToolUse` 钩子（`mcr_hook.py`）在每次 `Bash|Write|Edit` 调用后核对 `/alb-workspace/submission/layout.gds` 的 SHA-256 与 `.mcr/best/cert.json` 是否一致。若发现 Agent 绕过 `work/draft/` 直接改写了 `submission/layout.gds`，钩子**先将 Agent 新文件完整保存至 `work/draft/_direct_snapshot/`（绝不丢失探索成果）**，再以之为候选 $w_{\mathrm{new}}$ 跑一次 `mcr_promote`：若 $w_{\mathrm{new}} \succ w_{\mathrm{best}}$ 则认证晋升，否则将 `submission/` 原子恢复为 `.mcr/best/`。
   - **原子落盘序（兼容 POSIX 目录约束与零 symlink 限制）**：唯有判定 $w_{\mathrm{new}} \succ w_{\mathrm{best}}$ 时，先在 `.mcr/staging/<uuid>/` 写入完整 `layout.gds`、`src/` 与 `cert.json`（禁止使用 symlink，对齐 `evaluate.py`/`runner.py` 拒收链接约束）并校验大小与 `klayout.db` 可读性；再更新 `.mcr/best/`；随后对单文件执行 `os.replace(staging_gds, "/alb-workspace/submission/layout.gds")`；对非空目录 `submission/src/`（POSIX `os.replace()` 不可直接原子覆盖非空目录）执行同文件系统分步交换：`os.rename("/alb-workspace/submission/src", staging/"src.old")` $\to$ `os.rename(staging/"src", "/alb-workspace/submission/src")` $\to$ `shutil.rmtree(staging/"src.old")`（首次为空时直接移入）。无论容器何时断电，`submission/` 永不存在半写损坏或回退空档（满足 `mcr-formal.tex` 不变量 1）。

### 3.2 第 I 类镜像检查器与第 II 类启发探针的严格分权 `[判断]`

| 权限类别 | 对象示例 | 允许行为 | 绝对禁令 |
|---|---|---|---|
| **第 I 类：镜像检查器**（仅此类具覆写权） | $\mathbf{m}_{\mathrm{base}} = (m_{\mathrm{gds}}, m_{\mathrm{lvs}}, m_{\mathrm{shape}}, m_{\mathrm{fill}}, m_{\mathrm{morph}}, m_{\mathrm{drc}})$ 及后续经 Stage 0 一致率核验的 $m_{\mathrm{pex}}/m_{\mathrm{postsim}}$ | 进入认证向量 $\mathbf{m}$；判定棘轮晋升序 $\succ$；以合法性依据决定是否覆写 `submission/`；触发终局补救 | 禁止使用未经一致率核验的经验阈值；禁止使用任何任务 ID/常量黑名单 |
| **第 II 类：启发探针**（仅参考参谋） | `advisory_aspect_gt_3`（$3.0 < q \le 4.0$）、LVS 失败前的 `W*L` 粗估面积密度、非镜像后仿启发告警、局部匹配/布线建议 | 向 Agent 输出结构化文字提示（`additionalContext`）；在 `T_soft` 时触发一次**受棘轮保护的**补救尝试（若补救破坏 $\mathbf{m}$ 或无法同层保底则自动丢弃补救产物） | **零覆写权**：绝对禁止进入 $\mathbf{m}$、禁止单独作为晋升判据、禁止否定或降级已通过第 I 类检查的 `w_best` |

### 3.3 面向 ALW 的镜像检查向量 $\mathbf{m}_{\mathrm{base}}$ 精确定义与离线一致率协议 `[事实 + 判断]`

镜像检查器（`src/ssap/checkers/alw/`）完全以容器本地工具与公开文件重新实现，**零引用** `external/AnalogLayoutWorld/repo/src/` 代码。其中运行期所需三项元信息均无需访问宿主机 `tests/task/constraints.json` 或磁盘 `instruction.md`（全库 `40/40` 验证精确匹配）：（1）`lvs_backend` 通过解析 `/alb-workspace/.mcr/trusted_task/eda_env.sh`（容器启动 `T=0` 只读冻结副本）是否导出 `ALB_NETGEN_BIN` 判定（仅 `#2, #10` 导出，40/40 精确）；（2）`top_cell` 与 `ports` 通过解析 `trusted_task/circuit.spice` 带单引号表达式感知的嵌套作用域调用图唯一根（如 `#37` 中 `bgr_ota` 嵌套于 `bandgap` 内），并与 `.mcr/task_prompt.txt`（`mcr_entrypoint.py` 自 Harbor `stdin` 捕获，`with top cell \`...\`` 匹配 40/40）互校确认；（3）`lvs_sub` 通过跨子电路层次追踪顶层端口连接的 NMOS 器件衬底端多数出现网络名提取（精确保留端口大小写，含 `#10 vssana`、`#38 gnd`、`#34/#35/#36 sourceNmos`、`#28 div2` 层次调用，40/40 精确匹配）。

| 分量 | 依赖与短路语义 | 精确计算方式（对齐外层 `verify.py / evaluate.py`） | 原始裕量 $\tilde{u}_j$ 与初始保护带 $\epsilon_j$ | 单次实测预设耗时 `[暂定]` |
|---|---|---|---|---|
| **$m_1$: `m_gds`**（Tier 1 前置；附 Tier 0 非空常规文件保底） | 无前置依赖 | 基于 `T=0` 冻结副本 `.mcr/trusted_task/` 隔离工作区误改（工作区 `task/` 变动与缺失 `src/*.py` 均列为第 II 类 `advisory_task_modified` / `advisory_missing_src_py`，因外层不将其计入 $C^\star=1$ 扣分，从而严格保证 $C^\star=1 \implies m_1=1$）；校验 `layout.gds` 无 symlink、`klayout.db` 可读、`top_cell` 精确属于 `layout.top_cells()`、`dbu>0`、`MANUFACTURING_LAYERS`（对齐 `verify.py:26-39` 全 **38** 层定义）递归正面积 bbox 非空且宽/高 $>0$。另设 Tier 0 保底位 `file_present=1`：当 `.mcr/best/` 为空（$\varnothing$）时，任何常规非空 `layout.gds`（外层至少得 $0.10$ 分）均优先于 $\varnothing$（$0.00$ 分），绝不回退删空 | $\tilde{u}_1 \in \{0.0, -1.0\}$；$\epsilon_1=0.0$ | `< 0.2 s` |
| **$m_2$: `m_lvs`**（Tier 2 前置） | 需 $m_1=1$；否则 $m_2=0$（库中记 $\bot$） | 依 `eda_env.sh` 判定后端：含 `ALB_NETGEN_BIN` 则跑 `geometry_only_gds` + Magic `ngspice()` 提取 + Netgen `-batch`（`netgen_report`）；否则跑 `sky130.lvs`（`run_mode=deep`, `convert_subckts=true`, `scale=true`, `schematic_simplify=true`, `spice_net_names=true`, `spice_comments=true`, `verbose=true`, `lvs_sub=<substrate>`, `thr=2`）并注入 `ignore_extraction_errors(true)` + `flag_missing_ports`（校验 `returncode==0`、`ports.json`、无 `Error`/`must-connect` 日志、唯一 `top_pair` 及全部电路对 `Match` 且器件数 $>0$），对照 `lvs_reference.spice`（若有）或 `circuit.spice`，其中 `floating_reference_ports` 始终在未改写 `circuit.spice` 上计算 | $\tilde{u}_2 \in \{0.0, -1.0\}$；$\epsilon_2=0.0$。<br>**差异说明**：外层失败时会回退私有 `sky130_dummy_purge_20261103_v2.lvs`，内层仅跑原版，故内层 $1\implies$ 外层 $1$（单侧保守，零假阳性） | `1–15 s`（`klayout`）；`5–30 s`（`magic_netgen`） |
| **$m_3$: `m_shape`**（Tier 3） | 需 $m_1=1$；否则 $0$ | 计算 $q = \max(W_{\mu\mathrm{m}}, H_{\mu\mathrm{m}}) / \min(W_{\mu\mathrm{m}}, H_{\mu\mathrm{m}})$。因全库 $q_t^{\max}\in\{3.0,4.0\}$ 且容器工作区不披露单题 $q_t^{\max}$，取全局必要上界 $q \le 4.0$（若在 $\mathbf{m}$ 内写死 $3.0$ 将在 17 道 $q_t^{\max}=4.0$ 题上误拒真解，破坏不变量 2）。同时设第 II 类建议探针 `advisory_aspect_gt_3` 警告 $3.0<q\le 4.0$ | $\tilde{u}_3 = 4.0 - q$（认证门裕量 $u_3 = \min(0.0, \tilde{u}_3 - \epsilon_3)$，原始 $\tilde{u}_3$ 存入 `cert.json`）；**$\epsilon_3=0.0$ 固定**（**严禁**将 $q_t^{\max}=3.0$ 题目在 $q\in(3.0,4.0]$ 的失配喂入 $\epsilon_3$ 校准） | `< 0.05 s`（复用 $m_1$） |
| **$m_4$: `m_fill`**（Tier 3） | 需 $m_1=m_2=1$；$m_2=0$ 时短路为 $0$ | **关键源码细节**：外层 `area_extracted.py` 对 MOS 计 $WL+A_{\mathrm{S}}+A_{\mathrm{D}}$，而非预布局 `area_v2` 的纯 $WL$！`klayout` 后端直接解析 `report.lvsdb`（`scale=true` 下按 `1e6`/`1e12` 换算 MOS $WL+A_{\mathrm{S}}+A_{\mathrm{D}}$、R/C `A` 与 BJT C/B/E 并集，并在遇到不支持器件类或源清单失配时同外层 fail-closed，镜像 `area_extracted.py`）；`#2/#10` 解析 Magic `extracted.spice`（`scale off`/`merge none` 已直接为 $\mu\mathrm{m}/\mu\mathrm{m}^2$，镜像 `area_magic_extracted.py`），得 $\hat{\rho}=A_{\mathrm{eff}}/A_{\mathrm{bbox}}$ 并核对 $\hat{\rho} \ge 0.10$（`40/40` 题通用下限） | $\tilde{u}_4 = \hat{\rho} - 0.10$（认证门裕量 $u_4 = \min(0.0, \tilde{u}_4 - \epsilon_4)$，原始 $\tilde{u}_4$ 存入 `cert.json`）；初始 $\epsilon_4=10^{-4}$（若 $\mathcal{B}_{\mathrm{dev}}$ 无贴线假阳性则置 $0.0$） | `< 0.5 s`（解析已有 `lvsdb`） |
| **$m_5$: `m_morph`**（Tier 3） | 需 $m_1=m_2=1$；$m_2=0$ 时短路为 $0$ | `magic_netgen` 依公开策略置 $1$；`klayout` 依 `layout.gds` + `report.lvsdb` 复现 `meaningless.py` 无标注四条件联合筛查（信号线总线数 $\ge 5 \land$ 垂直落线 $\ge 5 \land$ 最大行器件占比 $\ge 0.7 \land$ 行长厚比 $\ge 4.0$ 时判 `0`） | $\tilde{u}_5 \in \{0.0, -1.0\}$；$\epsilon_5=0.0$ | `0.5–3 s` |
| **$m_6$: `m_drc`**（Tier 3） | 需 $m_1=1$；否则 $0$ | 调用 `sky130A_mr.drc`（`feol=true beol=true offgrid=true floating_met=true sram_exclude=false seal=false info=false thr=2`），解析 `report.lyrdb` 统计违例条数 $N_{\mathrm{drc}}$ | $\tilde{u}_6 = -N_{\mathrm{drc}}$（$0$ 违例时为 $0.0$）；$\epsilon_6=0.0$ | `1–15 s` |

**零参数棘轮晋升序 $\succ$（分三层，严密契合 $S_t$ 结构与 `mcr-formal.tex` 规则 2(b)）**：
- **第 0/1 层（Tier 0 非空文件保底 & Tier 1-2 严格字典序）**：先比较 `file_present`（防 `0.10` 分常规文件被空仓 $\varnothing$ 删回 `0.00` 分），再依次比较 $m_1$（`m_gds`）与 $m_2$（`m_lvs`），因 $m_1=0$ 时无法进入 LVS/DRC、$m_2=0$ 时外层锁死于 $0.10$ 且短路后续全部 5 项分项。
- **第 2 层（Tier 3 帕累托序）**：当 $(m_1, m_2)$ 相等时，比较 $\mathbf{m}_{3:6}=(m_{\mathrm{shape}}, m_{\mathrm{fill}}, m_{\mathrm{morph}}, m_{\mathrm{drc}})$，采用**帕累托支配**（各分量 $\ge$ 且至少一维 $>$）杜绝 DRC 与资格门 $E_t$ 相互逆向覆写。
- **第 3 层（同层级非严格裕量单调序，严格对齐 `mcr-formal.tex` 规则 2(b) `>=`）**：当 $\mathbf{m}_{\mathrm{base}}(w_{\mathrm{new}})=\mathbf{m}_{\mathrm{base}}(w_{\mathrm{best}})$ 且 $\hat{y}_{\mathrm{mir}}=\text{None}$ 时，各二值门槛裕量 $u_j = \min(0.0, \tilde{u}_j - \epsilon_j)$ 在已达标项上饱和为 $0.0$，在未达标项上保留连续负距离（如 $-N_{\mathrm{drc}}$）；当且仅当所有有效门裕量均不下降（$\forall j: u_j(w_{\mathrm{new}}) \ge u_j(w_{\mathrm{best}})$，且未全部达标时至少一项改善或已达 $\mathbf{m}=\mathbf{1}$ 时保持全部达标）时允许新候选晋升。这样既拦截任何破坏已通过镜像门的倒退修改，又允许 Agent 在达成 $\mathbf{m}_{\mathrm{base}}=\mathbf{1}$ 后为修复后仿真（$F_t$ 权重 $0.40$）合法微调版图而不被误回滚！直接覆写恢复时钩子通过 `additionalContext` 显式告知 Agent 已恢复及快照保存路径 `work/draft/_direct_snapshot/<seq>_<sha8>/`，并采用文件锁与超时“不作降级判定”保护。
- **Pre-Sim 锁定权衡与 Phase 2 演进接口 `[判断]`**：由于 `H_base` 暂未将耗时较长的 `m_pex` / 公开 `m_postsim` 放入 $\mathbf{m}_{\mathrm{base}}$，若某任务在早步已达 $\mathbf{m}_{\mathrm{base}}=\mathbf{1}$ 但 $F_t=0$，而后期为修复仿真略微增大外接框导致 $\hat{\rho}$ 微降，则同层单调规则会保守拦截该覆盖以防反向破坏已有后仿。此现象恰好精确定位了 Phase 2 通过 Stage 0 一致率审计将 `m_pex` 与公开 `m_postsim` 升格为第 I 类镜像组件的核心增益空间。

**离线一致率验证协议（`ssap alw-concordance`）**：在进入 Phase 1 全量基线前，从早期探索与合成正负变体（改错 top 名、漏标端口、人为破坏 DRC、长宽比设为 `3.5` 与 `4.5`、填充率缩至 `0.08`、触发总线单排形态、注入同网 dummy 管）构造测试集，同时运行 `ssap` 容器镜像检查与断网 `alw verify`（`evaluate.py`）：
1. 在排除 $\bot$ 的样本上，分项核验三类精确一致条件：
   - $m_1, m_4, m_5, m_6$ 满足双向零假阳性与真解零误拒（$m_k=1 \implies d^\star_k=1$ 且 $C^\star=1 \implies m_k=1$）；
   - $m_2$ 满足单侧保守零假阳性（$m_2=1 \implies d^\star_{\mathrm{lvs}}=1$；仅当外层私有 `dummy_purge` 回退生效时可出现 $m_2=0, d^\star_{\mathrm{lvs}}=1$）；
   - $m_3$ 满足真解零误拒（$d^\star_{\mathrm{shape}}=1 \implies m_3=1$；在 $q_t^{\max}=3.0$ 题上 $3.0<q\le 4.0$ 时出现的 $m_3=1, d^\star_{\mathrm{shape}}=0$ 由第 II 类 `advisory_aspect_gt_3` 捕获与触发受棘轮保护补救）；
2. 若在 `m_fill`（或在严格筛选出的 $q_t^{\max}=4.0$ 样本子集上的 `m_shape`）发现贴线假阳性，仅允许按 $\epsilon_k = \max\{\tilde{u}_k : \tilde{u}_k \ge 0 \land d^\star_k=0\} + 10^{-6}$ 校准该对应保护带；**严禁**将 $q_t^{\max}=3.0$ 任务在 $q\in(3.0,4.0]$ 上的失配输入 $\epsilon_3$ 校准（否则 $\epsilon_3$ 会膨胀至 $\sim 1.0$，在 $q_t^{\max}=4.0$ 任务上误拒 $C^\star=1$ 真解并破坏不变量 2）。`epsilon_3` 保持 `0.0` 固定（或仅限 $q_t^{\max}=4.0$ 样本核验）。

### 3.4 双层水位线、交接单、快照布局与无状态终局补救 `[事实 + 判断 + 暂定]`

1. **双层水位线配置**：
   - **已验证硬上限 `[事实]`**：Harbor `agent.timeout_sec = 10800.0`（$T_{\max} = 10800\text{ s}$），`verifier.timeout_sec = 3600.0`，`ALB_VERIFIER_BUDGET_SEC = 3300`。
   - **暂定初始水位线 `[暂定]`**（Phase 0/1 实测镜像耗时 $P_{95}$ 与单轮冷启动修复耗时后再锁定，不作自由调参）：
     - **预警线 `T_warn = 9000s`（150 min，余 30 min）**：由 `mcr_hook.py` 在跨越 `elapsed >= 9000s` 的首次工具调用后注入非阻塞提醒（`additionalContext`），督促 Agent 收敛当前修改、调用 `mcr_promote` 同步 `work/draft/` 并更新交接单 `/alb-workspace/.mcr/handoff.json`。
     - **软截止线 `T_soft = 9900s`（165 min，预留 $\Delta T_{\mathrm{rep}} = 900\text{ s}$）**：由容器入口封装 `mcr_entrypoint.py` 启动主阶段 Phase A 的硬计时器（`9900s`）。无论主 Agent 是提前正常退出（`Stop`）还是撞线 `T_soft` 被 `mcr_entrypoint.py` 干净终止（`SIGTERM` $\to$ 5s 后 `SIGKILL` 主进程组），容器仍保有 $\ge 900\text{ s}$ 缓冲才触及 Harbor 10800s 强杀。
2. **结构化轻量交接单（`/alb-workspace/.mcr/handoff.json`）**：Agent 按系统规范在里程碑及 `T_warn` 更新以下 JSON（上限约 4 KB，防上下文爆炸）：
   ```json
   {
     "schema_version": 1,
     "task_id": "sky130-...",
     "top_cell": "ota_5t",
     "status": "iterating | ready_for_signoff | blocked",
     "draft_gds_path": "/alb-workspace/work/draft/layout.gds",
     "draft_src_dir": "/alb-workspace/work/draft/src",
     "best_cert_summary": {"m": [1,1,1,1,1,1], "u": [0,0,1.8,0.22,1,0]},
     "last_checks": {"drc": "passed(0)", "lvs": "passed", "pex": "not_run", "postsim": "not_run"},
     "known_blockers": ["aspect ratio q=3.42 > 3.0; DRC clean"],
     "next_steps": ["compact placement horizontally by 15% without breaking LVS"],
     "updated_at_utc": "2026-11-23T..."
   }
   ```
3. **归档终态快照布局与 Harbor 导出机制**：
   - 因 `task.toml` 仅复制 `/alb-workspace/submission` 至 `artifacts/submission/`，且 `environment.delete=true` 会销毁容器内 `work/`，Harbor 原生宿主机绑定挂载 `/logs/agent/`（导出为 `<trial>/agent/`）直接承载 `/logs/agent/mcr_snapshot/` 跨周期存档（详见 [fable-5.2-plan-review.md](fable-5.2-plan-review.md)）：
     - `pre_repair/`（Phase A 退出且清理残留后台子进程后立即冻结，**无论是否触发补救均必写**）：完整归档 `/alb-workspace/`（含 `work/`、`.mcr/{best/, trusted_task/, task_prompt.txt, fork_meta.json, handoff.json, gate_report.json}`、`submission/`、`eda_env.sh`、`task/`，排除瞬态缓存套接字/锁），确保线上 Phase B 与离线 Path 1 重放面对 100% 同构的文件系统视图；
     - `post_repair/`（仅当 `repair_ran == true` 时写出）：补救后的 `.mcr/best/`、`submission/`、`phase_b_stream.jsonl` 及 `repair_trace.json`；
     - `artifact_summary.json` 与 `artifact_summary.md`（见 3.5 节）。
4. **门控诊断、触发条件与无状态独立补救子调用（Phase B）**：
   - Phase A 结束时（注意 Harbor 通过 `printf "$instr" | claude ... --print` 启动，`mcr_entrypoint.py` 作为 `claude` 透明包装器捕获 `stdin` 提示并管理计时），若存在尚未经 `mcr_promote` 评估过的新草稿（由 `.mcr/last_evaluated_draft.json` 去重防已拒草稿重触发），先对其执行一次 `mcr_promote`；随后 `mcr_check` 生成确定性 `/alb-workspace/.mcr/gate_report.json`（含 `phase_a_elapsed_sec` 与 `granted_repair_budget_sec`，保证离线重放与线上门控/预算完全一致）。
   - **`gate_triggered == true` 判据**：当剩余预算 `T_max - phase_a_elapsed_sec >= 180s` 且满足任一条件时触发：（a）$\mathbf{m}_{\mathrm{base}}(w_{\mathrm{best}}) \neq \mathbf{1}$（含仅 `terminal_only` 注册的扩展第 I 类门控）；（b）存在未评估的新版草稿；（c）激活的第 II 类建议触发器命中（`H_base` 中为 `advisory_aspect_gt_3`：$3.0 < q \le 4.0$）。否则直接跳过 Phase B（`repair_ran = false`，零额外 token）。
   - **无状态调用与会话目录隔离规范**：启动全新独立的真实 `claude -p` 进程（显式带 `--no-session-persistence`、**严禁 `--resume` / `--continue`**、设置独立 `CLAUDE_CONFIG_DIR=/alb-workspace/.mcr/phase_b_sessions` 与 `MCR_PHASE=B` 关闭 `T_warn`，并将 stdout 单独重定向至 `mcr_snapshot/phase_b_stream.jsonl`，防止污染 Harbor 对 `/logs/agent/sessions` 单会话 ATIF 的解析），预算受限于 $\Delta S_{\mathrm{rep}} \le 15$ 轮与 `granted_repair_budget_sec = min(Delta_T_rep, T_max - phase_a_elapsed_sec - tail_buffer_sec)`（默认 `tail_buffer_sec=120s`）。退出后清理子进程并执行 `mcr_promote`，仅当满足棘轮序 $\succ$ 时覆盖 `submission/`。
5. **离线分叉评估与线上部署的前缀同分布等价性证明简述 `[判断]`**：
   - 设 Phase A 主会话在 `Stop` 或 `T_soft` 结束时构成自然分叉点 $t_{\mathrm{fork}}$，产出完整文件快照 $S_{\mathrm{pre}}$（写入 `pre_repair/`）。
   - **线上部署**：Phase B 从干净 `claude -p` 启动，仅依赖 $S_{\mathrm{pre}}$ 与门控函数 $G(S_{\mathrm{pre}})$，与 Phase A 的内部隐式 KV 缓存或会话内存完全独立。
   - **离线 Path 1 重放（`ssap alw-eval`）**：直接挂载同一归档快照 $S_{\mathrm{pre}}$ 至全新断网/限域容器运行候选门控 $G'$：若 $G'(S_{\mathrm{pre}})=0$，不启动 `claude -p`，收益差精确为 $\Delta Y^\star = 0$；若 $G'(S_{\mathrm{pre}})=1$，执行与线上完全同构的独立无状态 `claude -p` 补救与棘轮晋升。由于 $t_{\mathrm{fork}}$ 前缀分布及 $t_{\mathrm{fork}}$ 处的状态输入分布完全一致，满足 `mcr-formal.tex` 不变量 3（无偏且零未触发重跑方差）。

### 3.5 基础设施故障（`infra_error`）分账与 Item 21 产物摘要规范 `[事实 + 判断]`

1. **`infra_error` 与任务失败的严格切分**：
   - **基础设施故障（记为 `infra_error`，不计 0 分；对照两臂按同一事前规则配对重跑，残余未解决者在终局报告并列 `delta_worst` 敏感性下界）**：
     - 容器或宿主机 OOM（`exit_code == 137` 或 cgroup OOM 事件）；
     - Docker daemon / Harbor 调度异常（无 `trial` 目录、`harbor` 进程崩溃、镜像拉取/挂载失败）；
     - 验证器自身故障：`metrics["evaluation_error"] == true` 或（非 `missing_submission`/`invalid_submission` 引起的）`status in {"tool_error", "interrupted"}`；
     - 代理/网关异常：未恢复的网关 `5xx / 429` 中断、`proxy_requests == 0` 旁路、或单次运行中模型网关等待时长占比超过事前写死的硬阈值 `gateway_wait_seconds / wall_seconds > theta_gw_max`（**暂定事前锁定值 `theta_gw_max = 0.35`**）。
   - **任务自身失败（严格计原生分，绝不静默剔除）**：
     - 正常结束或耗尽 `T_soft/T_max` 未交出 `submission/layout.gds`（`metrics["status"] == "missing_submission"` $\implies Y^\star = 0.0, C^\star = 0$）；
     - 交出损坏/空/错 top GDS（`invalid_submission` $\implies 0.0$ 或 `geometry_valid=false` $\implies 0.10$）；
     - LVS/DRC/资格门/后仿未过（$Y^\star \in [0, 1), C^\star = 0$）。
2. **落实 `review-2026-11.md` 第 21 条（产物摘要审计）**：针对 ALW "模型请求全命中但后处理钩子改写了最终 GDS 导致分数改变" 的特有现象，每次执行强制输出 `artifact_summary.json`（及人类可读 `artifact_summary.md`），作为"是否受候选改动触及"的权威判据：
   - 必含字段：`task_id`, `seed`, `version_id`, `pre_repair_gds_sha256`, `post_repair_gds_sha256`, `submission_gds_sha256`, `submission_src_tree_sha256`, `draft_latest_gds_sha256`, `m_pre`, `u_pre`, `m_post`, `u_post`, `gate_triggered`, `trigger_reasons`, `repair_ran`, `ratchet_promoted`, `promote_reason`, `model_request_diverged`（相对基准流是否发生模型请求分叉）, `submission_artifact_diverged`（`submission_gds_sha256` 或 `submission_src_tree_sha256` 相对基准流是否变化）, `infra_error`, `infra_reason`, `outer_metrics` (`Y_star`, `C_star`, `d_star`, `score_is_lower_bound`, `evaluation_error`), `cost_ledger` (`input_tokens`, `output_tokens`, `cached_tokens`, `usd`, `tool_cpu_sec`, `peak_ram_mb`, `wall_sec`)。

### 3.6 已移除旧机制对照表（MCR v2 vs `method-v5` / 归档 `docs/ssap.tex`） `[事实 + 判断]`

| 维度 | 旧方案（`method-v5` / 归档 `ssap.tex` v1） | MCR v2（`docs/alw-mcr-instantiation.md` 锁定规范） | 移除理由（依据 `review-2026-11.md` 与 `mcr-formal.tex`） |
|---|---|---|---|
| **评估重放范式** | 请求级拦截重放 + 7 次二分调用定位（`method-v5` 代理重放 / `ssap.tex` 后缀唤醒） | 三条分叉点物理路径（Path 1 终局无状态补救重放；Path 2 条件钩子+前缀泄漏审计；Path 3 全局配对） | ALW 工具读写有状态磁盘、KLayout 多线程日志非确定、后处理钩子在模型调用之外改写产物（复审 #11, #21）；`T_soft` 无状态子调用保证线上线下严格同构 |
| **超时管理** | `enforce_virtual_deadline` 虚拟时间累计（`method-v5`） | 真实墙钟双水位线（`T_warn=9000s`, `T_soft=9900s` < `T_max=10800s`） | 并发工具耗时累加失真（复审 #10）且前缀工具必须重跑（`alw-plan.tex` §2.3）；真实现场预留窗口无漂移 |
| **中间状态保护** | `ssap.tex` $M_2$ 步级帕累托回滚或无保底裸跑 | 自由草稿区零强制回滚 + 第 I 类镜像认证原子写穿透棘轮 $H_{\mathrm{base}}$ | 步级强回滚掐死跨临时不合法态的正确重构路径；原子棘轮实现不变量 1 写穿透保底 |
| **检查器权限** | 通用合法性谓词 $V(x,w)$ 与四元算子耦合（`M_1..M_4`） | 严格四分类：**仅第 I 类镜像检查器有覆写权**；第 II 类启发探针零覆写权；第 III 类观测映射 | 彻底隔离经验/幻觉探针污染正确解（不变量 2）；不强绑定固定 4 槽避免跨槽接口死锁 |
| **搜索筛选与指标** | `ssap.tex` 跨槽 P-SHA + `0.49/|B|` 复合分 $\mathcal{J}_B$ + 单模块嫁接 | 单点原子变异 + 分叉对齐净增晋级（`Path 1` 事件账本 / `Path 2,3` 原生 $Y^\star$ 全集均值）+ 零救回清理（$K_{\mathrm{idle}}=2$） | 消除人为代理标量化与多步短视截断偏差；重型小样本直接按原生指标与事件账本归因 |
| **分项状态模型** | `ssap.tex` 二值分项 $\mathbf{c}^\star\in\{0,1\}^K$ | 三值短路分项 $\mathbf{d}^\star\in\{0,1,\bot\}^8$，严禁 $\bot$ 污染 $\mathcal{S}^+_k/\mathcal{S}^-_k$ | ALW 中 LVS 失败致 `fill/morph/pex/postsim` 全短路跳过（$\bot$），不可误标为实测失败（`0`） |
| **防过拟合与样本库** | 被动收集历史 `S_clean/S_silent`；无盲簇防特设规则机制 | 主动裁判库 $\mathcal{B}_{\mathrm{dev}}$（含合成负例/正例）+ $G_{\mathrm{dev}}=10\ge 8$ 冻结 **3 个内部盲簇** | 防止小样本下提议 Agent 针对已知错题拼凑特设规则；按独立簇数计数防重跑膨胀 |
| **验证与测试统计门** | 门控分层净胜分 $W_i$ + 簇置换 $p\le 0.05$ + Holm + $n_{10}\le r_{\max}$；`method-v5` $c=1.64\cdot\text{SE}$ + 符号检验 $p\le 0.10$ + McNemar 熔断 | **纯净 `C1` 协议**：`val` 仅做一次唯一胜出者 $H_{\mathrm{inc}}$ vs $H_{\mathrm{base}}$ 配对（$\hat{\Delta}_{\mathrm{val}}(Y^\star)>10^{-6}$）；`test` 单次揭盲跑 $\ge 3$ 种子 + 三段式归因 + $C^\star$ 翻转拆账 + 原始成本账本 | 复合零假设下装饰性统计检验既不能控 I 类错误又在重尾小簇下枪毙真改进（复审 #1, #4）；纯净 `C1` 以三段归因与多种子实数透明交代效应与噪声地板 |
