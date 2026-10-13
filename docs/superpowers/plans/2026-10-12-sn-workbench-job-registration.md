# 固定计划到独立 Job 首切片

当前：J01 在登记／回访／派发前取消范围已闭合，0 Blocking／0 Important／0 Minor；不是执行生命周期验收。A/B/C 保持闭合，受管预览仍为已验收 B/C 版本。用户已授权小步开发、fresh 独立审；不重复审批，不提交或清理 dirty。

目标：从明确 plan_id + revision + expected_content_hash 登记独立 Job；原子保存固定计划快照、Job、事件和精确请求回执。该记录是执行意图，尚未派发，不等于可执行输入已准备好。

## 输入假设与首要反例

Codex 6.1 high D01 静态确认 runner 一次消费 partition × mode，而 draft 可能有多分区；不在本切片展开运行。实际 SN 历史候选仍存在且 dirty，不据此选定未来运行源，不改其源码。

源码归档未绑定实际加载：snapshot_sources 保存 evaluator 与 SN archive；configure_environment 仍从传入 project/backend 导入，CLI 仍从自身 checkout 加载 evaluator。独立临时样例 `/tmp/d01-root-source-loading-probe.py` 实际调用 configure_environment(product_track=False)，同一 run 存在不同内容 archive，但新子进程加载 mutable project；exit 0，证据 `/tmp/d01-root-source-loading-probe.txt`。样例没有导入真实 SN、读取模型配置或派发 Job。保留现有 dirty gate；不把 archive、HEAD、opaque source_revision、配置 hash 当实际加载或模型身份。

未来 executable prepare 要另行绑定实际消费输入/index/capsule、完整资料与选题、SN/evaluator 加载字节及依赖、model/runtime 和 scorer。未跟踪遮蔽、symlink、捕获后变化与 sys.modules 缓存须验证。本切片全部标为 not_captured，而非推断配置不存在。

## J01 边界与所有权

- root：repository additive jobs 表与 schema 版本3、固定 revision 同连接读取接缝、共享测试、窄 API/CLI、文档。暂不 App/UI/build/预览更新。
- 本步消费已经存在的 Notebook draft 适配器，不给通用 Task 增加 benchmark/suite 必填字段；无评测计划的 Task 仍可正常协作。将来其他计划适配器在其固定内容校验边界扩展，不把本步五套输入规则固化成通用Job永久限制。
- Codex 6.1 high worker：仅新增 `src/rag_eval/workbench/evaluation_jobs.py` 与 `tests/test_workbench_evaluation_jobs.py`；困难实现与 fresh 独立审。
- jobs 表是当前 Job 唯一权威；history 审计；requests 保存原命令不可变回执；计划 history 保存不可变计划修订。状态不从历史回执推导。
- jobs 表：id TEXT PRIMARY KEY、task_id TEXT NOT NULL REFERENCES tasks(id)、workspace_id TEXT NOT NULL REFERENCES workspaces(id)、record TEXT NOT NULL。JSON record 闭合校验；当前状态只有 not_dispatched/cancelled，executable=false、execution_spec=null，不含 process/run/result 成功事实。
- PlanDraftStore.get_fixed_in_transaction(conn, task_id, plan_id, revision) 在调用方事务内校验并读取准确旧修订；validate_plan_snapshot(value, *, task_id, plan_id=None) 复用既有 draft 校验。不得复制 schema 或打开第二连接选择 head。
- EvaluationJobStore(repository)：prepare(task_id, plan_id, revision, expected_content_hash, *, actor, request_key)、get(task_id, job_id)、list(task_id)、receipt(task_id, *, actor, request_key)、cancel(task_id, job_id, expected_state_version, *, actor, request_key)。get/list Task 共享；回执按 actor 隔离。
- prepare/cancel 独立 request scopes；保留原 key/原始命令拼写，prior-first；同 key异内容 Conflict，不同 key明确创建独立 Job。同 key重放不读取源、不复活 cancelled。receipt增加operation='prepare'参数；HTTP回执必须显式提供prepare/cancel，返回operation/request_key/record封闭信封，避免两类回执混淆。
- 固定快照必须匹配任务/workspace与 content_hash，不随 plan head变化。允许 canonical已删除后登记历史意图，所有执行绑定仍未捕获。当前Job更新、审计、回执同事务，无外部 I/O。取消 state_version CAS；重复已取消的当前版本返回当前状态与新回执，不增加状态事件；过期版本冲突。

## 可检查步骤

