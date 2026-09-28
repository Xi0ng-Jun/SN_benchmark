"""Pinned implementation of the HotpotQA v1 evaluator formulas.

The upstream evaluator is intentionally small.  This module keeps the exact
normalization, set-based supporting-fact score and joint composition while
returning structured values for the frozen submission protocol.
"""
from __future__ import annotations

from collections import Counter
import re
import string


SOURCE_COMMIT = 'fa3a36370899e1d85822de61e58c85ea19993154'
SOURCE_URL = 'https://raw.githubusercontent.com/hotpotqa/hotpot/' + SOURCE_COMMIT + '/hotpot_evaluate_v1.py'
SOURCE_REVISION = 'hotpot_evaluate_v1.py@' + SOURCE_COMMIT
SOURCE_SHA256 = 'd35fc91a6db21d791dbdda11daf3856e9359f5701d54e3eefba20d88fecc02c0'


def normalize_answer(value):
    text = value.lower()
    text = ''.join(ch for ch in text if ch not in set(string.punctuation))
    text = re.sub(r'\b(a|an|the)\b', ' ', text)
    return ' '.join(text.split())


def answer_scores(prediction, gold):
    predicted = normalize_answer(prediction)
    expected = normalize_answer(gold)
    if predicted in {'yes', 'no', 'noanswer'} and predicted != expected:
        return dict(em=0.0, f1=0.0, prec=0.0, recall=0.0)
    if expected in {'yes', 'no', 'noanswer'} and predicted != expected:
        return dict(em=0.0, f1=0.0, prec=0.0, recall=0.0)
    prediction_tokens, gold_tokens = predicted.split(), expected.split()
    common = Counter(prediction_tokens) & Counter(gold_tokens)
    overlap = sum(common.values())
    if not overlap:
        return dict(em=float(predicted == expected), f1=0.0, prec=0.0, recall=0.0)
    precision = overlap / len(prediction_tokens)
    recall = overlap / len(gold_tokens)
    return dict(em=float(predicted == expected), f1=2 * precision * recall / (precision + recall),
                prec=precision, recall=recall)


def supporting_fact_scores(prediction, gold):
    predicted, expected = set(map(tuple, prediction)), set(map(tuple, gold))
    tp = len(predicted & expected)
    fp, fn = len(predicted - expected), len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return dict(em=float(not fp and not fn), f1=f1, prec=precision, recall=recall)


def score_one(answer, gold_answer, supporting_facts=None, gold_supporting_facts=None):
    answer = answer_scores(answer, gold_answer)
    if supporting_facts is None or gold_supporting_facts is None:
        return {'answer_em': answer['em'], 'answer_f1': answer['f1'],
                'answer_prec': answer['prec'], 'answer_recall': answer['recall']}
    support = supporting_fact_scores(supporting_facts, gold_supporting_facts)
    joint_prec, joint_recall = answer['prec'] * support['prec'], answer['recall'] * support['recall']
    joint_f1 = 2 * joint_prec * joint_recall / (joint_prec + joint_recall) if joint_prec + joint_recall else 0.0
    return {'answer_em': answer['em'], 'answer_f1': answer['f1'],
            'answer_prec': answer['prec'], 'answer_recall': answer['recall'],
            'supporting_fact_em': support['em'], 'supporting_fact_f1': support['f1'],
            'supporting_fact_prec': support['prec'], 'supporting_fact_recall': support['recall'],
            'joint_em': answer['em'] * support['em'], 'joint_f1': joint_f1,
            'joint_prec': joint_prec, 'joint_recall': joint_recall}


def aggregate(rows):
    if not rows:
        raise ValueError('HotpotQA score requires at least one row')
    keys = tuple(rows[0])
    return {key: sum(row[key] for row in rows) / len(rows) for key in keys}
