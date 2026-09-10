#!/usr/bin/env python3
"""Audit and summarize a completed experimental public-system run."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3
import shutil
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from rag_eval.artifacts import digest, save_json
from rag_eval.datasets import load_jsonl
from rag_eval.metrics import score_ranking


def main():
    p = argparse.ArgumentParser()
    p.add_argument('run_dir', type=Path)
    args = p.parse_args()
    run = args.run_dir.resolve()
    meta = json.loads((run / 'run.json').read_text())
    db = sqlite3.connect('file:' + str(run / 'runtime/database.db') + '?mode=ro', uri=True)
    report = ['# Silicon Notebook 公开数据集实验', '',
              '实验性：使用两个数据集原始顺序前 50 个问题及其正证据文档的并集。',
              '这是受限候选库实验，非完整语料库评测；不得与此前全库 BM25 分数直接比较。', '',
              '## 运行配置', '', f"- 项目 revision：`{meta['project_revision']}`。",
              f"- DeepEval：`{meta['deepeval_version']}`；无外层人为时限。",
              '- 生产代码未修改；独立 SQLite、来源、索引和 LLM 缓存位于本实验 runtime 目录。',
              '- `upload_sources` 同步导入 Markdown，原生切块与向量化，构建 scale ANN 后调用 `repo.ask(mode=chunk)`。',
              '- 本次未构建 KG，因此实际走 chunk-only 的 MMR/多查询融合分支；虽配置 reranker，本次没有触发 KG mix 重排分支，不声称测过其质量。',
              '- 全部实际答案和带 k 标记的实际生成上下文保留，评测侧未截断或合并。',
              '- 确定性指标：对实际生成上下文按顺序映射文档 ID、去重，然后计算文档级 @10。',
              '- 项目 Ask 的默认 chunk 选择预算与文档级 @10 口径分别记录，不将二者混同。',
              '- 代码中的示例 metric 阈值不作为验收门禁；本报告只呈现实测分数。',
              f"- 模型配置 SHA-256：`{meta['model_config_sha256']}`。",
              f"- 检索配置：`{json.dumps(meta['retrieval_settings'], ensure_ascii=False)}`。", '']
    for role in ('ask_answer', 'retrieval_query_embedding', 'retrieval_rerank'):
        service = meta['models'][meta['bindings'][role]]
        report.append(f"- `{role}`：`{service['model']}`，配置并发 {service['max_concurrency']}。")
    report += ['- DeepEval judge 复用 ask_answer；以上 rerank 仅记录配置，本次未调用。', '']
    summaries = {}
    usage = Counter()
    statuses = Counter()
    scheduler = Counter()
    for path in (run / 'runtime/logs').glob('*/events-*.jsonl'):
        for row in load_jsonl(path):
            if row.get('kind') == 'model_scheduler':
                scheduler[(row.get('workload_id'), row.get('model'), row.get('status'))] += 1
    for path in (run / 'runtime/logs').glob('*/llm-*.jsonl'):
        for row in load_jsonl(path):
            statuses[(row.get('kind', ''), row.get('status', ''))] += 1
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                usage[key] += (row.get('usage') or {}).get(key, 0) or 0
    save_json(run / 'runtime-observations.json', {
        'scope': 'isolated LLM logs only; initial preparation logs excluded',
        'logged_calls_by_kind_status': [{'kind': k[0], 'status': k[1], 'count': v} for k, v in statuses.items()],
        'scheduler_events': [{'workload': k[0], 'model': k[1], 'status': k[2], 'count': v} for k, v in scheduler.items()],
        'logged_token_usage': dict(usage), 'monetary_cost': None,
        'cost_limitation': 'No provider billing reconciliation or pinned price table'})
    attempts = [{'answer_id': row[0], 'notebook_id': row[1], 'question': row[2],
                 'response': json.loads(row[3]), 'created_at': row[4]}
                for row in db.execute('SELECT id, notebook_id, question, payload, created_at FROM answers ORDER BY created_at, id')]
    save_json(run / 'answer-attempts.json', attempts)
    for name in ('multihop-rag', 'scifact'):
        folder = run / name
        qs = load_jsonl(folder / 'questions.jsonl')
        records = load_jsonl(folder / 'answers.jsonl')
        judges = json.loads((folder / 'deepeval-results.json').read_text())
        mapping = json.loads((folder / 'document-map.json').read_text())
        assert len(records) == len(qs) == meta['limit']
        assert len(judges) == len(qs)
        assert {q['id'] for q in qs} == {r['id'] for r in records} == {r['id'] for r in judges}
        reverse = {v['source_id']: k for k, v in mapping.items()}
        scores = []
        for r in records:
            assert r['retrieved_document_ids'] == [reverse[s] for s in r['source_ids']]
            assert r['answer'] == r['response']['answer']
            assert '\n'.join(r['retrieval_context']) == r['context_block']
            for chunk_id, source_id in zip(r['retrieved_ids'], r['source_ids']):
                assert db.execute('SELECT source_id FROM chunks WHERE id=?', (chunk_id,)).fetchone()[0] == source_id
            score = score_ranking(r['retrieved_document_ids'], r['relevance'], 10)
            assert score == r['metrics_at_10']
            if score is not None:
                scores.append(score)
        notebook = meta['datasets'][name]['notebook_id']
        chunks = db.execute('SELECT count(*) FROM chunks WHERE notebook_id=?', (notebook,)).fetchone()[0]
        embedded = db.execute('SELECT count(*) FROM chunk_embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE notebook_id=?)', (notebook,)).fetchone()[0]
        assert chunks == embedded
        totals = defaultdict(list)
        errors = []
        by_id = {r['id']: r for r in records}
        for case in judges:
            original = by_id[case['id']]
            expected_metrics = {'Contextual Relevancy', 'Faithfulness', 'Answer Relevancy'}
            if original['expected_answer']:
                expected_metrics |= {'Contextual Recall', 'Contextual Precision'}
            if original['answer'].strip():
                assert {m['metric'] for m in case['metrics']} == expected_metrics
            else:
                assert case.get('skipped_no_actual_answer') and not case['metrics']
            for metric in case['metrics']:
                if metric['error'] or metric['score'] is None:
                    errors.append({'id': case['id'], **metric})
                else:
                    totals[metric['metric']].append(metric['score'])
        summary = {'questions': len(records), 'with_gold': len(scores), 'documents': len(mapping),
                   'chunks': chunks, 'embedded_chunks': embedded,
                   'deterministic': {k: sum(s[k] for s in scores)/len(scores) for k in scores[0]},
                   'retrieval_zero_hit_ids': [r['id'] for r in records if r['metrics_at_10'] is not None and not r['metrics_at_10']['hit']],
                   'retrieval_incomplete_evidence_ids': [r['id'] for r in records if r['metrics_at_10'] is not None and not r['metrics_at_10']['complete_evidence']],
                   'deepeval': {k: {'count': len(v), 'mean': sum(v)/len(v)} for k, v in totals.items()},
                   'judge_errors': errors, 'llm_modes': dict(Counter(r['response']['llm_mode'] for r in records)),
                   'judge_metric_attempts': sum(len(r['metrics']) for r in judges),
                   'judge_metric_valid': sum(len(v) for v in totals.values()),
                   'judge_seconds_sum': sum(m['seconds'] for r in judges for m in r['metrics']),
                   'judge_skipped_missing_reference': dict(Counter(m for r in judges for m in r.get('skipped_missing_reference', []))),
                   'judge_skipped_no_actual_answer': sum(bool(r.get('skipped_no_actual_answer')) for r in judges),
                   'ask_seconds_sum': sum(r['latency_seconds'] for r in records),
                   'model_errors': [r['id'] for r in records if r['response']['model_errors']]}
        summaries[name] = summary
        state = meta['datasets'][name]
        report += [f'## {name}', '', f"来源版本：`{state['manifest']['revision']}`；许可：{state['manifest'].get('license', '未记录')}。",
                   f"问题 {len(records)} 条，有正证据标注 {len(scores)} 条；文档 {len(mapping)} 篇，chunk {chunks} 个，向量 {embedded} 个。", '',
                   '| Hit@10 | Recall@10 | MRR@10 | nDCG@10 | 完整证据集 |',
                   '|---:|---:|---:|---:|---:|',
                   '| ' + ' | '.join(f'{v:.4f}' for v in summary['deterministic'].values()) + ' |', '',
                   f"未命中问题 ID：`{summary['retrieval_zero_hit_ids']}`；未找齐证据 {len(summary['retrieval_incomplete_evidence_ids'])} 条，完整 ID 列表见 summary.json。", '',
                   '| DeepEval 指标 | 有效样本 | 均分 |', '|---|---:|---:|']
        report += [f"| {k} | {v['count']} | {v['mean']:.4f} |" for k, v in summary['deepeval'].items()]
        report += ['', f"Judge 执行错误 {len(errors)} 项；Ask 模型错误样本 {len(summary['model_errors'])} 条。",
                   f"Judge 共尝试 {summary['judge_metric_attempts']} 项，有效 {summary['judge_metric_valid']} 项；失败项不填零、不计入均分。",
                   f"缺少参考答案跳过：`{summary['judge_skipped_missing_reference']}`；累计指标耗时 {summary['judge_seconds_sum']:.1f} 秒。",
                   f"Ask 状态：`{summary['llm_modes']}`；最终保存样本累计 Ask 耗时 {summary['ask_seconds_sum']:.1f} 秒，不含此前失败后重试的尝试。", '']
        if errors:
            report += ['| 问题 ID | 指标 | 执行错误 |', '|---|---|---|']
            report += [f"| {e['id']} | {e['metric']} | {e['error'] or 'missing score'} |" for e in errors]
            report.append('')
    report += ['## 限制与解释', '',
               '- 文档由所选问题的证据标注决定，缺少全库干扰文档，检索分数可能偏高；不据此设产品阈值。',
               '- MultiHop-RAG 前 50 条中的无正证据问题不计入 ID 召回指标；仍保存其真实答案和 judge 结果。',
               '- SciFact 原任务为科学声明证据检索，当前归一化数据没有参考答案；Contextual Recall/Precision 明确不适用，未生成替代答案。',
               '- SciFact 其余 DeepEval 指标只观察系统对声明的回答与上下文，不等同于原任务的支持/反驳分类准确率。',
               '- 使用项目配置的相同 chat 模型生成和评审，可能有共同偏差；未做人审校准。',
               '- 原始公开标注直接复用，没有把模型生成内容提升为 gold。',
               '- 导入时曾有一篇论文元数据提取 malformed_response；其正文、chunk、向量完整性另行验证。',
               '- MultiHop-RAG 第 38 条首次因服务商拒绝输入而合成失败，恢复执行时成功。最终样本结果使用恢复后的回答；answer-attempts.json 保留包括首次失败在内的数据库记录，不将重跑后成功当成首次成功率。',
               '- 不覆盖 UI、PDF 解析、reasoning 模式、KG 质量、完整拒答与引用正确性，也未建立回归门禁。',
               '- 当前已有运行记录支持重放和审计；远端模型响应不保证逐字重现。', '']
    report += ['## 审计偏差', '',
               '- 初始导入遗漏 LLM_LOG_PATH 隔离，向项目默认 LLM 日志位置写入日志；后续问答和评审已纠正。生产数据库和索引未用于本实验。',
               '- 初始导入的脚本 SHA 已记录，但原始脚本快照未保存。后续问答恢复版与 judge 脚本保存在 code/；当前维护版本另有元数据加锁与分阶段记录修正。',
               '- 详细历史与证据边界见 provenance-notes.md。这些偏差限制历史逐字重建，不改变已核验的文档、上下文和实际回答。', '']
    report += ['## 结果文件', '',
               '- `summary.json`：确定性指标、judge 均分及有效数、逐项错误、耗时。',
               '- 每个数据集的 `questions.jsonl`、`document-map.json`、`answers.jsonl`、`deepeval-results.json`：输入、映射、实际回答与上下文、逐项评分及理由。',
               '- `answer-attempts.json`：全部问答尝试，包括首次失败后恢复的记录。',
               '- `run.json`、`environment.json`、`code/`、`artifact-sha256.json`：数据来源版本、模型与检索配置、依赖、脚本快照、产物摘要。',
               '- `runtime-observations.json`：隔离日志中可见的调用与 token 使用；不含初始导入遗漏隔离的 LLM 日志，不能当成完整账单。',
               '- `provenance-notes.md`：隔离与版本记录偏差。', '']
    save_json(run / 'summary.json', summaries)
    meta['status'] = 'completed_with_metric_errors' if any(s['judge_errors'] for s in summaries.values()) else 'completed'
    meta['audited_at'] = datetime.now(timezone.utc).isoformat()
    save_json(run / 'run.json', meta)
    code = run / 'code'
    code.mkdir(exist_ok=True)
    for source in (ROOT / 'src/rag_eval').glob('*.py'):
        dest = code / 'rag_eval' / source.name
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(source, dest)
    shutil.copy2(__file__, code / 'report_public_system.py')
    shutil.copy2(ROOT / 'scripts/run_public_system.py', code / 'run_public_system.maintained.py')
    (run / 'report.md').write_text('\n'.join(report), encoding='utf-8')
    artifact_hashes = {str(p.relative_to(run)): digest(p) for p in run.glob('**/*')
                       if p.is_file() and 'runtime' not in p.relative_to(run).parts and p.suffix in ('.json', '.jsonl', '.py', '.md') and p.name != 'artifact-sha256.json'}
    save_json(run / 'artifact-sha256.json', artifact_hashes)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    db.close()


if __name__ == '__main__':
    main()
