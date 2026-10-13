# J02 单次隔离替身生命周期

当前：J02 在以下披露的独立临时state／固定替身验证范围内闭合。fresh审的I1经局部修复及原reviewer对应复核关闭，无剩余确认Blocking／Important／Minor；root核对最终源码与证据并发布完成通知。自然PID代际复用尚未制造或排除，不能把本结论扩大为通用OS进程身份绑定或真实runner验收。受管预览仍为 B/C，本切片不更新服务或用户state，不推进J03或真实执行。

目标：在独立临时 state 中验证固定替身的单次 claim、实际启动、取消请求、OS 退出观测及 owner 重启对账；真实计划始终未派发、executable=false。不接 SN、模型、benchmark、judge 或真实 runner，不建队列／并发池／重试平台。

## 输入假设与最先证伪

D01 已核对当前 J01 闭合 v1 record／回执只允许 not_dispatched/cancelled。直接加入 running/exit 会改变旧回执契约。因此首步在 jobs 行新增 nullable probe JSON 列，保留原 record 字节与 v1 回执。

先证伪：事务 claim 后，实际固定 helper 已启动，但 started 尚未落库时终止临时 owner。新 owner 重开同库、reconcile、重复原启动请求。必须只启动一次；证据不足保持 unknown，不重派、不按 PID 杀进程、不把真实 Job 标为完成，旧 v1 回执仍可读。这个实验不成立就调整设计，暂不扩散实现。

源码 archive 未绑定实际加载已由 J01 的实际临时样例确认；J02 不消费真实源码，不放宽 dirty gate。将来真实 executable prepare 另行验证输入字节、实际 SN/evaluator 加载、依赖、model/runtime 与 scorer。

## 当前权威与表示

- jobs 是唯一当前权威：record.state 表示真实计划意图；probe.phase 只表示隔离替身观测，不是计划运行状态。get Job／旧回执仍返回原 v1 record；新的内部 probe get 显式返回观测。
- history 只审计，requests 只保存原命令回执；不新增 attempt 表或平行当前状态账本。替身启动标记不能证明 OS 退出或退出码。
- 每 Job 最多一次 probe。新的 key 不能产生第二次替身；同 key 重放只返回原命令事实。多 attempt／重试的表示留给真实需求，不把本局部策略写成永久产品限制。
- 闭合 probe 包含 format、probe_id、version、mode、helper_hash、phase、cancel_requested、owner_id、process_identity、exit_observation。最终字段／约束在实现前与 worker 定稿并记录，不能猜测未知 schema。
- phase 最小集合 claimed/running/exited/start_failed/unknown；启动授权在事务中消费，先持久记录不确定性再做外部 I/O，重复 start 不再次派发。exit 0 仅证明替身 OS 退出，不产生 execution_complete/run/result。

## 根边界与失败语义

- root additive schema／nullable 列迁移、J01 同连接当前 Job 校验接缝、取消接缝、必要共享回归与文档；workers 只新增 lifecycle/adapter/tests owned 文件。
- 内部 claim(task_id, job_id, expected_job_version, *, actor, request_key, mode)，start_claimed(task_id, job_id, *, adapter)，request_cancel(task_id, job_id, expected_probe_version, *, actor, request_key)，reconcile(task_id, job_id, *, adapter)，get(task_id, job_id)。不接 HTTP/CLI/UI，不开放 execution capability。
- claim 校验未取消的准确 Job、版本及无既有 probe；Job cancel 与 claim／启动授权在同库串行化。Job 首次取消转换同事务持久记录 probe 的取消请求；原回执重放不重复触发，Job cancelled 不表示 helper 已停止。
- 外部启动／等待／进程控制不持 SQLite 写锁。claim 或启动授权先赢时仍可能发生取消与启动竞态，启动者必须处理已保存取消意图；不能承诺任何时间的取消都绝无 helper 启动。
- 固定 adapter 只用有限预设模式和私有临时目录；不接受任意 shell/argv/源路径/model。helper 有有限存活上限，不把期限到达当退出证据。
- 原 owner 的私有控制管道才允许发送取消意图；固定helper收到后仅向自身发送SIGTERM，ignore_cancel仍可忽略并受TTL约束。PID、存在检查或检查后 kill(pid) 都不授权owner终止进程。控制／OS状态丢失不发取消、不重启，非终态 reconcile 保持 unknown；已落库退出观测重开仍保留。
- 首步不从 helper 自写标记恢复退出，也不引入第二套可疑“完成”账本。只接受实际 OS wait 的观测；wait 后落库前 owner 崩溃可以保持 unknown。这是本 probe 的明确恢复预算，不是未来真实 runner 的永久限制。
- 退出事实与取消意图分开：晚取消不改写已确认退出码，取消请求不证明退出由取消造成。观测绑定 probe/owner/helper/process identity，旧 owner 或不匹配观测不能覆盖已确认终态。
- 仅显式领域错误返回 typed 拒绝；程序／SQL／commit 异常保留原对象与回滚事实，不广泛转换 ValueError/FileNotFoundError，不把失败诊断称未提交。

