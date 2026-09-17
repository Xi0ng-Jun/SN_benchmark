# 官方数据预检反馈与适配修正

输入为用户提供的服务器 Agent 汇报，服务器版本 525633f；以下真实数据数量未经本机重算。本机不下载数据、不启动 SN 或模型。此修正只修改评测侧适配、证据诊断和文档。

## 服务器观察

- QASPER：416 篇论文，1059 题纳入、392 题排除、386 分区。253 题有图表证据；其余 139 题中，服务器空白归一化复查可找回 138 题，1 题是章节标题路径、非段落。
- QMSum：35 场会议、281 题。17 条空发言（11 空字符串、6 纯空白）。临时清洗替换为 `[empty]`，没有删除或重排 turn，因此位置索引未偏移，但正文已改变。
- MultiHop-RAG：2556 题、609 篇 corpus，单个完整分区。
- ALCE：ASQA 948 题已准备。QAMPARI 5 处空 alias 是 answers[j][k] 中空字符串；不是空列表。ELI5 可直接适配 1000 cases，上一份汇报误把 QAMPARI 问题归到 ELI5。
- 全部仍在 prepare 阶段，未导入、未 Ask，不能据此声称模型、容量或上下文捕获已验收。

## 修正设计与执行顺序

1. 先保存当前版本生成的构造 QASPER bundle 为兼容性 fixture，含精确匹配纳入题和空白差异排除题。
2. 编写失败回归：空白差异证据应纳入但原文原证据不变；真正缺证据、图表证据仍排除；空 turn 原样保留且索引不变；QAMPARI 空 alias 原样保留，不删除答案组。
3. 新 prepare 记录 `adaptation_revision=notebook-data-v2`；加载无此字段的旧 bundle 严格按 notebook-data-v1 重建。未知版本拒绝，原协议名称不变。新旧样本/来源身份不同，不能覆盖或混作同一配置。
4. QASPER 只合并连续空白为一个空格并去首尾空白进行段落定位；不去词内空格、不忽略标点/大小写。保留 raw、document、annotation.evidence 字节，新增证据到段落 ID 的映射和匹配方式。
5. QASPER 上下文诊断同步使用空白归一化，独立 scorer v2；只接受同论文完整段落，不把跨 chunk 拼接当已观察证据。旧 scorer v1 保留。
6. QMSum 保留空/空白字符串，不造占位文字；content 非字符串仍报错。新 specific turn 覆盖诊断从分母中排除无文本发言并记录 ID，全部为空则 N/A；旧 scorer 保留。
7. QAMPARI 保留空字符串 alias 及 gold 组数，现有评分器本来会过滤空预测，因此空 alias 不能获得命中；不需要改评分公式。空 alias 列表与非字符串仍为 schema 错误，本次不猜测未报告形状。
8. 离线回归涵盖 source→bundle→load→计划指标、旧 bundle 和旧分数协议兼容；再更新服务器迁移说明。所有修改留在开发 worktree，不自动推送。

## 服务器应如何处理现有产物

**先完成新 bundle 的核验，再进入 Ask。** 不需要重复下载，使用服务器已保存、未改动的官方文件。旧 bundle 归档保留，不修改其中 raw-data/cases/manifest，也不要直接给旧 manifest 加版本字段。

| 已有产物 | 处理 |
| --- | --- |
| QASPER 旧 bundle | 用原官方 JSON 与来源说明重新 prepare 到新目录，例如 bundles/qasper-data-v2。若服务器的 138 题归因完整且采用同一空白规则，预期 1197 题纳入、254 题排除（253 图表 + 1 非段落）；这是待验证预期，不是本机实测。分区数应重新统计，不能仍假定 386 |
| QMSum 清洗版 bundle | 归档，标明用了 `[empty]` 转换；新 prepare 直接使用原始 test.jsonl，不用 clean 文件。预期 281 题、35 分区，检查所有原 turn 和 span 索引保持原样 |
| QAMPARI | 用原文件新增 prepare；gold.answers 中空 alias 保留，不改成占位符、不删除整个答案组；查看 data_observations.empty_alias_positions，与服务器发现的 5 处核对 |
| ALCE ELI5 | 没有空 alias 问题，按正常 task=eli5 来源配置准备；按报告预期 1000 cases，不需要清洗 |
| MultiHop / ASQA | 已有原始文件 bundle 不受上述问题影响，可继续保留使用；新版 loader 仍按旧版本校验，不要求仅为升级而重做 |

source 中包和文件哈希要分开命名，例如 `archive_sha256` 表示 ALCE tar，`raw_file_sha256` 表示实际传给 prepare 的 JSON；manifest.files.raw-data 始终由准备器按实际文件计算。更正 source 元数据时重建到新目录，不能手改冻结 bundle 的 source 后继续冒用旧 manifest。

新 bundle：manifest 与 case 都有 `adaptation_revision=notebook-data-v2`。旧 bundle 缺字段时按旧规则读取，旧 case 不补写字段；QASPER 空白误排除仍按旧 decisions 保留。新来源/适配身份不同，不把新旧实验合并为同配置组。

当前 QASPER 排除有图表证据的题符合本阶段纯文本范围，但不能声称保留题全都语义上不依赖图表。剩余章节标题路径也不能擅自视为段落；这次继续按明确的段落范围排除。

## 给服务器 Agent 的交接文字

> 在拿到包含 notebook-data-v2 修正的代码之后，仅做离线数据准备复核，不自动启动 SN 或模型评分。请先确认本地 commit、工作树与更新后的 docs/notebook-data-corrections.md。
>
> 1. 保留旧 bundles，直接使用已下载的原始文件，不重复下载、不修改原文件。
> 2. 将 QASPER prepare 到新目录；按 reason 汇总 decisions，并对比旧题单的新增/丢失 case_id。检查空白匹配只合并空白，原 evidence/文档原文未改；预期可找回服务器先前定位的 138 题，以实际 ID 对账为准。若计数不同，报告差异，不为凑数改数据。
> 3. QMSum 使用原始 test.jsonl 重建新目录，核对 17 个空/空白 turn 原样保留，281 cases/35 partitions，原始 turn 数与 span 索引不变；不能再填 `[empty]`。
> 4. QAMPARI 原始 answers 中空 alias 原样保留，使用 task=qampari 的 source 准备，核对 empty_alias_positions；ELI5 正常准备，无需清洗。
> 5. MultiHop 和 ASQA 原 bundle 可继续保留。source 中 archive/file 哈希分开记录；需更正来源说明时生成新 bundle，不能破坏旧冻结文件。
> 6. 汇报版本、每套选题/排除/分区数量、差异 ID 计数、输入文件与 bundle 哈希、空值观察及离线加载结果。导入/Ask/官方模型评分等实际实验按既有授权另行安排，不把 prepare 成功视为执行验收。

## 本机验证与边界

新增回归先复现空白误排除、空 turn/alias 拒绝和新诊断缺失，再修正实现。现有 venv 全量 Python 离线回归 **333 passed**，socket/getaddrinfo 阻断下网络尝试 **0**。用修正前代码生成的构造 fixture 验证旧 bundle 不增删题；用新构造数据验证原始文件字节、gold、turn 索引和空值处理。没有访问服务器产物或重算其真实计数。修改尚未提交/推送，服务器须拿到本次修正后再按上文操作。

独立定向审阅未发现待修复问题；另将当前 legacy 分支与 525633f 适配结果及评分计划比较，验证四套旧协议一致，定向回归 47 项通过。
