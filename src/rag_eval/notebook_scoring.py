"""Deterministic notebook scores; no dataset, product or model imports.

metric_specs(case) returns scorer/metric_role/score_kind dictionaries.
score_case(case, product_record, scorer) returns status/score/reason/details.
Scores use 0..1; model-dependent metrics remain unscored until explicit ALCE
execution. Cases follow notebook_data's suite/task/gold schema. Context scoring
requires aligned context/document IDs or aligned source IDs with a complete
source_to_document mapping. A pre-handle prefix is excluded only after exact
capture segmentation is revalidated; the original saved context stays intact.
It never treats a retrieved document as fully read.

Formula sources: QASPER afd0fb96 scripts/evaluator.py; MultiHop-RAG c1c1287
qa_evaluate.py; ALCE 246c476 eval.py and utils.py. Names explicitly distinguish
SN body adaptations from original benchmark runs. Only [kN] markers are removed.
"""
from __future__ import annotations

from collections import Counter
import importlib.metadata
import re
import string

from .notebook_data import ADAPTATION_REVISION, LEGACY_ADAPTATION, OFFICIAL_ADAPTATION_REVISION, whitespace_key

PREFIX = 'product.notebook.'
SN_MARKER = re.compile(r'\[k[1-9]\d*\]')


def body_text(answer):
    return SN_MARKER.sub('', answer)


def _normalize(text):
    text = text.lower().translate(str.maketrans('', '', string.punctuation))
    return ' '.join(re.sub(r'\b(a|an|the)\b', ' ', text).split())


def _f1(prediction, reference):
    predicted, expected = _normalize(prediction).split(), _normalize(reference).split()
    overlap = sum((Counter(predicted) & Counter(expected)).values())
    return 2 * overlap / (len(predicted) + len(expected)) if overlap else 0.0


def _result(score=None, *, status='scored', reason=None, **details):
    return {'status': status, 'score': score, 'reason': reason, 'details': details}


def metric_specs(case):
    """Declare independent metrics, with no total score across these columns."""
    suite, task = case['suite'], case.get('task')
    revision = case.get('adaptation_revision', LEGACY_ADAPTATION)
    if revision not in {LEGACY_ADAPTATION, ADAPTATION_REVISION, OFFICIAL_ADAPTATION_REVISION}:
        raise ValueError('Unsupported notebook adaptation revision')
    # v3 changes the complete source scope, not these SN diagnostic formulas.
    # Original benchmark scoring is a separate explicit submission boundary.
    current = revision != LEGACY_ADAPTATION
    specs = []
    def add(name, role='primary', kind='continuous'):
        specs.append({'scorer': PREFIX + name, 'metric_role': role, 'score_kind': kind})
    if suite == 'qasper':
        add('qasper_answer_token_f1_body_v1')
        add('qasper_context_paragraph_f1_whitespace_v2' if current else 'qasper_context_paragraph_f1_v1', 'diagnostic')
    elif suite == 'multihop_rag':
        add('multihop_official_weak_match_body_v1', kind='binary')
        add('multihop_context_fact_recall_v1', 'diagnostic')
    elif suite == 'alce':
        if task == 'asqa':
            add('alce_asqa_str_em_body_v1')
            add('alce_asqa_str_hit_body_v1', 'diagnostic', 'binary')
        elif task == 'qampari':
            for key in ('prec', 'rec', 'rec_top5', 'f1', 'f1_top5'):
                add(f'alce_qampari_{key}_body_v1', 'primary' if key == 'f1_top5' else 'diagnostic')
        elif task == 'eli5':
            add('alce_eli5_claims_official_v1')
        else:
            raise ValueError(f'Unknown ALCE task: {task}')
        add('alce_citation_rec_official_v1', 'diagnostic')
        add('alce_citation_prec_official_v1', 'diagnostic')
    elif suite == 'qmsum':
        for metric in ('1', '2', 'L'):
            add(f'qmsum_rouge{metric}_f1_body_v1')
        if task == 'specific':
            add('qmsum_context_nonempty_turn_recall_v2' if current else 'qmsum_context_turn_recall_v1', 'diagnostic')
    else:
        raise ValueError(f'Unknown notebook suite: {suite}')
    return specs