## 可检查步骤

- [x] D01 只读收窄表示、权威与首要崩溃反例，完成通知保留。
- [x] J01 原 reviewer 对应复核闭合，保留阶段完成通知后再启动 J02 实现。通知 `/tmp/j01-final-completion-notification.json`，完整记录见[J01闭合](2026-10-12-sn-workbench-job-registration.md)。
- [x] root 先红验证 additive schema、旧 record／回执不变及同连接当前 Job 接缝；给 worker 准确 shared 契约。5failed→共享／既有Job定向231passed、exit0；证据 `/tmp/j02-root-seams-red.txt`、`/tmp/j02-root-seams-green.txt`、`/tmp/j02-root-seams-command.json`。
- [x] Codex 6.1 high 先跑实际 owner 崩溃证伪，随后实现单次 lifecycle 与固定子进程 adapter；只改新 owned 模块／测试。作者冻结回归391passed／1warning、exit0；证据 `/tmp/j02-lifecycle-_yimdpn8/`，不替代独立审。
- [x] root 取消接缝及必要共享集成：旧 J01 不带 probe 行为不变、取消仍不变更计划、实际 probe 取消意图与 Job 同事务、内部错误回滚。实际 HTTP／OS wait 集成15passed／1warning、exit0；完整定向回归随后单独记录。
- [x] fresh Codex 6.1 high 独立审具体hash与反例；原I1修复后对应复核闭合，不重开A/B/C。
- [x] root核对当前hash、具体结果、剩余未知与完成通知；不把领域／替身验收称预览已部署或真实执行已可用。

必要反例：同 key／不同 key双 claim；派发前取消；启动失败与启动不确定分开；非零退出／忽略取消／取消和退出竞争；claim-start、start-started、wait-exit落库三个崩溃窗口；PID复用或假身份不发信号；损坏/伪造helper标记不能报成功；重复reconcile、迟到旧owner观测；重开后原回执及终态不变；实际进程启动与exit0不制造评测完成或成绩。

## 已提供的共享接缝与 owned

repository 接受 schema0–4，迁移到4；jobs追加nullable probe TEXT，现有v1 record／history／requests不改写。EvaluationJobStore.get_in_transaction(conn, task_id, job_id) 使用传入conn校验当前v1意图，不开第二连接；get委托它。J01允许schema3/4，未知版本仍明确拒绝。root四文件manifest `/tmp/j02-root-seams-manifest.json`；J01最终manifest保留为上一阶段版本，不再冒称当前全部源码相同。

worker仅新增 `src/rag_eval/workbench/job_lifecycle_probe.py`、`src/rag_eval/workbench/probe_subprocess.py`、`tests/test_workbench_job_lifecycle_probe.py`、`tests/test_workbench_probe_subprocess.py`。Job v1 record／旧回执不得由worker更改；root随后接入其纯事务函数 request_probe_cancel_in_transaction(repository, conn, task_id, job_id)，仅在Job首次取消且probe非NULL时调用。此函数只更新同jobs行的probe／审计，不做进程I/O、不重开conn；失败时整个Job取消事务回滚，原回执重放不调用。

root 已按该签名提供取消调用接缝：首次Job状态转换后的同事务、仅probe非NULL时调用；原prepare/cancel回执及当前版本重复取消不触发。root用替身纯事务hook注入验证调用条件、未提交状态可见和全部回滚，4failed→共享接缝及Job HTTP/CLI 51passed／1warning、exit0；证据 `/tmp/j02-root-cancel-hook-red.txt`、`/tmp/j02-root-cancel-hook-green.txt`。这些hook测试不替代worker实际probe模块的集成／进程证据；完整生命周期及fresh审仍未通过。root当前四文件manifest `/tmp/j02-root-shared-current-manifest.json`，初始seam manifest保留，worker不得把这次root并行修改归给其owned。

替身adapter由内部可信owner对象注入，有限模式 normal/nonzero/wait_cancel/ignore_cancel；helper有固定有限存活上限，不读外部源码／config，不继承应用模型环境，不接受用户argv或shell。每个probe原owner持有私有稳定进程句柄；新owner无句柄的非终态对账unknown，不从helper标记恢复OS退出。接口最终类名及准确闭合字段由worker先通知root确认，scope约束不变。

