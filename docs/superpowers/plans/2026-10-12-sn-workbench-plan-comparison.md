# B／C 首切片：持久计划与只读结果比较

2026-10-12。用户与父会话已认可两份只读 scope brief，授权小步实现、独立评审及预览恢复，无须重复批准。统筹唯一维护此记录与共享入口；A 已最终委员会闭合，不重开。

目标：B 保存 Task 内完整评测计划草稿、追加修订并回访固定版本；C 对 Task 已关联两份不可变结果复用既有比较核心，返回准确报告或不可配对原因。二者都不执行 Job／模型／benchmark／judge，不改 SN／Paseo，不新增通用队列平台。没有评测输入的 Task 仍可正常协作。

**最终状态：B／C 首可用切片已闭合并更新受管预览。** Astra／Opus最终0Blocking／0Important，保留已披露的M1／M2／M5三项Minor；部署只读验收通过，原三任务及状态内容不变。具体证据和回退在末尾，推进时记录不代表当前等待状态。

## 归属与依赖

| 切片 | 唯一 owned | 生产者与验收 | 当前状态 |
| --- | --- | --- | --- |
| B01 scope | Paseo 1ef13c46-e5ee-41eb-b012-a80b66f49efe，只读 | 实际事务／canonical／runner约束 brief | 已完成，不重复派工 |
| C01 scope | Paseo 4987c69e-ce48-4c84-900a-a54175f7338f，只读 | frozen snapshot＋完整canonical复用比较器 brief | 已完成，不重复派工 |
| B02 领域 | 新 evaluation_plans.py、plan_inputs.py 及各自测试 | history/requests 单事务；全局plan CAS；完整不可变revision与receipt；受控v3 canonical选题无gold；源删除后旧记录可读 | fresh4e对应最终0/0/0，87原检查＋41独立反例通过 |
| B03 前端状态/编辑 | 新 plans/usePlanDrafts.ts、PlanDraftEditor.tsx＋React定向harness | 原命令/回执恢复、显式选题、revision/CAS与scope隔离 | c5e M4对应最终0/0/0；255harness＋197独立检查通过；Python空白/码点规则一致 |
| B04 受控历史 | 新 plans/PlanDraftHistory.tsx | 精确revision选择、只读固定详情、source/model/scorer未捕获 | 042对应最终0/0/0；原反例与500字符标题390明暗8组通过 |
| C02 领域 | 新 result_comparison.py 及测试 | task关联双选；read_snapshot；可信完整canonical；既有核心准入；独立fixture成功配对及不可配对／不可用分工 | 570最终对应0/0/0，四项Important闭合，51适配＋77核心128通过 |
| C 核心接缝 | 统筹 benchmark_comparison.py 与必要等价性测试 | 路径入口与frozen对象入口共用同一验证／比较核心，不写临时答卷、不改评分 | 新core类别类型修复后141定向通过；570及Astra对应独立核验通过 |
| C03 前端状态 | 新 comparison/useTaskComparison.ts＋定向React harness | 显式双选＋GET、runtime绑定、scope代次和迟到隔离 | 977对应最终0/0/0；enum反例5/5拒绝，22真实DTO＋3生命周期通过 |
| C04 受控显示 | 新 comparison/ComparisonPanel.tsx | 纯props，原官方汇总／附加paired分开、失败保留阅读、dark390 | Pi交付及570对应0/0/0；390明暗8组布局通过 |
| 共享接入 | 仅统筹 api/repository/CLI/contracts/App/配置/迁移/文档/预览 | 消费实际已审契约；领域先审，再窄HTTP／CLI，再受控UI；无新数据库 | CLI M3最终0/0/0；App fresh审与重构建33actualApp通过；委员会闭合、预览更新及原state只读验收通过 |

B/C新模块无共享写，可并行。B不依赖C结果，C不依赖B计划；统筹核心接缝供C消费。不得从COMMIT来源路径自动授信canonical。B输入登记不要求先接成绩；request-v3/Notebook runner仅是当前适配意图，不是通用Task固定协议。source/model/scorer未捕获状态必须可读，draft execution=false不冒充ready；未来执行需真实源码内容加载身份，取消dirty检查不能代替绑定。