def _without_validated_prefix(record, texts, sources):
    """Return handle-bound passages only if the saved prefix is reproducible."""
    from .protocol import capture_context

    if not sources or len(texts) != len(sources) + 1:
        return None
    fields = ('context_block', 'retrieval_context', 'source_ids', 'handles', 'retrieved_ids')
    try:
        if 'captures' in record:
            successful = [c for c in record['captures'] if c.get('succeeded') and c.get('answer', '').strip()]
            if not successful or successful[-1].get('sectioned'):
                return None
            captured = capture_context(successful[-1])
        else:
            handles, object_ids = record['handles'], record['retrieved_ids']
            if (not isinstance(handles, list) or not isinstance(object_ids, list)
                    or len(handles) != len(sources) or len(object_ids) != len(sources)
                    or any(not isinstance(h, str) or not re.fullmatch(r'k\d+', h) for h in handles)
                    or len(set(handles)) != len(handles)):
                return None
            captured = capture_context({'context_block': record['context_block'], 'id_map': {
                handle: {'source_id': source, 'object_id': object_id}
                for handle, source, object_id in zip(handles, sources, object_ids)}})
        if any(captured[field] != record.get(field) for field in fields):
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return texts[1:]


def _mapped_context(record):
    if not record.get('context_supported'):
        return None
    texts = record.get('retrieval_context')
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        return None
    sources, mapping = record.get('source_ids'), record.get('source_to_document')
    documents = record.get('retrieved_document_ids')
    if isinstance(sources, list) and isinstance(mapping, dict):
        if len(texts) != len(sources):
            texts = _without_validated_prefix(record, texts, sources)
        if texts is None or any(not mapping.get(s) for s in sources):
            return None
        mapped = [mapping[s] for s in sources]
        if documents is not None and documents != mapped:
            return None
        return list(zip(texts, mapped))
    if isinstance(documents, list) and len(texts) == len(documents) and all(documents):
        return list(zip(texts, documents))
    return None


def _context_score(case, record, name):
    gold = case['gold']
    if name == 'multihop_context_fact_recall_v1' and (case.get('task') == 'null_query' or gold.get('question_type') == 'null_query'):
        return _result(status='not_applicable', reason='null_query has no retrieval gold')
    mapped = _mapped_context(record)
    if mapped is None:
        return _result(status='not_applicable', reason='complete final-context document mapping unavailable', ranking_available=False)
    context_details = {'unbound_context_indices': [0] if len(record['retrieval_context']) > len(mapped) else []}
    if name in {'qasper_context_paragraph_f1_v1', 'qasper_context_paragraph_f1_whitespace_v2'}:
        key = whitespace_key if name.endswith('_v2') else lambda text: text
        paper_ids = set(case['material_document_ids'])
        context_keys = [(key(text), doc) for text, doc in mapped]
        observed = {key(p['text']) for p in gold['paragraphs'] if key(p['text']) and
                    any(key(p['text']) in text and doc in paper_ids for text, doc in context_keys)}
        scores, recalls = [], []
        for annotation in gold['annotations']:
            expected = {key(text) for text in annotation['evidence']}
            overlap = len(observed & expected)
            scores.append(2 * overlap / (len(observed) + len(expected)) if observed or expected else 1.0)
            recalls.append(overlap / len(expected) if expected else None)
        return _result(max(scores), **context_details, observed_paragraphs=sorted(observed), annotation_scores=scores,
                       annotation_recalls=recalls, ranking_available=False, scope='whole paragraphs in final synthesis context; not predicted evidence')
    if name == 'multihop_context_fact_recall_v1':
        facts = gold.get('evidence', [])
        if not facts:
            return _result(status='not_applicable', reason='gold facts unavailable')
        matched = [i for i, fact in enumerate(facts) if fact['fact'] and any(fact['fact'] in text and doc == fact['document_id'] for text, doc in mapped)]
        return _result(len(matched) / len(facts), **context_details, matched_fact_indices=matched, gold_fact_count=len(facts), ranking_available=False,
                       scope='exact full fact text in final synthesis context')
    turns = {t['id']: t for t in gold['turns']}
    expected_ids = sorted({i for start, end in gold['relevant_text_span'] for i in range(start, end + 1)})
    if name == 'qmsum_context_nonempty_turn_recall_v2':
        excluded = [i for i in expected_ids if not turns[i]['content'].strip()]
        expected_ids = [i for i in expected_ids if turns[i]['content'].strip()]
        context_details['excluded_empty_turn_ids'] = excluded
    if not expected_ids:
        return _result(status='not_applicable', reason='gold turn spans have no nonempty text' if name.endswith('_v2') else
                       'gold turn spans unavailable', **context_details)
    meeting_ids = set(case['material_document_ids'])
    matched = [i for i in expected_ids if turns[i]['content'] and any(turns[i]['content'] in text and doc in meeting_ids for text, doc in mapped)]
    return _result(len(matched) / len(expected_ids), **context_details, matched_turn_ids=matched, gold_turn_ids=expected_ids, ranking_available=False,
                   scope='full turn text coverage; repeated text cannot establish unique occurrence')