## owned 交付与 root 实际集成

作者完成通知 `/tmp/j02-lifecycle-_yimdpn8/completion-notification.json` 保留准确接口、闭合schema及三个真实owner崩溃窗口。启动授权未提交、helper已握手但started未提交、OS退出已观测但exit未提交，重开后都不从未提交事实推断成功；失去owner句柄保持unknown，不重派。crash-evidence中的事件内容是屏障处尝试写入的内容，不能当作已提交状态；永久测试重开数据库核对持久事实。

root 新增实际HTTP取消与实际固定helper OS wait集成：派发前取消不会启动helper；正常替身退出不改变真实Job的not_dispatched/executable=false或原prepare回执；晚取消保留退出观测；新owner保留已提交终态。注入实际probe取消audit的ValueError/FileNotFoundError/SQLite异常，默认TestClient保留原异常对象，wire固定安全500，jobs/history/requests全回滚。没有开放probe派发HTTP/CLI/UI。

首轮新增集成4failed／11passed，是root测试装配错误（固定plan路由写错、静态_event误加self），日志 `/tmp/j02-root-actual-http-first.txt` 保留，不计为产品行为红例。仅修测试后15passed／1warning、exit0：`/tmp/j02-root-actual-http-green.txt`。命令／退出记录 `/tmp/j02-root-actual-http-commands.json`；当前八文件hash `/tmp/j02-root-actual-http-review-manifest.json`。初始manifest继续保留，只共享集成测试hash变化，四owned未变。

root 最终六测试文件定向回归实际396passed／1warning、exit0、92.19秒；日志 `/tmp/j02-root-final-regression.txt`，准确命令及退出记录见上述commands文件。作者391项为新增root实际HTTP集成之前的版本，不能与396混用；两轮对应各自hash和命令。验证后8/8hash与review manifest一致。warning为已有Starlette/AnyIO弃用提示，不是实际服务或真实runner验收。

fresh reviewer `9f981f84-5fde-484b-bb8d-65b0ef812a5d` 已正式接收上述具体版本及证据，Codex6.1/high，只读审J02 owned和必要root接缝，允许独立tmp固定短helper与合成state反例。不重开A/B/C，不修改预览或用户state；等待完成通知，不poll/c催。审查结果、修复和对应复核尚未产生，不能提前写闭合。

## fresh 审查结果：I1 待修复

以上交接描述保留为当时状态。fresh完成报告 `/tmp/j02-fresh-review-kxvfn65d/REVIEW.md`：0Blocking／1Important／0Minor；其396项原定向回归通过，但新增独立反例18passed／2failed，两失败属于同一I1。probe_subprocess.observe将Popen.poll/wait的ECHILD代填0误认实际OS退出状态，lifecycle持久为exited/0；正常固定nonzero helper应为7。独立owner忽略SIGCHLD时真实status不可得；提前waitpid回收自己helper时原始status明确为7，probe仍错误记0并重开保留。不能用396项green抵消这个真实失败。

root使用原独立反例仅重跑actual_nonzero：2failed／1passed／17deselected、exit1，日志 `/tmp/j02-root-i1-red.txt`、独立state `/tmp/j02-root-i1-red-state`。本次未改项目实现或原review证据。原worker `4b867cef-2868-4410-95b3-5de14e8f2c4c` 已接局部修复，仅原owned必要OS观测边界及永久反例：只有实际取得的OS status能报告退出码；不可得保持unknown/null，不要求恢复丢失7、不改全局信号策略、不重派，不凭丢状态后的PID发信号。root共享未改变，修复具体hash与绿证据尚待交付，再交原reviewer对应复核。

其余已运行独立边界包括三个真实Connection.commit前owner退出、跨进程同key不同mode、迟到发布／取消合并、实际HTTP commit内部异常原对象透传／安全500与全回滚。current损坏时旧probe回执重放的可用性已观测，但未确认契约违例，不自动新增finding。审前后8/8manifest及21必要依赖hash不变、dirty保留；完整未测范围见报告。不推进J03、预览或真实执行。

## I1 局部修复交付，待对应复核