## 验证与交付

- 先针对最可能推翻契约的条件验证：B完整canonical选题／分区与旧回执恢复；C完整canonical＋子集frozen结果成功配对，缺／错canonical明确失败。
- 当前内容baseline与前后hash；保存dirty/untracked，不commit/push/reset/stash/clean，不install，不写预览测试记录。
- B定向检查：重开固定revision、追加CAS、跨actor并发、同key丢响应一次提交、异内容／旧base零残留、来源变化／删除后旧回执可读、gold不进入描述、canonical顺序／完整partition保留。
- C定向检查：资格正向配对保持官方汇总与独立paired估计；错误／缺失生成拒绝；有效零分、no_answer/clarification与pending/batch-only原样；同分母异eligible IDs不回退交集；篡改／缺canonical准确失败；原工件删除不影响snapshot消费。
- 每条困难实现交Codex6.1 high，完成后fresh独立Codex6.1 high审；简单受控组件待实际契约闭合后Codex确认→Pi Flash max→Codex审。通知衔接，不轮询催促，不重复委派。
- 阶段可用且小审通过后再Astra／Opus高effort委员会；本记录不冻结临时容量、布局或全部产品DTO。

## 预览恢复

入场Paseo workbench stopped。原3任务state／配置／旧dist已一致备份到 /tmp/sn-preview-restore-20261012-nvpx3p0r，SQLite integrity ok。仅允许必要additive migration；先在副本验证旧表内容与回退，再通过受管script启动。构建不含访问码／私密素材，认证仅内存使用；预览只读检查三任务与A能力，不写任务／人评／消息。新旧bundle须关闭或刷新旧页。恢复进展与实际状态接续补充。

预览恢复完成：bridge／前端build exit0；公开Paseo status实际监听端点与官方CLI /ws规则派生服务器配置，只读listWorkspaces/releaseok且workspace匹配，没有猜凭据。副本启动additive新增3表，原6表内容hash全部相同；旧state一致备份可在停止服务后整体回退，配套旧config/dist保留。受管workbench running，port46223，terminal19370321-3721-4af2-9e74-05e9ef7a1316，无访问码入口 http://workbench--feat-benchmark-protocol-correctness--bench--16ca78a8.localhost:6767 。真实代理浏览器仅GET/内存cookie，三原任务、A read/create/send=true、execution=false、无会话禁发原因、零pageerrors/零domainwrites通过。原表hash仍相同，访问码未进入构建；CLI不再打印认证URL。未真实创建／发送、未写预览测试记录，A不重复整套验证。

最小canonical假设已验证：预览登记的完整QMSum281题/35分区、MultiHop2556题/1分区、ALCE948题/948分区均load_bundle通过，版本notebook-data-v3。只读，不运行评分/模型。它们可登记为B输入而不依赖结果附着；现有三个任务各单份且不同suite，不是C正向配对材料。C正向需独立state完整canonical＋明确scorer身份fixture，不能改历史类别或跨任务拼对。

## 推进时证据（按发生时状态记录，当前结论以表格及末尾收敛为准）

当前内容baseline：统筹核心2文件 /tmp/sn-c-core-entry-y7wifl5v；worker自行保留新模块入场副本/hash。核心 compare_frozen_submissions(bundle_directory,entries) 接精确{submission,scores,source:{snapshot_id,source_hashes}}，load_bundle后同一_compare_objects做全部准入及报告；method.files用真实snapshot元数据。路径入口不伪造或替换输入。新增等价性测试将source文件删除后比较对象与路径报告除provenance外逐字段相同，原官方batch与paired估计分工保持。

C02交付仅新result_comparison/test，证据 /tmp/c02-comparison-evidence-4emnp75d/final-manifest.json、owned.diff、red.txt、final-targeted.txt。33适配检查＋核心/工作台阅读回归组合124 passed；root独立当前核心＋适配110 passed。完整/subset合格synthetic canonical+scorer身份成功配对，原source删除后继续消费冻结体，正式批次值未替换为逐题均值；损坏/缺源保留独立侧阅读状态、准入不退成功交集、SQLite/工件不写。fresh Codex6.1 high 570e3c75-383c-4531-9757-04d8d51feca4 仅对应4文件只读审，不把作者绿当最终接线许可。

