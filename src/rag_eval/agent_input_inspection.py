"""Inspect saved trajectory judge inputs without running a judge or altering traces.

Uses the installed SDK's default text templates. Dynamic requests which depend
on earlier judge responses cannot be measured offline and are explicitly unknown.
"""
from __future__ import annotations

from collections import Counter
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from typing import Any

from .agent_deepeval import available_agent_metrics, trajectory_skip_reason
from .agent_evaluator import _envelope, _record_parts
from .agent_trace import AgentTraceEnvelope
from .artifacts import save_json, save_jsonl


def _size(text: str) -> dict[str, Any]:
    data = text.encode('utf-8')
    return {'chars': len(text), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def inspect_tree(tree: dict[str, Any]) -> dict[str, Any]:
    """Count each string value once at its owning span; children stay separate.

    JSON string bytes include quotes/escapes, but exclude keys, scalar values and
    layout. Subtree totals overlap and must not be added across ancestor spans.
    Only equal complete string values >=256 characters are grouped as repeated.
    """
    from deepeval.utils import serialize_to_json

    trace_size = _size(serialize_to_json(tree, indent=2))
    spans, strings = [], {}

    def string_fields(value, path):
        if isinstance(value, str):
            yield path, value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from string_fields(item, f'{path}.{key}')
        elif isinstance(value, list):
            for index, item in enumerate(value):
                yield from string_fields(item, f'{path}[{index}]')

    def visit(node, path):
        fields = []
        for key, value in node.items():
            if key == 'children':
                continue
            for location, text in string_fields(value, f'{path}.{key}'):
                info = _size(text)
                info['json_bytes'] = len(serialize_to_json(text).encode('utf-8'))
                fields.append({'path': location, **info})
                if len(text) >= 256:
                    group = strings.setdefault(info['sha256'], {
                        **info, 'occurrences': 0, 'locations': [],
                    })
                    group['occurrences'] += 1
                    if len(group['locations']) < 10:
                        group['locations'].append(location)
        own = sum(field['json_bytes'] for field in fields)
        row = {'path': path, 'name': node.get('name'), 'type': node.get('type'),
               'own_string_json_bytes': own, 'subtree_string_json_bytes': own,
               'largest_string_fields': sorted(fields, key=lambda f: f['json_bytes'], reverse=True)[:3]}
        spans.append(row)
        for index, child in enumerate(node.get('children', [])):
            row['subtree_string_json_bytes'] += visit(child, f'{path}.children[{index}]')
        return row['subtree_string_json_bytes']

    string_bytes = visit(tree, '$')
    repeated = [{**group, 'repeated_json_bytes': (group['occurrences'] - 1) * group['json_bytes'],
                 'locations_truncated': group['occurrences'] > len(group['locations'])}
                for group in strings.values() if group['occurrences'] > 1]
    repeated.sort(key=lambda group: group['repeated_json_bytes'], reverse=True)
    return {'trace': trace_size, 'span_count': len(spans), 'spans': spans,
            'string_json_bytes': string_bytes, 'other_json_bytes': trace_size['bytes'] - string_bytes,
            'repeated_string_group_count': len(repeated),
            'repeated_string_json_bytes': sum(group['repeated_json_bytes'] for group in repeated),
            'repeated_strings': repeated[:20], 'repeated_strings_min_chars': 256}


def inspect_envelope(envelope: AgentTraceEnvelope) -> dict[str, Any]:
    """Project exactly as scoring does, then render only knowable SDK requests."""
    result = {'case_id': envelope.case_id, 'mode': envelope.mode,
              'completeness': envelope.completeness, 'completeness_reason': envelope.completeness_reason,
              'status': 'not_applicable', 'context_fit': 'unknown', 'token_count': None,
              'dynamic_prompts_measured': False, 'static_prompts': [], 'metrics': {}}
    for name in available_agent_metrics():
        reason = trajectory_skip_reason(envelope, name)
        result['metrics'][name] = ({'status': 'not_applicable', 'reason': reason} if reason
                                   else {'status': 'input_inspected'})
    if envelope.execution_trace is None or envelope.completeness != 'complete':
        return result

    from deepeval.templates import resolve_template
    from deepeval.utils import serialize_to_json

    tree = envelope.to_deepeval_dict()
    result.update(inspect_tree(tree))
    result['status'] = 'inspected'
    trace_json = serialize_to_json(tree, indent=2)
    # SDK 4.2.2: these extraction prompts need only the saved trace. Other
    # requests need the judge's task/plan/outcome and must remain unmeasured.
    templates = (
        ('TaskCompletionMetric', 'extract_task_and_outcome_from_trace', 'trace_json',
         ('task_completion',)),
        ('StepEfficiencyMetric', 'extract_task_from_trace', 'trace_json',
         ('step_efficiency', 'plan_quality', 'plan_adherence')),
        ('PlanAdherenceMetric', 'extract_plan_from_trace', 'trace_json_str',
         ('plan_quality', 'plan_adherence')),
    )
    for template_class, method, argument, names in templates:
        users = [name for name in names if result['metrics'][name]['status'] == 'input_inspected']
        if not users:
            continue
        prompt = resolve_template('metrics', template_class, method, **{argument: trace_json})
        result['static_prompts'].append({'template': f'{template_class}.{method}',
                                          'metrics': users, **_size(prompt)})
    return result


def _write_report(output: Path, summary, rows) -> None:
    def cell(value):
        return str(value).replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')

    lines = ['# Agent 评分输入检查', '',
             f"记录数：{summary['record_count']}；DeepEval：{summary['deepeval_version']}；未调用 judge。", '',
             '大小为 UTF-8 字节；字符数与哈希见 JSON。没有模型 tokenizer，token 数和能否装入上下文均为 unknown。',
             '已测静态提示词包含 SDK 评分指令；后续依赖 judge 回答的提示词、provider 包装和输出预留未测。', '',
             '| case | mode | 状态 | span 数 | 轨迹 JSON 字节 | 最大已测提示词字节 | 字节预算 |',
             '| --- | --- | --- | ---: | ---: | ---: | --- |']
    for row in rows:
        values = (row['case_id'], row['mode'], row['status'], row.get('span_count', '—'),
                  row.get('trace', {}).get('bytes', '—'),
                  max((p['bytes'] for p in row['static_prompts']), default='—'), row['byte_budget_status'])
        lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
    lines += ['', '## 主要来源（每题前三个步骤）', '',
              '按步骤自身字符串值的 JSON 字节排序，排除子步骤，避免父子重复计数。', '',
              '| case | mode | 步骤路径 | 名称 | 自身字符串 JSON 字节 |',
              '| --- | --- | --- | --- | ---: |']
    for row in rows:
        largest = sorted(row.get('spans', []), key=lambda s: s['own_string_json_bytes'], reverse=True)[:3]
        for span in largest:
            lines.append('| ' + ' | '.join(cell(v) for v in (row['case_id'], row['mode'], span['path'],
                         span['name'], span['own_string_json_bytes'])) + ' |')
    lines += ['', '## 如何使用', '',
              '- 逐题字段、重复内容哈希与位置见 `agent-inputs.jsonl`，报告不复制资料正文或模型消息。',
              '- 重复检测只匹配至少 256 字符的完整字符串，未识别嵌在更长 prompt 中的相同段落。重复不证明适配器有错，也不代表可以删除。',
              '- 字节预算仅检查已测提示词；不是模型 token 窗口，不自动拦截现有评分命令。',
              '- 对照服务器实际模型的输入限制、tokenizer 和输出预留，再决定完整轨迹评分是否可行。',
              '- 如更换 judge，先复用最长的 reasoning 轨迹检查可行性；正式对比时 chunk/reasoning 使用同一 judge 和新的评分目录。',
              '- 若完整轨迹仍装不下，分别设计组件评测；组件分不合并冒充完整 Agent 分。', '']
    (output / 'agent-input-report.md').write_text('\n'.join(lines), encoding='utf-8')


def inspect_run(run_dir: str | Path, output_dir: str | Path, *,
                max_prompt_bytes: int | None = None) -> dict[str, Any]:
    """Read saved outputs; emit only size diagnostics in a fresh separate directory."""
    run, output = Path(run_dir).resolve(), Path(output_dir).resolve()
    if output == run or output.is_relative_to(run):
        raise ValueError('Inspection output must be separate from the immutable run')
    if output.exists():
        raise FileExistsError(f'Inspection output directory already exists: {output}')
    if max_prompt_bytes is not None and (type(max_prompt_bytes) is not int or max_prompt_bytes <= 0):
        raise ValueError('max_prompt_bytes must be a positive integer')
    source = (run / 'outputs.jsonl').read_bytes()
    sdk_version = version('deepeval')
    rows = []
    for line in source.splitlines():
        row = json.loads(line)
        case, observed = _record_parts(row)
        envelope = _envelope(row, case, observed)
        try:
            result = inspect_envelope(envelope)
        except Exception as exc:
            # Do not copy an exception's possible prompt/corpus content into a summary.
            result = {'case_id': envelope.case_id, 'mode': envelope.mode, 'status': 'error',
                      'error_type': type(exc).__name__, 'reason': 'SDK projection or template inspection failed',
                      'static_prompts': [], 'context_fit': 'unknown', 'token_count': None}
        prompts = result['static_prompts']
        if max_prompt_bytes is None or not prompts:
            result['byte_budget_status'] = 'not_checked'
        else:
            result['byte_budget_status'] = ('over_byte_budget' if any(
                p['bytes'] > max_prompt_bytes for p in prompts) else 'within_byte_budget')
        rows.append(result)
    summary = {'format': 'sn-agent-input-inspection-v1', 'run_dir': str(run),
               'source_sha256': hashlib.sha256(source).hexdigest(), 'deepeval_version': sdk_version,
               'record_count': len(rows), 'judge_enabled': False, 'context_fit': 'unknown',
               'max_prompt_bytes': max_prompt_bytes, 'dynamic_prompts_measured': False,
               'status_counts': dict(Counter(r['status'] for r in rows)),
               'over_byte_budget_count': sum(r['byte_budget_status'] == 'over_byte_budget' for r in rows),
               'scope': 'default_text_trajectory_templates; no DAG or provider request framing'}
    output.mkdir(parents=True, exist_ok=False)
    save_jsonl(output / 'agent-inputs.jsonl', rows)
    save_json(output / 'agent-input-summary.json', summary)
    _write_report(output, summary, rows)
    return summary