原worker交付 `/tmp/j02-i1-repair-rocv8byx/`，仅原owned4必要调整。adapter从自己实际取得的waitpid终态status转换并缓存退出码，不再使用Popen.poll/wait/returncode作为证据。精确ECHILD永久标记status不可得，observe返回None；其他程序错误原对象透传。取消改为原owner私有stdin管道一次固定cancel字节，helper只向自己发SIGTERM；外部reaper恰在观察与取消之间运行时管道不会指向新PID。EOF/TTL/marker不是退出证据。状态不可得后无管道控制或重派；新owner保留已记录unknown/handle_lost，不改写原因／版本。公共接口与数据库／v1回执schema不变。

helper字节随固定取消协议变化，当前helper hash为 `a6b1cb3344cf24f125e9f560e4f8e36d054eedf9692f5cb379ab46b14db5f5b0`。严格hash校验未放宽，旧helper hash的历史合成probe仍拒绝，不迁移／重写旧probe；无用户state或受管预览部署。worker17个共享／必要依赖未改；root核对新owned4/4及shared4/4，八文件manifest `/tmp/j02-root-i1-recheck-manifest.json`。旧manifest对应旧实现，继续保留不混用。

作者原三反例2failed→3passed；最终405passed／1warning、原review完整独立20passed／1warning，均exit0。作者中间original-green日志实际上仍2failed（unknown重开原因／版本变化），测试SQL装配错误和精确errno guard红例均保留，准确阶段见HANDOFF及commands.json，不能用文件名当成功证据。root复跑原review完整独立文件实际20passed／1warning、exit0，`/tmp/j02-root-i1-independent-green.txt`；完整必要405项回归另行记录。修复与对应复核尚未闭合；本节不证明真实宿主、跨平台或自然PID复用验收。

root完整修复版定向回归实际405passed／1warning、exit0、94.03秒，`/tmp/j02-root-i1-regression-green.txt`；两命令／退出 `/tmp/j02-root-i1-recheck-commands.json`。验证后当前八文件hash匹配。原9f reviewer已接准确hash、diff、原红例、20项独立和405项root绿证据，开始I1窄对应复核及必要新私有取消协议检查；原完成通知及失败证据不改写。暂不更新preview或开始下一切片。

## 最终对应复核与 root 收尾

原9f对应报告 `/tmp/j02-i1-corresponding-review-24zx8rvm/REVIEW.md`：I1关闭，新增确认0Blocking／0Important／0Minor。reviewer实际重跑修复版405passed／1warning、原独立文件20passed／1warning、新私有管道／OS观测独立20passed，均exit0；报告自身只覆盖对应复核，阶段收尾由root核对范围后发布。确认默认实际7、status不可得unknown/null及重开不改原因／版本、取消负SIGTERM、ignore_cancel／EOF有限TTL、真实EPIPE后sticky不再reap／写／信号、伪造身份／无handle不碰管道、程序异常原对象、实际status缓存不受Popen假returncode影响、旧helperhash拒绝不改写。原失败与中间装配错误继续保留。

root未重复405项已通过检查；当前8/8源码与reviewed修复manifest再次实算匹配，原21文件及dirty未变的审查记录已读，准确命令退出与三轮日志尾部核对。最终源码manifest `/tmp/j02-root-final-source-manifest.json`，完成通知 `/tmp/j02-final-completion-notification.json`。单次启动／请求回执／取消事务、真实替身启动／退出观测、三个owner崩溃窗口的保守对账在该本地范围成立；真实Job始终not_dispatched/cancelled和executable=false，没有run/result/评测完成。

**必须保留的OS身份局限：** 当前waitpid使用原私有Popen的数字PID。原child未回收时PID不会同时属于另一个活进程；外部reap后通常ECHILD已验证保持unknown。但是外部reap后该PID复用为同owner另一个child、在adapter先见到ECHILD之前取得其status的条件，本轮没有自然制造或排除；数字相等不是独立代际证据。reviewer用两个真实不同PID固定helper、注入返回PID映射，只诊断到代码缺额外代际防护；这不是自然复用实证，不自动列新增finding，也不能声称已排除。私有管道取消不会向替代PID发信号，与OS退出status归属是不同结论。

因此本切片只验收独立临时state的固定替身验证，不将adapter推广为真实受管执行器。进入可能有外部reaper／PID代际变化的真实runner之前，还需建立并验证退出观测的代际归属，不能用当前green代替；具体措施留给实际运行约束，不把单次probe、恢复预算或平台选择固化永久产品限制。其他未验证部分包括真实服务重启、跨平台、长期争用／残留预算、真实输入/source加载/dependency/model/scorer绑定。重开SQLite与测试owner崩溃不是实际受管服务重启；SN归档仍不绑定mutable加载，dirty gate保留。用户state、preview、SN、模型、benchmark/judge、真实runner、生产、提交/推送均未触碰；不推进J03。