C02 fresh评审0Blocking/3Important/0Minor，暂不验收：合法batch_only_metrics:null导致summary TypeError；自洽新hash快照的manifest/cases/count未与完整canonical绑定，可返回来源不一致的comparable；损坏denominators结构KeyError丢失另一侧。read_snapshot完整性hash不等于新内容语义保证，根认可该契约缺口，交原595仅两owned局部修复、先反例后实现，再原reviewer对应复核。核心及API不抢改，不放宽既有比较准入。

修复交付 /tmp/c02-review570-fix-erlo0loz：12项新反例先红，45适配／136组合通过；root实读结构门禁、canonical阅读投影绑定及合法null规范化后，已交原570复核。尚未放行C HTTP/UI。

对应复核与root定向均122 passed，原三项闭合；570补出一次operation内canonical两次读取之间损坏的结构异常遗漏（manifest为[]／缺max_documents）。已交原作者在明确core调用边界诊断，重读损坏返回unavailable并保留侧，重读正常则透传原编程异常；不扩大捕获、不抢改核心。新Important闭合前C不接线。

统筹共享入口baseline /tmp/sn-bc-shared-entry-4jreb3kc。先完成独立受控input登记与相对配置路径解析（configuration.py，6项先红后绿），不要求关联结果、不读取不可用输入或私有凭据；CLI serve复用配置解析。HTTP／CLI消费测试先证明当前入口缺失，覆盖无结果计划、固定版本／回执／源删除、可信canonical正向比较和零写入，待领域通过再实现。B草稿测试曾存在直接rename登记canonical的危险路径，root入场核对三原来源仍存在，并明确要求改为独立fixture；任何源删除测试不得触及原输入或预览state。

C二读竞态修复 /tmp/c02-review570-race-a5_j3du8 已原570独立通过，程序异常正常透传。领域通过后root接只读GET comparison：严格query且拒浏览器路径，先查Task关联，再按显式input登记的bundle_id选择可信canonical；缺登记不读取COMMIT路径。3HTTP边界＋既有API/配置合计13 passed，typecheck exit0。配置显式登记3个完整canonical输入，与旧sources并列而非依赖结果；现服务仍原A bundle，B/C最终验收前不宣称预览交付。

B作者85通过交接 /tmp/b01-handoff.md 与 /tmp/b01-owned.sha256，fresh4e审中。作者确认危险旧测试仅在缺模块时collection失败，函数体未执行；现71新增测试全部tmpfixture，不读本机私有config，不变更原canonical。新signature仅expected_bundle_id+explicit case_ids，partition server派生，原title/key/选题顺序参与幂等，saved schema固定校验，旧revision不重读源。

B fresh4e：71原检查＋23独立反例通过，2项source.revision私有路径/含凭据URL泄漏失败，0Blocking/1Important。root认可公开投影缺口，交原作者将真实源revision转换为明确opaque内容身份（不改canonical、不靠脆弱字符串黑名单），修复descriptor/content/history/receipt全链，原4e再核。saved草稿仍未HTTP/UI发布。作者关于旧危险函数体未执行的陈述不当作独立追溯证明，已独立确认当前测试只用tmpfixture。

B source_revision修复 /tmp/b01-revision-privacy-handoff.md 已原4e独立87＋41检查通过，最终0/0/0。公开revision是sha256:canonical JSON fingerprint，非原文字；不改bundleID或源、不迁移旧原文记录。领域通过后root接save/list/get/receipt HTTP与thin CLI，配置inputs不依赖results，typedStore rollback拒绝带code/status/key，泛化错误不能等同零写。成功POST与GET receipt回显精确request_key＋record envelope，固定revision/list/CLI仍原Record，避免把其他key旧回执误当本次保存；Store不可变schema不改。当前共享HTTP/CLI/config/原API27passed，fresh2ee3独立审完成；CLI对应修复0/0/0；C04首审props tuple错配Important与subset题数Minor由Pi局部修复，原570对应核中。App/预览B/C尚未接线，未跑实际Job。

