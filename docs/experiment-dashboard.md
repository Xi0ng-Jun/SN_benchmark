# 实验地图与单题回放

文档状态：当前实现说明。只读已保存工件，不启动 SN、judge 或数据下载。当前总览见 [评测状态](evaluation-status.md)。


Dashboard v3 从已保存的实验文件生成本地报告，依次回答：做了哪些实验、资料和答卷如何流转、每一步实际拿到什么、分数如何产生、哪些结果可以比较。它只读工件，不调用 SN、judge 或下载数据，不重新计算实验分数。

## 打开一份真实报告

使用 Python 3.11+，无需安装 DeepEval。显式列出需要纳入的运行目录，避免混入不相关实验：

```bash
python scripts/build_experiment_dashboard.py \
  /absolute/path/to/qmsum-chunk \
  /absolute/path/to/qmsum-reasoning \
  /absolute/path/to/qmsum-bm25 \
  /absolute/path/to/qmsum-rescoring \
  --output /absolute/path/to/reports/experiment-map-001
```

也支持 `--runs-root /absolute/path/to/runs` 自动发现。输出目录必须是新的目录，且不能与任何输入 run 互相包含。双击 `dashboard.html` 即可；不需要 HTTP 服务、CDN 或联网。旧 HTML 不会自动升级，重新生成报告即可。

**复制报告时应复制整个输出目录，包括 `details/`。** 首页只加载摘要；点题时再载入该题完整数据。单独拷贝 HTML 会使详情无法加载，页面会明确提示。

| 产物 | 保存什么 |
| --- | --- |
| `dashboard.html` | 内嵌样式、页面代码和轻量摘要，三种联动视图 |
| `dashboard-data.json` | 实验关系、运行配置、题目预览、评分摘要、指标说明 |
| `details/<observation_id>.js` | 单题完整 case、输出、评分细节、逻辑步骤、原生组件和轨迹 |
| `summary.json` | 生成运行、重评分运行、问答、评分分别统计 |
| `audit.json` | 运行警告、缺文件和报告边界 |

详情片只是 JSON 的本地脚本包装，支持 `file://` 打开；不包含待执行的模型操作。原始文本以安全文本渲染。结构化密钥和服务地址字段脱敏，但这不是自由文本隐私审查，分享前仍需检查私有语料。

## 先体验构造演示

```bash
python scripts/build_dashboard_demo.py --output /absolute/path/to/demo-new
```

打开打印的 `report/dashboard.html`。演示使用一场手写会议、三道问题、chunk / reasoning / BM25 和一个重评分分支；没有下载 QMSum，没有调用模型。BM25 检索和客观指标确实在这些构造文本上计算，SN 回答、span、judge 分数是明确标注的演示数据，不能用于判断 SN 能力。若环境有 `rouge-score==0.1.2`，演示会计算其 BM25 ROUGE 评分；缺少该可选依赖时仍会生成报告，但对应 objective 条目保留为 `error`，不会填 0。读取已有真实结果的报告命令不需要这个依赖。

推荐浏览顺序：实验地图选择 reasoning → 单题流程 → 检索 → 原生调用树中的一次检索 → 查看该 span 的输入、证据和 Contextual Relevancy → 切换评分步骤 → 结果分析比较同题。

## 三个视图共用同一个选择

### 实验地图

数据来源、生成运行、答卷和评分批次通过连线展示。来源身份使用冻结内容的指纹；重评分连线必须能由 `origin_manifest_sha256`、保存的 base manifest 和答卷哈希证明，不能根据目录名猜测。

同一份答卷重新评分不会变成一次新的问答。来源 run 没有纳入时显示“外部来源”，不跟随记录中的磁盘路径偷偷加载它。初始化未完成的运行也可以展示，但不会编造题目或答卷。

地图回答的是**“这些实验和结果之间有什么关系”**。每个节点是一批资料、一次运行、一套答卷或一批评分，不是 SN 内部的一次调用。以同一份 QMSum 数据为例，chunk / reasoning / BM25 分别生成答卷；补评从已有答卷连接到“重新评分”，不会多出一套新生成答卷。SN 内部检索、合成和模型调用在“单题回放”查看；能力差异和分数比较在“结果分析”查看。

