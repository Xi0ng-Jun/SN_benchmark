"""Read-only, same-question QMSum comparison; does not reuse SN mode pairing IDs."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from pathlib import Path
import re

from .artifacts import save_json
from .notebook_baseline import BASELINE_VERSION
from .notebook_data import VERSION as SN_VERSION
from .starter_protocol import fingerprint
from .starter_report import load_run

SCORER_FILES = ('src/rag_eval/notebook_scoring.py', 'src/rag_eval/notebook_data.py', 'src/rag_eval/protocol.py')


def _scoring_identity(identity):
    code = identity['code']
    hashes = code.get('benchmark_source_hashes', {})
    versions = {re.sub(r'[-_.]+', '-', k).lower(): v for k,v in code.get('versions', {}).items()}
    if any(not hashes.get(p) for p in SCORER_FILES) or any(not versions.get(p) for p in ('rouge-score', 'nltk')):
        raise ValueError('Comparison requires saved scorer source hashes and rouge-score/nltk versions')
    return dict(sources={p: hashes[p] for p in SCORER_FILES},
                versions={p: versions[p] for p in ('rouge-score', 'nltk')})


def _cohort(paths, baseline):
    paths = [Path(p).resolve() for p in paths]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError('Provide nonempty distinct run directories in each cohort')
    rows, runs, family = {}, [], None
    source, scoring, mode = None, None, None
    for path in paths:
        run = load_run(path)
        m = run['manifest']
        expected = BASELINE_VERSION if baseline else SN_VERSION
        if (not m or m.get('product_protocol') != expected or m['suite'] != 'qmsum'
                or m['track'] != 'R' or m['mode'] not in ({'bm25'} if baseline else {'chunk', 'reasoning'})):
            raise ValueError('Comparison requires QMSum BM25 versus one SN mode')
        identity = m['identity']
        config = deepcopy(identity)
        config.pop('product_bundle')
        config['notebook_context'].pop('partition_id')
        current_family = fingerprint({**config, 'mode': m['mode']})
        if family is not None and family != current_family:
            raise ValueError('A cohort mixes model, method, mode, source, code or runtime configurations')
        family, mode = current_family, m['mode']
        source, scoring = identity['source'], _scoring_identity(identity)
        observed = {(r['case_id'], r['scorer']): r for r in run['scores']}
        outputs = {r['case_id']: r for r in run['outputs']}
        partition = identity['notebook_context']['partition_id']
        for plan in run['planned']:
            if not plan['scorer'].startswith('product.notebook.qmsum_'):
                continue  # SN-only citation-object diagnostics have no BM25 counterpart.
            key = (plan['task'], plan['case_id'], plan['scorer'])
            if key in rows:
                raise ValueError('A cohort contains duplicate cases; select one attempt per partition')
            score = observed.get((plan['case_id'], plan['scorer']))
            output = outputs.get(plan['case_id'])
            rows[key] = dict(partition_id=partition, status=score['status'] if score else 'missing',
                score=score['score'] if score else None, reason=score.get('reason') if score else 'score not recorded',
                output_reason=output.get('reason') if output else 'output not recorded',
                output_status=output['status'] if output else 'missing',
                output_available=bool(output and output['output_available']), run_path=str(path))
        runs.append(dict(path=str(path), run_id=m['run_id'], state=run['state'],
                         warnings=run['warnings'], identity=identity, protocol_id=m['protocol_id']))
    return dict(rows=rows, runs=runs, config_family=family, source=source, scoring=scoring, mode=mode)


def compare_runs(baseline_dirs, sn_dirs):
    left, right = _cohort(baseline_dirs, True), _cohort(sn_dirs, False)
    if left['source'] != right['source']:
        raise ValueError('Comparison requires exactly the same frozen source, cases and partition policy')
    if left['scoring'] != right['scoring']:
        raise ValueError('Scorer code or dependency versions differ; do not pair these scores')
    keys = sorted(left['rows'].keys() | right['rows'].keys())
    groups, pairs = defaultdict(list), []
    for key in keys:
        a, b = left['rows'].get(key), right['rows'].get(key)
        if a and b and a['partition_id'] != b['partition_id']:
            raise ValueError('The same case has different source partitions')
        both = bool(a and b and a['status'] == b['status'] == 'scored')
        pair = dict(task=key[0], case_id=key[1], scorer=key[2], baseline=a, sn=b,
                    delta_sn_minus_baseline=b['score']-a['score'] if both else None)
        pairs.append(pair)
        groups[(key[0], key[2])].append(pair)
    summaries = []
    for (task, scorer), items in sorted(groups.items()):
        common = [r for r in items if r['baseline'] and r['sn']]
        paired = [r for r in items if r['delta_sn_minus_baseline'] is not None]
        stats = dict(task=task, scorer=scorer, common_planned=len(common), paired_scored=len(paired),
                     paired_score_coverage=len(paired)/len(common) if common else None,
                     mean_delta_sn_minus_baseline=sum(r['delta_sn_minus_baseline'] for r in paired)/len(paired) if paired else None)
        for name in ('baseline', 'sn'):
            planned = [r[name] for r in items if r[name]]
            scored = [r for r in planned if r['status'] == 'scored']
            stats.update({f'{name}_planned': len(planned), f'{name}_scored': len(scored),
                f'{name}_only_planned': len(planned)-len(common),
                f'{name}_saved_outputs': sum(r['output_available'] for r in planned),
                f'{name}_score_coverage': len(scored)/len(planned) if planned else None,
                f'{name}_mean_over_scored': sum(r['score'] for r in scored)/len(scored) if scored else None,
                f'{name}_mean_over_paired': sum(r[name]['score'] for r in paired)/len(paired) if paired else None})
        summaries.append(stats)
    return dict(format='qmsum-baseline-comparison-v1', sn_mode=right['mode'], model_alignment='not_verified',
                release_gate=False, source=left['source'], scoring_identity=left['scoring'], groups=summaries, pairs=pairs,
                baseline_runs=left['runs'], sn_runs=right['runs'],
                limitations=['同题端到端系统对照；SN 内部提示与 BM25 提示不同，差值不能仅归因于检索。',
                    '程序未验证 SN 最终回答模型与 baseline tested 的实际模型/采样设置一致；需核对两侧配置。',
                    '主指标是适配后的 ROUGE，不是原论文复现；上下文覆盖仅作诊断。',
                    '差值只用共同有效评分题；失败和缺失保留 null，并报告覆盖率；不是统计显著性结论。'])


def write_comparison(baseline_dirs, sn_dirs, output):
    baseline_dirs, sn_dirs = list(baseline_dirs), list(sn_dirs)
    output = Path(output).resolve()
    for path in baseline_dirs + sn_dirs:
        path = Path(path).resolve()
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError('Comparison output must be outside all input runs')
    data = compare_runs(baseline_dirs, sn_dirs)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output/'comparison.json', data)
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
    def number(value):
        return '—' if value is None else f'{value:.4f}'
    lines = ['# QMSum：BM25 与 SN ' + data['sn_mode'], '', *data['limitations'], '',
             '| task / metric | BM25 已评分/计划 | SN 已评分/计划 | 共同有效/共同计划 | BM25 配对均分 | SN 配对均分 | SN − BM25 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for g in data['groups']:
        values = [g['task']+' / '+g['scorer'], f"{g['baseline_scored']}/{g['baseline_planned']}",
                  f"{g['sn_scored']}/{g['sn_planned']}", f"{g['paired_scored']}/{g['common_planned']}",
                  number(g['baseline_mean_over_paired']), number(g['sn_mean_over_paired']), number(g['mean_delta_sn_minus_baseline'])]
        lines.append('| '+' | '.join(map(cell, values))+' |')
    lines += ['', '逐题差值、单侧题、未评分原因状态、运行配置及警告见 comparison.json。',
              '不同模型配置可以作系统对照，但不能标注为已控制生成模型的检索实验。']
    (output/'comparison.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return output