def _qampari(body, answers):
    predictions = [_normalize(x.strip()) for x in body.rstrip().rstrip('.').rstrip(',').split(',')]
    predictions = [x for x in predictions if x]
    aliases = [[_normalize(x) for x in answer] for answer in answers]
    flat = [x for answer in aliases for x in answer]
    prec = sum(x in flat for x in predictions) / len(predictions) if predictions else 0.0
    matched = sum(any(x in predictions for x in answer) for answer in aliases)
    rec, top = matched / len(answers), min(5, matched) / min(5, len(answers))
    harmonic = lambda a, b: 2 * a * b / (a + b) if a + b else 0.0
    return {'prec': prec, 'rec': rec, 'rec_top5': top, 'f1': harmonic(prec, rec),
            'f1_top5': harmonic(prec, top), 'num_preds': len(predictions)}


def score_case(case, record, scorer):
    """Score a saved product record, never invoking SN or model inference."""
    if scorer not in {spec['scorer'] for spec in metric_specs(case)}:
        raise ValueError(f'Scorer not declared for this case: {scorer}')
    if record.get('status') != 'success' or not isinstance(record.get('answer'), str) or not record['answer'].strip():
        return _result(status='unscored', reason='no successful nonempty product answer', product_status=record.get('status'))
    name = scorer.removeprefix(PREFIX)
    if name in {'alce_eli5_claims_official_v1', 'alce_citation_rec_official_v1', 'alce_citation_prec_official_v1'}:
        return _result(status='unscored', reason='deferred: requires explicit local official ALCE model scoring command',
                       requires_model=True, command='python -m rag_eval.notebook_alce score --help')
    body, gold = body_text(record['answer']), case['gold']
    try:
        if '_context_' in name:
            return _context_score(case, record, name)
        details = {'scoring_body': body, 'preprocessing': 'remove only SN [kN] markers; preserve entire answer body'}
        if name == 'qasper_answer_token_f1_body_v1':
            scores = [_f1(body, a['answer']) for a in gold['annotations']]
            return _result(max(scores), **details, annotation_scores=scores, source_revision='afd0fb96bf78ce8cd8157639c6f6a6995e4f9089')
        if name == 'multihop_official_weak_match_body_v1':
            match = re.search(r'The answer to the question is "(.*?)"', body)
            prediction = match.group(1) if match else body
            overlap = set(prediction.lower().split()) & set(gold['answer'].lower().split())
            return _result(float(bool(overlap)), **details, matched_tokens=sorted(overlap), extracted_answer=prediction,
                           source_revision='c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8', formula='nonempty intersection of lowercase whitespace-token sets')
        if name.startswith('alce_asqa_'):
            hits = [any(_normalize(a) in _normalize(body) for a in pair['short_answers']) for pair in gold['qa_pairs']]
            em = sum(hits) / len(hits)
            return _result(float(em == 1) if '_hit_' in name else em, **details, short_answer_hits=hits)
        if name.startswith('alce_qampari_'):
            scores = _qampari(body, gold['answers'])
            key = name.removeprefix('alce_qampari_').removesuffix('_body_v1')
            return _result(scores[key], **details, metrics=scores, cot=False)
        if name.startswith('qmsum_rouge'):
            try:
                version = importlib.metadata.version('rouge-score')
                if version != '0.1.2':
                    raise ValueError(f'found {version}')
                from rouge_score.rouge_scorer import RougeScorer
            except (ImportError, ValueError, importlib.metadata.PackageNotFoundError) as exc:
                return _result(status='error', reason='requires local rouge-score==0.1.2; no automatic installation', dependency_error=type(exc).__name__)
            metric = name.removeprefix('qmsum_').removesuffix('_f1_body_v1')
            result = RougeScorer([metric], use_stemmer=True).score(gold['answer'], body)[metric]
            return _result(result.fmeasure, **details, precision=result.precision, recall=result.recall,
                           dependency='rouge-score==0.1.2', use_stemmer=True, original_paper_reproduction=False)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        return _result(status='error', reason='invalid or unavailable scoring inputs', error_type=type(exc).__name__)
    raise ValueError(f'Unknown scorer: {scorer}')


# Display-only descriptions share scorer identities with the execution registry.
def _description(name, formula, *, source='', model=False, context=False):
    return {'name': name, 'method': '显式本地官方模型评分' if model else '确定性 / SN 正文适配' if not context else '确定性 / 最终上下文诊断',
            'inputs': ['保存的 SN 回答', '冻结的公开 gold'] if not context else ['最终合成上下文', '完整文档映射', '冻结的公开 evidence'],
            'formula': formula,
            'implementation': source or 'notebook_scoring.score_case；仅删除 [kN] 引用标记，完整保留其余正文。',
            'limitations': ('默认 unscored，必须显式运行本地官方模型评分；数值除以 100 后为 0..1，未执行真实实验。' if model else
                            '仅最终上下文文本覆盖；不是 Hits@k/MAP/MRR 或模型输出的官方 evidence 分。' if context else
                            'SN 任务适配分，不宣称复现原论文；错误、澄清、缺评分保留 null。'),
            'code': ['src/rag_eval/notebook_alce.py:run_official' if model else 'src/rag_eval/notebook_scoring.py:score_case']}


