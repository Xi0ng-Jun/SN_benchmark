# Benchmark Evaluation as a Formal Protocol

本文用形式化语言描述 Silicon Notebook 的 benchmark 评测流程。
本文定义流程。
本文不替代五套 benchmark 的官方评分器。

## 1. Protocol profile

对每个 benchmark，先固定一个协议 profile：

\[
\mathcal P=(\mathcal C,\mathcal X,\mathcal Y,\pi,\Sigma,\mathcal M)
\]

其中：

- \(\mathcal C=\{c_i\}_{i=1}^{N}\) 是固定题目集合。
- 每道题有唯一的 `case_id`。
- \(\mathcal X\) 是模型可以看到的公开输入。
- \(\mathcal Y\) 是评分侧保存的参考答案和标注。
- \(\pi\) 是公开输入投影函数。
- \(\Sigma\) 是提交格式、预处理和评分依赖。
- \(\mathcal M\) 是该 benchmark 的指标集合。

每道题可以表示为：

\[
c_i=(id_i,x_i^{raw},y_i,\mu_i)
\]

其中 `raw` 表示原始资料，\(y_i\) 表示 gold，\(\mu_i\) 表示题目元数据。

模型实际收到的输入是：

\[
x_i=\pi(x_i^{raw},\mu_i)
\]

生成请求必须满足：

\[
y_i\notin x_i
\]

答案、证据、题型标签和其他 gold 字段只能在评分阶段使用。

## 2. Generation and observation

被测系统由模型、方法组件和运行预算共同决定：

\[
f_{\theta,\kappa,b}(x_i)\rightarrow z_i
\]

其中：

- \(\theta\) 是生成模型身份。
- \(\kappa\) 是检索、分块和重排等组件身份。
- \(b\) 是提示、上下文、token、重试和超时预算。
- \(z_i\) 是原始生成结果。

评测同时保存运行观测：

\[
o_i=(z_i,e_i,r_i,t_i,s_i)
\]

其中：

- \(z_i\) 是答案文本。
- \(e_i\) 是证据或引用观测。
- \(r_i\) 是有序检索结果。
- \(t_i\) 是轨迹和成本观测。
- \(s_i\) 是运行状态。

状态至少包括：

\[
s_i\in\{\text{success},\text{no\_answer},\text{clarification},
\text{error},\text{missing}\}
\]

失败状态必须保留。
系统不能删除失败题。
系统不能按分数选择性重跑。

## 3. Submission projection

原始观测必须转换成 benchmark 要求的提交格式：

\[
\hat y_i=T_{\Sigma}(o_i)
\]

转换只能使用实际观测和公开输入：

\[
T_{\Sigma}(o_i,x_i)\ \perp\ y_i
\]

例如：

- QASPER 将最终引用映射为原始段落。
- MultiHop-RAG 将实际检索结果转换为有序 passage 列表。
- ALCE 将引用转换为候选文档编号。
- QMSum 保留摘要文本。
- HotpotQA 将引用转换为 `[title, sentence_id]`。

证据投影是项目的适配策略。
它不是自动成为 benchmark 官方方法的一部分。

## 4. Scoring

每个指标 \(m\) 定义一个评分函数：

\[
\phi_m(\hat y_i,y_i;\Sigma_m)
\]

其中 \(\Sigma_m\) 固定 scorer 版本、预处理、参数和依赖。

可以逐题评分的指标写为：

\[
\operatorname{Score}_m=
\operatorname{Agg}_m\left(
\left\{\phi_m(\hat y_i,y_i;\Sigma_m)\mid i\in E_m\right\}
\right)
\]

其中 \(E_m\) 是该指标实际适用的题目集合。
指标分母为：

\[
n_m=|E_m|
\]

不同指标可以有不同的 \(E_m\)。
例如 MultiHop QA 使用全部题目，而检索指标排除 null 题。

批量 scorer 不强行改写成逐题平均：

\[
\operatorname{Score}_m=S_{\Sigma_m}
\left(\{\hat y_i\}_{i\in\mathcal C},
\{y_i\}_{i\in\mathcal C}\right)
\]

QMSum Perl ROUGE 的 `Average_F` 就属于这种批量结果。

## 5. Metric state

每个指标还保存一个状态：

\[
\operatorname{status}_m\in\{\text{valid},\text{pending},\text{error}\}
\]

- `valid`：所有必要输入和观测都满足契约。
- `pending`：缺少指标所需观测或依赖。
- `error`：评分过程失败。

`pending` 不能转换成零分。
上下文覆盖不能替代显式证据。
最终答案不能替代检索排名。
组件诊断分不能替代 benchmark 官方分数。

## 6. Pairwise comparison

两个方法 (A) 和 (B) 只有在以下条件成立时才进入正式逐题比较：

\[
\begin{aligned}
&\mathcal C_A=\mathcal C_B\\
&\Sigma_A=\Sigma_B\\
&E_{m,A}=E_{m,B}\\
&\text{双方没有未解释的 missing/error}
\end{aligned}
\]

共同指标的逐题差异为：

\[
\Delta_m=\operatorname{Agg}_m
\left(\{\phi_m(\hat y_i^B,y_i)-
\phi_m(\hat y_i^A,y_i)\}\right)
\]

如果模型、提示、检索器或预算不同，\(\Delta_m\) 只能支持端到端描述。
它不能单独证明某个组件造成了差异。

## 7. Paper-ready description

> We first froze the benchmark data revision, case scope, public-input projection, submission format, and official scoring implementation. For each case, the system received only the permitted public input. Reference answers and annotations were kept on the scoring side. The evaluation runner stored the raw response, retrieval or citation observations, runtime status, and relevant configuration identities. We then projected the observations into the official submission format and applied the fixed scorer for each benchmark metric. Metric-specific denominators and pending states were retained. We reported a metric only when its required observation contract was complete. Pairwise comparison was performed only for methods with the same case IDs, scorer identity, and metric-eligible cases. Results from QASPER, MultiHop-RAG, ALCE, QMSum, and HotpotQA were reported separately. DeepEval component and agent diagnostics were treated as supplementary analysis rather than replacements for official benchmark metrics.

## 8. Implementation anchors

- [Official scoring bridge](../src/rag_eval/benchmark_official.py)
- [Submission construction](../src/rag_eval/benchmark_submission.py)
- [Comparison checks](../src/rag_eval/benchmark_comparison.py)
- [Benchmark standards and conformance](notebook-benchmark-standards-and-conformance.md)