C03状态与B04历史分别交fresh Codex6.1 high只读977a5f0f-e404-4a4a-b9db-479d5c34dc9b、042da83f-5a45-4374-af55-969c7616fc34。C04对应修复最终0/0/0，独立390明暗8组实际Chromium无横向溢出；只证明组件，App仍待root接线。App当前内容baseline /tmp/sn-bc-app-entry-uv5si7tl；actualApp独立canonical/SQLite fixture，不碰原三任务，不执行模型。

C03 fresh977最终0/0/0，真实领域返回→hook22检查、3生命周期＋8破坏DTO反例通过；actualApp根12检查已走通真实HTTP正向与scorer不一致拒绝、失败侧打开原成绩、空结果Task原因、sidebar切Task及dark390。测试初稿用同页fragment跳转未触发App导航，已改真实sidebar；resize断言先于React布局，改等实际布局满足条件；compact关闭材料须点其可见close按钮，测试已修正，未据这两种测试误用改产品。B04合法长英文标题溢出已原作者确认并局部把完整标题换行/按钮短label，待042对应复核。

共享2ee评审0P0/0P1/1P2（Important）：CLI缺必填字段输出traceback暴露checkout路径。root仅入口补必填/closed字段与UTF8JSON安全诊断，9反例红后10CLI检查通过，manifest已刷新交原2ee复核。App已接C与B入口，临时actualAppB真保存/reload/GET固定revision定位到controller未暴露独立fixed读取对象（saved只代表回执）；交原e6在owned补只读selected接缝，编辑body保持唯一。此问题未闭合前不宣称B页面验收完成。

共享HTTP/CLI/config/contracts最终无剩余finding：原2ee CLI对应10项＋18类真实子进程坏输入通过，六必填字段合法保存及Store程序异常透传，8文件hash不变。B/C共享读取无新DB或运行接线；App仍待B固定选择接缝与独立最终验收。

B03最终交付 /tmp/b03-handoff.md、b03-owned-sha256.json，83 React19/RNW checks/typecheck通过；selected从已核GET/receipt的不可变记录与当前binding派生，saved仅原命令回执。root消费selected后临时actualApp完整30checks通过，零pageerror：B真实保存/reload/fixed/append/旧base拒绝/响应已提交后丢失→同keyGET回执、3history无重复；C真实成功/正式scorer不一致拒绝/两侧成绩阅读/跨Task迟到GET/空任务说明/dark390。证据 /tmp/sn-bc-app-entry-uv5si7tl/final-browser.txt、final-build.txt、final-typecheck.txt、after.json；待B03及App独立审，未更新预览。

最终小审调度：B03 c5e569d6-e1d1-4b04-8c04-43da22e59e82；App3文件 d4f8d127-f364-4a9a-b43b-8c5db5597524，均Codex6.1 high只读。root截图补出保存后heads旧缓存，actual新revision行反例先timeout；App局部callback等待在途旧列表settle后GET committed heads，不改hook/固定selection，补反例后33actualApp checks/typecheck/build通过，after.json已刷新。README明确未保存正文/未确认command不跨刷新持久化；unknown先在原页面核对，重开读服务端记录不以暂缺判断未提交。不把这一页面限制说成远端派发锁，B无执行副作用。

B03fresh c5e：原83/typecheck及真实五套领域DTO/开发StrictMode53正向通过，但suite用String强转接纳array，标题UTF16计数拒领域合法300emoji导致fixed/head失败；0Blocking/2Important。root核对Pythonlen标题契约，交原e6只owned反例修复，不改领域限制；另让原C03 reviewer在tmp核对相同String(enum)模式是否有实际违例，不以类比当finding。B03未放行，委员会未启动、预览未变。

App fresh d4f最终无新增Blocking/Important/已确认Minor：新版33共享harness＋14独立场景/typecheck通过，实际head刷新不重绑fixed与编辑，18102关闭、最终3filehash不变，报告 /tmp/sn-bc-independent-review-6rcg0uj9/REVIEW.md。评审先前两次harness超时保留证据，不据后续绿推断根因或已修复；B03两Important与C03相似模式定向核对仍各自处理，阶段未放行。