画布操作：

- 鼠标拖动或单指触摸平移，滚轮围绕鼠标位置缩放；拖动节点不会误触发选择。
- 工具栏 `− / ＋` 缩放，`显示全部` 包含所有节点，`100%` 恢复原尺寸，`定位所选` 将选中节点带回可读视野。
- `展开画布` 使用更多屏幕空间，`Esc` 收起。大量运行适配全图后文字会较小，可用定位所选、放大和拖动查看。
- 键盘聚焦画布后，方向键平移，`+ / −` 缩放，`0 / Home` 显示全部。Tab 可选择节点，Enter 或空格查看详情；移到画面外的节点会自动带入视野。
- 切换地图、回放、分析保留地图视野；改变窗口大小会保持全图适配或当前浏览位置。

白底细线区分结构，蓝色表示所选节点及其直接关系。实线表示来源，虚线表示复用答卷；完整关系及配置在节点详情保留。为减少交叉，画布不重复画出已由路径表达的评分归属关系，例如 `运行 → 答卷 → 评分` 已显示时，不再叠加 `运行 → 评分`。这只改变展示，报告 JSON 和详情中的原始关系不删除。

### 单题流程

横向流程包括原始题目、适配提问、资料分区、准备/导入、检索、回答生成、答卷和评分。选择节点可以查看输入、输出、字段含义、工件和代码入口。

- `recorded`：冻结协议或文件能证明这项配置/转换。
- `observed`：已有对应实际输出或观测记录。
- `missing`：没有保存足够信息，不能还原该步。

播放、前后步和重置只改变当前选中的阶段，**不会重新执行实验，也不代表真实耗时**。真实原生 span 有时序时才显示时序；没有保存调用树的旧实验仍可检查答卷和分数，但不补造检索/模型调用。

原生 DeepEval 展示 `sn-deepeval-native-v1` 的 `agent/components.jsonl`、`native-traces.jsonl`、`native-scores.jsonl` 和诊断。相同 request 的评分前/结束快照保留在详情，调用树优先使用最终快照；组件以 request / sample / span 身份关联，不能只按相似名字关联。

导入节点只显示资料，参考答案只在原题和评分侧出现。reasoning 的多次检索、多次分节合成各自保留，不拼成一次不存在的模型调用。BM25 展示原本保存的 prompt、turn 排名和上下文，不伪装成 SN span。

URL 中保存视图、run、题目、步骤和 span；复制定位链接后可回到同一处。接收者仍需取得同一份完整报告。不同机器的本地路径不同，可复制 `#` 后的定位部分到该机器的报告地址。

### 结果分析

侧栏组合 Benchmark、Task、Track、Mode、Scorer、Scope、评分/输出状态、运行和配置等标签。不同标签组取交集，同组多选取并集。选项随其他条件动态变化，已选但匹配数变为零的选项仍可取消。搜索针对轻量索引中的题目预览、身份和评分原因；不为了搜索而自动加载全量证据。

图表展示状态、同口径分布和均值，列表能跳回单题流程。保存多个筛选组后比较结果和配置。分数 `0` 是有效零分；error、not_applicable、unscored、missing 均不补零。题数、问答尝试数、评分条目数分开，组件多次调用不能被当作更多题目。

## 比较的解释

| 比较对象 | 如何展示和解释 |
| --- | --- |
| 同源同题、同指标的 chunk / reasoning | 显示配置差异、共同题、共同有效题和配对差值；配置不同则标明整个配置系统的比较 |
| 同一冻结会议题目的 BM25 / SN | 同资料与客观评分口径核实后比较系统结果；不同模型、prompt、检索预算等差异必须保留，不能把差值只归因于检索 |
| 原生整轨迹指标 | 还要求 judge 和评测协议一致；同题关联不等于模型或策略单变量实验 |
| 检索、分节合成组件 | 每题可能有不同数量的调用，只展示分布和逐条归属，不构造一对一调用差值 |
| 不同 source、case、scorer、judge 或重复题记录 | 明确不可比原因，仍可分别查看原始记录 |

多会议可以按同一冻结来源汇总；共同有效题差值与两组各自有效题均值不是同一个统计量，界面分别展示。没有跨指标“总质量分”、显著性声明或发布门禁。参考答案与各指标实际算法见[指标实现表](benchmark-metrics-reference.md)。