- [x] root 先验证同连接旧修订与 additive schema 反例，再提供接缝。3项红（缺接缝/schema2）→3项绿＋原计划53项，合计56 passed；证据 `/tmp/j01-shared-seams-red.txt`、`/tmp/j01-shared-seams-green.txt`。
- [x] worker 先红后绿：旧revision/hash、源删除、回执不复活、跨scope、同key并发、CAS、事务故障、返回对象不变、未知schema与状态、真实数据库重开。worker133项；其报告保留94红与4项后补回执红、两项测试前提修正事实，交接 `/tmp/j01-job-registration-diu65z8u/HANDOFF.md`。
- [x] fresh Codex 6.1 high 窄领域及必要接缝审；修复后对应复核，不重开 A/B/C 委员会。
- [x] root API/CLI只收固定身份与key，server注入actor，禁止argv/cwd/源路径/状态，能力execution=false；真实TestClient/CLI独立state验证。再次窄shared独立审。
- [x] 记录实际结果与剩余未知，完成通知。预览保持现版；没有新UI则不为本领域/API切片重建或更新服务。

## 紧邻 J02（尚未实施）

J01审后可安排隔离替身生命周期：事务 claim后才启动固定临时 helper；不接受任意shell/argv、不接真实runner。process-start与exit0不代表评测完成。取消请求/已停止分开，核验attempt/process身份才终止；三个崩溃窗口证据不足时unknown，不自动再派发。J01闭合不宣称dispatch/exit/reconcile闭环；J02再给明确owned与fresh独立审，不提前建完整调度平台。

## 共享接入契约（待验证）

`POST /api/tasks/{task_id}/jobs` 只收 plan_id、revision、expected_content_hash、request_key；actor由server注入。`GET .../jobs` 列当前Job；`GET .../jobs/{job_id}`读当前状态。`POST .../{job_id}/cancel` 只收 expected_state_version、request_key。`GET .../jobs/receipt?operation=prepare|cancel&request_key=...` 返回原命令回执，不是当前状态。写回执和读取回执都为 operation/request_key/record 信封；missing回执record=null不证明未提交。

CLI不要求源或模型配置：`job-prepare --task T --plan P --revision R --content-hash H --actor A --request-key K`；`job-cancel --task T --job J --state-version V --actor A --request-key K`；`job-list --task T`；`job-show --task T --job J`；`job-receipt --task T --actor A --operation prepare|cancel --request-key K`。均通过既有 `--state` 显式选择独立库。命令拒绝输出固定JSON诊断与原key；程序/setup异常透传。没有任意argv/cwd、模型配置、用户提供状态或派发入口。

root薄接入最初10项HTTP/CLI反例全部红，证据 `/tmp/j01-http-cli-red.txt`。后补真实类型、跨Task与actor回执隔离、程序异常透传检查；领域落地后的实际绿记录见下文。

## 当前验证与审查

root首轮实际定向检查 `/tmp/j01-root-targeted-final.txt`：236 passed、exit0、1项既有TestClient/AnyIO弃用warning。覆盖新领域133、共享schema3/同连接接缝、HTTP/真实CLI，以及必要既有计划与API回归；未运行A整阶段、服务、模型或原用户state。首轮9份源码/测试hash `/tmp/j01-root-reviewed-manifest.json`，worker两owned匹配交付；本清单是修复前历史版本。

fresh Codex6.1 high reviewer73f4ed18报告 `/tmp/j01-fresh-review-_e5ph1l3/REVIEW.md`：0 Blocking／2 Important／1 Minor。其局部240passed不替代独立反例：58passed／11failed（含1项不列finding的损坏history观测）。I1：HTTP/CLI用通用ValueError/FileNotFoundError把程序异常当业务拒绝；I2：Job GET经全局handler回显程序异常路径；M1：Job argparse禁用参数值回显。root复现4项事件异常红 `/tmp/j01-root-review-reproduce.txt`，永久边界反例12failed／2passed `/tmp/j01-root-error-boundary-red.txt`，保留失败事实。

修复边界：新JobRejected只表示明确Job领域失败；root共享UnknownIdentity/PlanSnapshotRejected/FixedPlanRejected只在缺行、闭合数据与明确固定revision输入位置产生。Job HTTP/CLI仅捕获明确业务类型及Conflict，写事件/回执/commit等真实ValueError/FileNotFoundError/SQL错误不包装成业务拒绝。Job路径剩余通用ValueError/FileNotFoundError原对象透传，真实HTTP收到安全500、不回显文本、不带业务拒绝code；TestClient默认raiseTrue保留原异常，raiseFalse检验wire响应。I2原先默认raiseTrue期望响应的临时测试将按这个准确契约对应核验，原红脚本不改写。

Job子解析器仅在Job语法失败输出固定JSON、原key，不回显任意argv/path；其他既有命令策略不改。5种禁用参数root独立CLI烟测已绿，未构造Store。损坏较新history阻止同plan旧固定revision读取的观测明确保留：闭合schema边界拒绝损坏历史，不通过猜测绕过；不是把Job重绑当前head。