C03原977定向补审实际证实method.kind=['reference']／summary.scope=['full']被hook发布为正常data，error=null；新增1Important，修正初审无finding结论。证据 /tmp/c03-enum-boundary-review-qso0ffh9/REVIEW.md。原595仅owned hook/harness修复实际类型门禁、反例红绿，再原977对应复核；不因B类比扩大到A或共享入口。预览仍为已验收A构建。

B03对应复核 /tmp/b03-corresponding-review-uua1ikz_/review.md 原两Important闭合，119harness＋78独立真实领域/StrictMode及typecheck通过；修复仅真实suite类型与Python一致码点计数，不缩领域预算、不改controller接口。C03作者四处enum真实类型修复 /tmp/c03-enum-fix-7ka12mi6/owned.diff，原977对应核中。root最终前端临时构建 /tmp/sn-bc-final-20261012-dist 已包含两模块修复，119B＋40C边界检查、typecheck、bridge build及33actualApp checks全部通过，零pageerror；临时18101已关闭。证据 /tmp/sn-bc-final-{plan-boundaries,comparison-boundaries,typecheck,build,bridge-build,actual-app}.txt、/tmp/sn-bc-final-frontend-manifest.json。这轮实际App通过不抹掉此前未解释的reload／网络瞬态失败，预览尚未更新。

C03原977对应复核 /tmp/c03-enum-fix-review-fnno8946/REVIEW.md 最终0/0/0，原Important及同边界metrics.kind闭合；旧版五反例0/5拒绝，修复版5/5，22有效DTO＋3生命周期及typecheck通过。所有小审闭合后已启动B/C独立阶段委员会：Astra high 3d72a822-9be4-4b02-8018-13b87dab371f、Opus high c6ec532e-9b32-447a-a577-e105cf1136fe，均只读代码与已有证据。profiles为空，实际discovery确认模型/mode，notifyOnFinish衔接，不轮询。最终前端24文件manifest和构建私密配置扫描已核，A不重开，阶段结论仍等待委员会。

Astra阶段评估0Blocking/1Important/0Minor：B可在披露范围闭合；C-CATEGORY-TYPE是正常JSON数组/对象category经build/refresh/import可达核心集合membership TypeError，属于外部准入校验遗漏而非编程异常。委员仅静态推导；root随后真实独立fixture两反例均失败 /tmp/sn-bc-category-red.txt，再只在核心所属的_comparison_category添加str门禁→ValueError，不改adapter捕获。新增root owned tests/test_workbench_comparison_categories.py验证C not_comparable/comparison_rejected及两侧readable、path/frozen同样拒绝、工件/SQLite零写；核心/适配/HTTP定向141passed，1既有Starlette弃用warning。前后diff/hash与红绿 /tmp/sn-bc-category-fix-c3il7iv5；原570独立对应核中，Opus已知此次局部变化，预览未更新。


委员会对应收敛：C-CATEGORY-TYPE已原570独立23定向反例及Astra静态复核0/0/0闭合；当前core e80119a5，不沿用旧core77证据。Opus接受M1合法canonical替换诊断差异、M2按左登记造成左右失败差异、M5写锁内完整重建预算未测，具体披露已写README，均不宣称修复。M3 CLI仅Store操作捕获已知Conflict/ValueError/FileNotFoundError，固定JSON拒绝不推断未提交，真程序异常透传；正确fixture在哈希匹配旧CLI隔离副本红5failed→当前17passed。最初fixture漏workspace的4failed13passed保留为测试装配失败，不作domain修复证据；曾提前报告17已立即更正。原2ee对应审中。M4由原e6对齐Python空白字符集，原c5e对应审后须重build/actualApp；M6移除README选题/比较未接入旧说法，解释执行冻结并修正表格，阶段暂未最终通过。

M3原2ee对应复核 /tmp/sn-bc-cli-domain-review-15yfmete/review.md 最终窄范围0/0/0；17targeted，12真实拒绝、12已知异常及20程序异常透传检查，scope/key/prior receipt均核，原错误数字与失败记录保留。C-CATEGORY-TYPE已Astra对应静态最终0/0/0，stage仍待M4重构建与最终委员会核对；README含M1/M2/M5及其余披露，已消除旧范围说法。

