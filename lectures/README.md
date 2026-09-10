# Executable DeepEval Lectures

这些 lecture 采用 Stanford CS336 `lecture_XX.py` 的组织方式：课程内容是 Python 程序，章节由函数组成。它们同时支持普通 Python 输出和官方 `edtrace` trace；后者会把 Markdown、代码位置和 `@inspect` 变量快照交给浏览器中的 trace viewer。

## 运行

```bash
cd /home/wabiwabi/silicon-notebook/benchmark-deepeval
.venv/bin/python lectures/lecture_01.py
.venv/bin/python lectures/lecture_02.py
.venv/bin/python lectures/lecture_03.py
.venv/bin/python lectures/lecture_04.py
```

## 浏览器逐步阅读与运行

安装官方 edtrace 后生成 trace：

```bash
cd /home/wabiwabi/silicon-notebook/benchmark-deepeval
.venv/bin/pip install --no-deps 'edtrace>=0.1.12' beautifulsoup4 sympy
PYTHONPATH=lectures:src .venv/bin/python scripts/build_lecture_traces.py
```

然后获取官方 trace viewer 并启动前端（首次使用）：

```bash
git clone https://github.com/percyliang/edtrace.git
npm install --prefix edtrace/frontend
npm run --prefix=edtrace/frontend dev
```

浏览器打开 `http://localhost:5173?trace=/absolute/path/to/benchmark-deepeval/var/traces/lecture_01.json`。在页面中可以按 lecture 层级展开每一步，查看对应源码、Markdown/图片渲染和 `@inspect` 标记的变量快照；切换 `lecture_02` 等 trace 即可阅览其他课次。

`edtrace` 的执行接口和前端流程遵循 Stanford CS336 官方仓库：`python -m edtrace.execute -m lecture_01` 生成 `var/traces/lecture_01.json`，前端通过 `?trace=...` 加载它。

`lecture_01` 到 `lecture_02` 完全不需要外部模型。`lecture_03` 会尝试导入当前项目的 DeepEval metric 工厂，但在缺少可选依赖或模型服务时只显示环境状态。`lecture_04` 只读取已记录的公开检索 artifact。

## 课程顺序

1. **对象与运行方式**：从 `LLMTestCase`、Dataset、Metric、Trace 建立心智模型。
2. **RAG 数据协议**：区分 gold evidence、retrieved IDs 和实际上下文，并手算 Recall@K/MRR。
3. **LLM judge**：理解五个 RAG metric 的问题边界、指标冲突和阈值校准。
4. **公开 baseline**：复现实验材料中的 BM25 结果，并明确它不能代表线上 Silicon Notebook 质量。

## 与正式实验的边界

lecture 中的样本是教学样本，不会自动成为正式 goldens。正式数据仍需人工确认答案、必要证据和可回答性；正式结果必须保存数据版本、模型、embedding、检索模式和运行参数。