重评分分支保留其批次身份；同时选中原评分和重评分可能造成同题重复，页面要求收窄到明确批次，不自动挑最高分。

## 实现入口与边界

| 职责 | 代码 |
| --- | --- |
| CLI、独立输出目录、静态打包 | `scripts/build_experiment_dashboard.py`、`src/rag_eval/experiment_aggregation.py:write_dashboard` |
| 校验来源、原生身份和重评分关系，写索引与详情片 | `src/rag_eval/explorer_artifacts.py` |
| 工件到逻辑步骤、字段解释和代码位置 | `src/rag_eval/explorer_steps.py` |
| 标签、配对、URL 和树选择等纯计算 | `src/rag_eval/dashboard/explorer-core.js` |
| 地图/流程/分析与懒加载 | `src/rag_eval/dashboard/app.js`、`template.html`、`style.css` |
| 地图布局、平移缩放与键盘/触摸操作 | `src/rag_eval/dashboard/map-view.js` |

现有 `aggregate_runs` 公共汇总接口保持原契约；`write_dashboard` 生成 v3 页面。没有维护第二套旧页面，也不迁移旧私有 Agent 树。代码位置用于理解当前实现；只有已核实的 `source/` 快照能证明某次历史运行的实际源码。

报告是生成时快照。运行仍在写入时，不完整 JSONL 尾行按已有规则披露，身份或哈希冲突直接失败。应优先报告完成的运行；需要刷新时写入新目录。只有本次提供的运行进入分母，尚未创建的实验不是“缺失题”。

首版本地验收使用构造数据和真实浏览器；真实服务器的大工件仍需服务器验收。没有启动新实验、修改 SN 或扩展新 benchmark。

2026-09-23 地图修订：修复固定 510 高度、按容器压缩列间距导致的裁切与重叠，并补上之前缺失的平移缩放事件。节点使用独立于视口的布局坐标，新增画布工具栏、关系说明、配置详情和分析入口；减少多色底块、阴影及重复连线。28 项相关 Python、34 项 JavaScript 检查通过；Chromium 检查拖动、缩放、键盘、390px 窄屏、触摸、展开、切换视图、窗口变化和 312 个构造节点，页面错误及 HTTP 请求为零。这是离线 UI 验证，不是新的 SN 实验。

如需复现交互回归，先用 `build_dashboard_demo.py` 生成演示，再运行：

```bash
node --test tests/dashboard_core.test.cjs tests/explorer_core.test.cjs tests/map_view.test.cjs
node tests/dashboard_map_browser.cjs /absolute/path/to/demo/report/dashboard.html
```

第二条需要本机已有 Playwright 和 Chromium；可通过 `PLAYWRIGHT_MODULE`、`CHROMIUM_EXECUTABLE` 指定现有安装路径。生成报告本身不依赖浏览器。代码更新后仍需重新生成 HTML，旧报告不会自动获得这些操作。

## 给服务器 Agent 的 prompt

```text
请只生成新版离线实验地图，不重跑 SN、不调用 judge、不重新评分。
读取 AGENTS.md 和 docs/experiment-dashboard.md。在独立 checkout/worktree 使用包含当前 Dashboard 实现的固定提交；当前本地主线已合并该功能，若服务器远程尚未包含该提交，先核对实际提交身份再交付。不要改正在运行实验使用的 checkout。
显式列出本次 QMSum chunk/reasoning、BM25、重评分和新版原生 DeepEval 的真实 run 目录，用 scripts/build_experiment_dashboard.py 写入一个独立的新报告目录。相互不可比的批次仍应保留其配置与分数身份，不改原工件来凑配对。
检查地图的生成/重评分关系；各选一题查看资料、实际问题、检索、答卷、评分和已保存的原生 span；检查 BM25/SN 比较的配置差异、共同题和缺分。没有原生轨迹的旧实验应显示未采集，不能伪造。
给出报告整个目录的获取方式（HTML 加 details/ 等文件必须一起复制），运行数、真正生成的问答数、评分数、文件体积及实际浏览器问题。校验失败时报告原因；不修改实验、不做新模型测试、不公开上传报告。
```