M4原e6交付 /tmp/b03-m4-whitespace-ccv61qcx：text/id/draft共用Python空白规则，保留原值/码点预算/题单解析，真实五套DTO含BOMcase反例；原c5e对应核中。root重构建 /tmp/sn-bc-final-m4-20261012-dist、typecheck、255B边界与33actualApp checks通过，零pageerror，临时18101已关闭，私密配置扫描通过。新源码/build manifest /tmp/sn-bc-final-m4-frontend-manifest.json；先前final-dist属于M4之前历史版本，不用于预览。最终共享manifest /tmp/sn-bc-final-shared-manifest.json仅CLI/test相对上一共享已审内容变化；不把旧manifest冒充当前版本。等待M4独立复核与两委员最终收敛，预览尚为A。

M4原c5e对应最终0/0/0 /tmp/b03-whitespace-review-etmpcuym/review.md：255harness＋197独立检查/typecheck，真实BOM保存/list/fixed/回执与所有码点Python谓词一致；owned及56源文件未变。M3/M4/M6交付与新版33actualApp证据已齐，交Astra/Opus最终对应静态收敛，预览更新仍在最终门槛之后。


## 最终收敛与预览交付

Astra 3d72a822-9be4-4b02-8018-13b87dab371f、Opus c6ec532e-9b32-447a-a577-e105cf1136fe 最终静态对应认可B/C首切片在披露范围闭合：0Blocking／0Important，保留M1合法canonical替换失败分类、M2按左登记导致左右失败不对称、M5每次新修订在写锁内完整重建且预算未测。M3安全CLI／M4Python空白／M6范围文档均闭合；Opus可选措辞已改为“每次保存新修订（同key重放除外）”。不声称保留Minor已修复，不以合成正向当历史配对或模型成绩，不抹掉未解释reload／网络瞬态失败。

最终可用版本：core e80119a5、CLI 0f272c59、B hook a510f6b4；前端 /tmp/sn-bc-final-m4-20261012-dist 与 /tmp/sn-bc-final-m4-frontend-manifest.json 完全匹配。root新core定向141passed（1既有warning）、CLI17passed、B255harness、C40harness、typecheck／bridge build、新版33actualApp通过；各范围独立复核见本记录表格及对应证据。已完成必要检查，不重跑A全套或真实发送。

预览更新前通过Paseo停止workbench；一致SQLite backup与state/imports、config、paseo配置、旧dist保存于 /tmp/sn-bc-preview-backup-20261012-cs9h3mos，备份integrity与逻辑内容复核通过。旧实际dist另保留dist-original-live；B/C未新增schema，重启后10张表的schema/user_version／行hash以及全部非SQLite状态文件与before完全相同，/tmp/sn-bc-preview-backup-20261012-cs9h3mos/{before,after}.json。停止服务后可从该目录整体恢复state与配套旧config/dist，再通过受管script启动；仅旧UI回退不表示回退当前源码功能。

受管启动成功，port45549，terminal b1f1a006-80a5-4d51-9399-de63ebe6f75a，Scripts running／healthy。无访问码入口 http://workbench--feat-benchmark-protocol-correctness--bench--16ca78a8.localhost:6767 。只读代理浏览器验收 /tmp/sn-bc-preview-final-facts.json：tasks3、inputs3、agent_read/create/send和plan_drafts/result_comparison均true、execution=false、domain_writes0、page_errors0，3构建文件私密配置扫描通过。原三任务各单份结果的缺配对提示、计划未填禁保存及390／dark检查通过。访问码仅本地内存换cookie，未输出认证URL或写测试记录。首次Node APIRequestContext对该*.localhost DNS ENOTFOUND，证据 /tmp/sn-bc-preview-final-read.txt；改用浏览器标准loopback解析与同源fetch后验收通过 /tmp/sn-bc-preview-final-read-browser.txt，未据此改应用或服务配置。

长期说明已同步CURRENT_STATE／README／RUNBOOK／ARCHITECTURE_CURRENT／evaluation-status。更新后关闭或刷新所有旧工作台页面。下一执行切片仍须另行核验完整运行输入、实际加载源码内容、模型及scorer身份；本轮无Job／benchmark／judge／SN／生产／服务器实验，无commit／push／stash／reset／clean，保留原dirty/untracked。