准确Job record格式 `sn-workbench-evaluation-job-v1`：job_id/task_id/workspace_id/plan_id/plan_revision/plan_content_hash/完整plan_snapshot、bindings(input/source/model_runtime/scorer=not_captured)、state/state_version、executable=false/execution_spec=null、prepared_by/at、cancelled_by/at。版本1登记，版本2取消；当前版本重复取消只新增回执。事件job.prepared/job.cancelled，取消不写回draft。JSON tuple编码task/actor scopes，避免冒号拼接歧义；prepare与cancel operation隔离。没有run、PID、完成或结果字段。

领域 typed 修复交付 `/tmp/j01-typed-failure-kjj6q3lp/HANDOFF.md`：仅原两个 owned 文件，新增明确 JobRejected；已知 validator/JSON/缺行拒绝仅在相应读取边界转换，真实事件、回执、SQL及commit内部异常原对象透传，回滚零残留。补充损坏 JSON null 回执检测，不把已存在的坏回执误作缺行。worker定向342passed／1warning、exit0；25failed→70passed及2failed→20passed保留，仍不是独立验收结论。

root对应原 reviewer脚本 `/tmp/j01-root-typed-independent-corresponding.py`：仅在副本中将I2改为双方已约定的默认客户端原对象透传／wire安全500，原红脚本保留。实际68passed／1deselected／1warning、exit0，证据 `/tmp/j01-root-typed-independent-green.txt`；唯一排除为既定损坏新history观测。外部脚本首次缺PYTHONPATH收集失败记录另存 `/tmp/j01-root-typed-independent-collection-red.txt`。扩大既有CLI回归首轮9failed／363passed是同样子进程导入路径缺失，记录 `/tmp/j01-root-typed-final-import-red.txt`；随后basetemp参数装配错误exit4另存 `/tmp/j01-root-typed-final-command-red.txt`，纠正后重跑，不抹去失败。

纠正验证命令后root完整必要回归372passed／1warning、exit0，包含Job领域、同连接／schema接缝、真实HTTP/CLI与既有plan/API/comparison入口。实际日志 `/tmp/j01-root-typed-final.txt`，准确命令及exit `/tmp/j01-root-typed-final-command.json`。刷新9文件manifest `/tmp/j01-root-typed-reviewed-manifest.json`，复验前后9/9一致，worker两owned与其typed修复交付一致。正式对应复核交接 `/tmp/j01-root-typed-recheck-handoff.json`；仍等待原73f复核，不提前称J01闭合。

原73f对应复核 `/tmp/j01-fresh-typed-recheck-pk80xn8e/RECHECK.md`：0Blocking／0Important／1Minor；独立372passed、原反例68passed／1deselected，新增42passed／2failed。I1/I2闭合，CLI原路径回显闭合，残余Minor为正常支持的缩写／重复key在解析失败诊断中返回null／较早key。

root永久真实CLI反例先4failed／2passed，证据 `/tmp/j01-parser-effective-key-red.txt`。正常完成解析时直接取namespace.request_key；子解析器提前失败则复用该argparse实例的选项／值识别，保留最后有效key，不另建缩写或负号语法。首修误取Python3.13选项tuple字段导致提前失败的等号用例1failed／95passed，保留 `/tmp/j01-parser-effective-key-first-fix-red.txt`；纠正后96passed。再用领域允许的负数样式字符串key `-1` 证伪2failed／10passed，保留 `/tmp/j01-parser-negative-key-red.txt`，值识别交回argparse自身，不以starts-with-minus推断缺值。旧版372项是CLI本增量前证据，不冒充最后版本完整回归。

最终parser增量必要检查102passed／1warning、exit0：Job API/CLI、既有plan CLI及原73f额外独立边界脚本，准确日志／命令 `/tmp/j01-parser-final-green.txt`、`/tmp/j01-parser-final-command.json`。9文件刷新manifest `/tmp/j01-parser-final-manifest.json`，只cli.py与其root测试变更，其他7份hash与对应复核一致。正式M1对应交接 `/tmp/j01-parser-final-handoff.json`，等待原reviewer窄复核。

## 最终闭合

原73f最终M1窄复核 `/tmp/j01-fresh-m1-final-_akllj2u/RECHECK.md`：0Blocking／0Important／0Minor，实际46passed／63deselected／1warning、exit0，新增独立混合缩写／重复／等号及合法负数key，拒绝后rows不变，9/9hash前后一致；I1/I2沿用原typed对应复核，不虚称再次整审。root实读证据并复算当前9/9匹配，阶段完成通知 `/tmp/j01-final-completion-notification.json`。

交付为仅开发源码的独立Job登记领域及薄HTTP/CLI；没有App/UI、build、live迁移／部署。Job仍not_dispatched/cancelled、executable=false、四类not_captured；源删除后可登记历史意图不代表执行材料可用。argparse提前诊断复用当前标准库私有选项匹配，核验限本机Python3.13，未宣称跨版本穷尽。capture≠load与损坏较新history严格拒绝继续保留。替身启动／取消／OS退出／owner重启对账另按[J02计划](2026-10-12-sn-workbench-job-lifecycle-probe.md)独立实施／fresh审，不据J01宣称执行闭环。
