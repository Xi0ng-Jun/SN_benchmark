"""Normalize, sample, and validate the frozen public benchmark bundle."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path


SEED = 20260909


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unique(values):
    return list(dict.fromkeys(values))


def normalize_squad(row: dict, split: str, calibration: bool = False) -> dict:
    answers = row["answers"]
    if isinstance(answers, list):
        answers = {"text": [answer["text"] for answer in answers],
                   "answer_start": [answer["answer_start"] for answer in answers]}
    if len(answers["text"]) != len(answers["answer_start"]):
        raise ValueError("SQuAD answer texts and starts differ in length")
    context = row["context"]
    evidence = []
    for text, start in zip(answers["text"], answers["answer_start"]):
        end = start + len(text)
        if context[start:end] != text:
            raise ValueError(f"SQuAD answer offset mismatch for {row['id']}")
        marker = {"text": text, "start": start, "end": end}
        evidence.append(marker)
    references = _unique(answers["text"])
    if not references:
        raise ValueError(f"SQuAD question {row['id']} has no answer")
    document_id = row.get("_document_id", f"squad-{row['title']}-{row['id']}")
    group = row.get("_group", f"squad-article-{sha256_text(row['title'])[:16]}")
    return {
        "id": f"squad-{row['id']}", "dataset": "squad", "question": row["question"],
        "references": references, "expected_answer": references[0],
        "gold_document_ids": [document_id], "split": split, "group": group,
        "answer_type": "span", "evidence": evidence, "diagnostic": split == "debug",
        "calibration": calibration, "smoke": False,
    }


def _drop_answer(annotation: dict) -> tuple[str, str]:
    number = str(annotation.get("number", "")).strip()
    if number:
        return number, "number"
    date = annotation.get("date") or {}
    date_parts = [str(date.get(key, "")).strip() for key in ("month", "day", "year")]
    if any(date_parts):
        return " ".join(part for part in date_parts if part), "date"
    spans = [str(value).strip() for value in annotation.get("spans", []) if str(value).strip()]
    if spans:
        return "; ".join(spans), "span" if len(spans) == 1 else "spans"
    raise ValueError("DROP annotation has no answer")


def normalize_drop(row: dict, split: str, calibration: bool = False) -> dict:
    annotations = row.get("_annotations")
    if annotations is None:
        flat = row["answers_spans"]
        spans = list(flat["spans"])
        types = list(flat["types"])
        if not spans or len(spans) != len(types):
            raise ValueError(f"DROP flattened answer mismatch for {row['query_id']}")
        answer_type = _unique(types)[0]
        # The HF representation cannot distinguish annotator alternatives. It is
        # accepted only as one composite annotation; production preparation uses
        # the official JSON with `_annotations`.
        annotations = [{"spans": spans}]
    parsed = [_drop_answer(annotation) for annotation in annotations]
    references = _unique(answer for answer, _ in parsed)
    types = _unique(kind for _, kind in parsed)
    answer_type = types[0] if len(types) == 1 else "mixed"
    document_id = row.get("_document_id", f"drop-{row['section_id']}")
    return {
        "id": f"drop-{row['query_id']}", "dataset": "drop", "question": row["question"],
        "references": references, "expected_answer": " OR ".join(references),
        "gold_document_ids": [document_id], "split": split,
        "group": row.get("_group", f"drop-section-{sha256_text(row['section_id'])[:16]}"),
        "answer_type": answer_type, "evidence": [], "diagnostic": split == "debug",
        "calibration": calibration, "smoke": False,
    }


def _document(row: dict, dataset: str) -> dict:
    text = row["context"] if dataset == "squad" else row["passage"]
    identity = row["_document_id"]
    title = row.get("title", row.get("section_id", identity))
    return {"id": identity, "title": title, "text": text, "text_sha256": sha256_text(text),
            "document_sha256": sha256_text(json.dumps({"id": identity, "title": title, "text": text}, sort_keys=True))}


def _stratified_take(rows, count, key):
    buckets = {}
    for row in rows:
        buckets.setdefault(key(row), []).append(row)
    selected = []
    while len(selected) < count and buckets:
        for name in sorted(list(buckets)):
            if len(selected) == count:
                break
            selected.append(buckets[name].pop())
            if not buckets[name]:
                del buckets[name]
    if len(selected) != count:
        raise ValueError(f"cannot select {count} stratified rows")
    chosen = {id(row) for row in selected}
    return selected, [row for row in rows if id(row) not in chosen]


def _sample_dataset(rows: list[dict], dataset: str, rng: random.Random):
    by_doc = {}
    for row in rows:
        if row["_document_id"] in by_doc:
            continue
        by_doc[row["_document_id"]] = row
    if len(by_doc) < 200:
        raise ValueError(f"{dataset} needs at least 200 unique passages")
    candidates = list(by_doc.values())
    rng.shuffle(candidates)
    if dataset == "drop":
        selected, remaining = _stratified_take(candidates, 100, lambda row: _drop_answer(row["_annotations"][0])[1])
        distractors = remaining[:100]
    else:
        selected, distractors = candidates[:100], candidates[100:200]

    # Assign complete groups to debug until exactly 20 passages are available.
    groups = {}
    for row in selected:
        groups.setdefault(row["_group"], []).append(row)
    group_items = list(groups.items())
    rng.shuffle(group_items)
    reachable = {0: set()}
    for group, members in group_items:
        for total, chosen in list(reachable.items())[::-1]:
            new_total = total + len(members)
            if new_total <= 20 and new_total not in reachable:
                reachable[new_total] = chosen | {group}
    debug_groups = reachable.get(20)
    if debug_groups is None:
        raise ValueError(f"{dataset} group split cannot produce exactly 20 debug questions")
    questions = []
    for row in selected:
        split = "debug" if row["_group"] in debug_groups else "regression"
        normalizer = normalize_squad if dataset == "squad" else normalize_drop
        questions.append(normalizer(row, split))
    debug = [q for q in questions if q["split"] == "debug"]
    regression = [q for q in questions if q["split"] == "regression"]
    for q in rng.sample(debug, 10):
        q["calibration"] = True
    for q in rng.sample(regression, 10):
        q["smoke"] = True
    documents = [_document(row, dataset) for row in selected + distractors]
    return questions, documents


def build_bundle(squad_rows: list[dict], drop_rows: list[dict], output: Path, seed: int = SEED):
    for dataset, rows in (("squad", squad_rows), ("drop", drop_rows)):
        ids = [row.get("_document_id", row.get("id")) for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate {dataset} passage")
    output = Path(output)
    results = {}
    for offset, (dataset, rows) in enumerate((("squad", squad_rows), ("drop", drop_rows))):
        questions, documents = _sample_dataset(rows, dataset, random.Random(seed + offset))
        target = output / dataset
        target.mkdir(parents=True, exist_ok=True)
        for name, values in (("questions.jsonl", questions), ("documents.jsonl", documents)):
            with (target / name).open("w", encoding="utf-8") as handle:
                for value in values:
                    handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        results[dataset] = {"questions": questions, "documents": documents}
    return results