METRIC_DESCRIPTIONS = {
    PREFIX + 'qasper_answer_token_f1_body_v1': _description('QASPER 答案 token F1', '小写、去 ASCII 标点/冠词、合并空格；词多重集 F1，取所有标注最大值。无共同词为 0（包括双方空串）。', source='QASPER afd0fb96 scripts/evaluator.py；SN 正文仅去 [kN]。'),
    PREFIX + 'qasper_context_paragraph_f1_v1': _description('QASPER 最终上下文整段 F1', '完全包含在同论文实际上下文中的段落集合与各标注 evidence 集合计算 F1，取最大值；双方空集合为 1。', context=True),
    PREFIX + 'qasper_context_paragraph_f1_whitespace_v2': _description('QASPER 空白归一化整段 F1', '段落、gold evidence 和上下文仅合并连续空白、去首尾空白；同论文完整段落集合与 gold 计算 F1，取最大值；不跨 chunk 拼接，不忽略大小写/标点。', context=True),
    PREFIX + 'multihop_official_weak_match_body_v1': _description('MultiHop 官方弱匹配', '先按官方模式提取 The answer to the question is "..."（如存在）；小写、按空白分词，词集合有任意交集 → 1，否则 0。不去标点。', source='MultiHop-RAG c1c1287 qa_evaluate.py；SN 正文仅去 [kN]。'),
    PREFIX + 'multihop_context_fact_recall_v1': _description('MultiHop 最终上下文 fact 覆盖', '在正确文档上下文中完整出现的 gold fact 数 / gold fact 数；null_query 无检索 gold 为 N/A。', context=True),
    PREFIX + 'alce_asqa_str_em_body_v1': _description('ALCE ASQA STR-EM', '每个 qa_pair 的任一归一化 short_answer 是归一化正文子串即命中；取 qa_pair 命中比例。'),
    PREFIX + 'alce_asqa_str_hit_body_v1': _description('ALCE ASQA STR-HIT', '全部 qa_pair 均命中 → 1，否则 0。'),
    PREFIX + 'alce_eli5_claims_official_v1': _description('ALCE ELI5 claims NLI', '官方 AutoAIS 判断回答是否蕴含每个 gold claim；取支持比例。', model=True),
    PREFIX + 'alce_citation_rec_official_v1': _description('ALCE 引用召回', '官方 AutoAIS 对回答单元和联合引用资料判断蕴含，再取单元支持比例；无引用或越界引用为不支持。', model=True),
    PREFIX + 'alce_citation_prec_official_v1': _description('ALCE 引用精确率', '官方 AutoAIS 在联合支持后检查单引用支持或移除引用的影响，必要/支持的引用数除以官方计入的引用总数。', model=True),
    PREFIX + 'qmsum_context_nonempty_turn_recall_v2': _description('QMSum 非空相关发言覆盖', '完整匹配的相关非空发言数 / span 并集内非空发言数；保留原始 turn 编号并记录排除的空发言，全部为空时 N/A。重复文本不证明唯一位置。', context=True),
    PREFIX + 'qmsum_context_turn_recall_v1': _description('QMSum 特定查询发言覆盖', '正确会议上下文完整包含的相关发言文本数 / span 并集发言数；同文复现不证明唯一发言位置。', context=True),
}
for _key, _formula in {
    'prec': '正确预测项数 / 非空预测项数；保留重复预测，任一 gold alias 精确归一化匹配算正确。',
    'rec': '至少一个 alias 命中的 gold 答案组数 / 全部 gold 答案组数。',
    'rec_top5': 'min(5, 命中 gold 答案组数) / min(5, gold 答案组数)。',
    'f1': 'precision 与 recall 的调和平均；两者均零则为 0。',
    'f1_top5': 'precision 与 recall_top5 的调和平均；两者均零则为 0。',
}.items():
    METRIC_DESCRIPTIONS[PREFIX + f'alce_qampari_{_key}_body_v1'] = _description(
        f'ALCE QAMPARI {_key}', _formula,
        source='ALCE 246c476 eval.py；逗号分隔完整正文，cot=False，规范化同官方；仅去 SN [kN]。')
for _key in ('1', '2', 'L'):
    METRIC_DESCRIPTIONS[PREFIX + f'qmsum_rouge{_key}_f1_body_v1'] = _description(
        f'QMSum ROUGE-{_key} F1', f'rouge-score==0.1.2 的 rouge{_key}.fmeasure；use_stemmer=True。',
        source='固定 Python rouge-score 0.1.2；缺包或版本不同记 error，不自动安装；不是原论文复现。')
