# 服务器共享存储与结果回传指令

核实日期：2026-10-08。这是待服务器执行的交接模板；本机只实现和离线验证，没有服务器实验结果。真实实验仍按已有授权范围，不能从本文件推断新增模型调用授权。

当前已完成实验正在旧版导出的场景，先使用[已有实验导出升级指令](server-upgrade-running-export-prompt.md)。下方模板用于新版 campaign，不要求把现有生成实验重跑。

```text
请先阅读 AGENTS.md、CURRENT_STATE.md、RUNBOOK.md 和 docs/result-storage-and-export.md，记录实际 evaluator SHA/未提交源码、SN revision/tree、依赖和配置身份。当前只做 SN-only：QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA 的 chunk，以及 MultiHop reasoning；smoke 后 full。外部方法/BM25/reference 暂缓，专门 Agent judge 与组件补评另按既有任务安排。本次不改生产环境、不启动 timer。

同一 campaign 的共享 store 设为 $CAMPAIGN_ROOT/artifacts，run 使用 $CAMPAIGN_ROOT/runs/<job--partition--attempt> 的直接子目录。已有 frozen v3 bundle 用 prepare_notebook_benchmarks.py --install-bundle "$BUNDLE" --artifact-root "$ARTIFACT_ROOT" 安装一次，不改原 bundle；新 prepare 可同次传 --artifact-root。保存受信任 installer 的 Shared bundle 和 Shared partition index 输出，按 benchmark/ALCE task 把这两个 ID 写入冻结 campaign 配置。ARTIFACT_INDEX_ID 从该冻结配置取得，不在每次运行重新读取可变 store pointer。所有 shared run CLI/API 要同时提供 artifact root 与预期 index ID，入口拒绝 pointer index 不符，包括 bundle/index 同时被替换后的自洽集合。变更 pin 先审查来源/协议并新建 campaign，不静默跟随 pointer。

先跑离线回归和安装/身份检查，再执行已有授权范围的 smoke。每次 run_notebook_benchmarks.py 明确 --bundle、--artifact-root "$ARTIFACT_ROOT"、--artifact-index-id "$ARTIFACT_INDEX_ID"、--partition-id、--request-revision notebook-request-v3、mode、模型配置和新 run-dir；smoke 仅用明确 case-id-file 选题，资料仍完整。Agent/现有 QMSum baseline shared CLI/API 同样要传 index pin，这不授权运行当前暂缓任务。失败重试另建 attempt，不按低分重问。

安装阶段仍完整 load_bundle 哈希并重新适配一次，不是重新 prepare；index 为每个 partition 保存 v1/v2/v3 capsule，公开资料／请求在版本间重复，bundle object identity 绑定每个 capsule 的文件哈希。消费时拒绝未获该 bundle reference 绑定的替换 index，并核源码对象与 run code identity 精确一致。每个 run 仍写完整 product-bundle.json，runtime 不缩减；inventory 单列这些实际字节。QMSum BM25 的 turns 读取仍完整哈希/解析 raw，不把当前 SN bounded 初始化解释为所有方法的启动成本。

共享 run 的输入/源码在 store 校验并复用，数据库/storage/模型配置/日志仍每次独立。自动单 run 报告是 partition-capsule 校验，其 input_validation 字段不能当作全 bundle 审计完成。正式 export-sn 显式列出实际 run 目录，不传共同父目录；同次 canonical 全验证、保留整套 scope/缺失状态与 outputs hash。smoke 显式声明 smoke scope，full 不删题、不混模式或重复 attempt。

官方成绩保存到 official-scores/<job>，submission 到 submissions/<job>，报告到 reports/ 或 comparisons/。答卷顶层仍是 benchmark-submission-v1，方法声明 scoring_projection 协议；QASPER/Hotpot 完整 response/captures 和原快照保持不变，MultiHop 保留完整真实 ranking，ALCE 保留 anchors/maps/errors，QMSum 不复制无关 context。原始 outputs 与 Agent 工件保留，不用 compact submission 代替补评输入。

每阶段先做 inventory，记录同范围 input/source/runtime/outputs/agent/官方产物的 logical 和 allocated bytes、安装/导出/打包耗时、读/验证次数、峰值内存及覆盖。先验证 results 包，再生成需要复核的 review 包：package_benchmark_results.py --campaign "$CAMPAIGN_ROOT" --mode results|review --output <campaign外的新目录或.tar.gz>，两模式分开实际调用。回传 archive、receipt；解包后 validate_package 校验 hash/共享依赖。review 只附一次共享对象并保留完整 outputs/必要轨迹，不夹带 runtime、模型配置或原始服务日志。已有嵌套目录先在新 staging 按角色组织；不得默认把整个 campaign tar 或静默遗漏 official scores。

所有原 bundle、旧 run、runtime、原始日志继续保留，无自动删除/TTL/GC。旧 QASPER 的显式 --qasper-evidence 恢复可能读 runtime 数据库，不能因已有 review 包就删库。结构化凭据检查不是自由文本脱敏；传输权限与资料审核沿用项目现有私有边界。

交付实际覆盖、状态、缺分/证据映射原因、官方指标实际分母、源码/配置身份、阶段耗时/字节/RSS 和 package hash。报告“代码离线通过”与“服务器实际验收”分别列证据；不承诺固定秒数或把存储差异解释为算法效果。若快照回放或同题 prepared 输入发生差异，停止扩 full，保留反例与原工件，查投影/引用契约，不裁剪输入或补零通过。
```
