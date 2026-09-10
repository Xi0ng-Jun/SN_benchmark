#!/usr/bin/env python3
"""Create the frozen SQuAD/DROP bundle from verified official releases."""

import argparse
import hashlib
import json
import random
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_eval.public_benchmarks import SEED, build_bundle, sha256_text  # noqa: E402


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def acquire(source, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or file_sha256(path) != source["sha256"]:
        temporary = path.with_suffix(path.suffix + ".part")
        urllib.request.urlretrieve(source["url"], temporary)
        if file_sha256(temporary) != source["sha256"]:
            temporary.unlink()
            raise ValueError(f"source hash mismatch for {path.name}")
        temporary.replace(path)
    if file_sha256(path) != source["sha256"]:
        raise ValueError(f"source hash mismatch for {path.name}")


def load_squad(path, rng):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != "1.1" or not isinstance(payload.get("data"), list):
        raise ValueError("unsupported SQuAD schema/version")
    rows = []
    for article_index, article in enumerate(payload["data"]):
        group = f"squad-article-{sha256_text(article['title'] + ':' + str(article_index))[:16]}"
        for paragraph_index, paragraph in enumerate(article["paragraphs"]):
            qas = [qa for qa in paragraph["qas"] if not qa.get("is_impossible", False)]
            if not qas:
                continue
            qa = rng.choice(qas)
            rows.append({"id": qa["id"], "title": article["title"], "context": paragraph["context"],
                         "question": qa["question"], "answers": qa["answers"], "_group": group,
                         "_document_id": f"squad-{article_index:03d}-{paragraph_index:03d}"})
    return rows


def load_drop(path, rng):
    with zipfile.ZipFile(path) as archive:
        try:
            payload = json.loads(archive.read("drop_dataset/drop_dataset_dev.json"))
        except KeyError as exc:
            raise ValueError("unsupported DROP archive schema") from exc
    rows = []
    for section_id, section in payload.items():
        qa = rng.choice(section["qa_pairs"])
        annotations = [qa["answer"], *qa.get("validated_answers", [])]
        rows.append({"section_id": section_id, "query_id": qa["query_id"],
                     "passage": section["passage"], "question": qa["question"],
                     "_annotations": annotations,
                     "_group": f"drop-section-{sha256_text(section_id)[:16]}",
                     "_document_id": f"drop-{section_id}"})
    return rows


def artifact_entry(path, root):
    return {"path": str(path.relative_to(root)), "sha256": file_sha256(path), "bytes": path.stat().st_size}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "configs/public-benchmark-v1.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/public-benchmark-v1")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if config["seed"] != SEED:
        raise ValueError("config seed does not match protocol")
    raw = args.output / "raw"
    paths = {"squad": raw / "squad-dev-v1.1.json", "drop": raw / "drop_dataset.zip"}
    for name, path in paths.items():
        acquire(config["sources"][name], path)
    squad_rows = load_squad(paths["squad"], random.Random(SEED))
    drop_rows = load_drop(paths["drop"], random.Random(SEED + 1))
    built = build_bundle(squad_rows, drop_rows, args.output, SEED)
    artifacts = []
    for dataset in ("squad", "drop"):
        for filename in ("questions.jsonl", "documents.jsonl"):
            artifacts.append(artifact_entry(args.output / dataset / filename, args.output))
    manifest = {
        "protocol_version": config["bundle_version"], "seed": SEED,
        "verified_tooling": config["verified_tooling"],
        "sampling": {"questions_per_dataset": 100, "debug": 20, "regression": 80,
                     "distractor_passages_per_dataset": 100, "unique_question_passages": True,
                     "group_split_isolation": True, "question_choice": "seeded random, one per passage",
                     "drop_answer_type_sampling": "round-robin strata over primary official annotation type"},
        "sources": config["sources"], "artifacts": artifacts,
        "datasets": {},
        "metric_applicability": {
            "squad": {"answer_correctness": True, "answer_offsets": True, "paragraph_hit": True,
                      "complete_reasoning_evidence_recall": False},
            "drop": {"answer_correctness": True, "answer_offsets": False, "paragraph_hit": True,
                     "complete_reasoning_evidence_recall": False}
        },
        "semantic_notes": [
            "DROP references are complete annotation answers: primary answer plus validated_answers.",
            "Multiple spans inside one DROP annotation are joined with '; ' and are not alternatives.",
            "DROP supplies no complete reasoning-evidence offsets; its evidence list is empty by design."
        ],
        "audit_sample_ids": {}
    }
    for dataset, content in built.items():
        questions, documents = content["questions"], content["documents"]
        manifest["datasets"][dataset] = {
            "question_count": len(questions), "document_count": len(documents),
            "debug_count": sum(q["split"] == "debug" for q in questions),
            "regression_count": sum(q["split"] == "regression" for q in questions),
            "calibration_count": sum(q["calibration"] for q in questions),
            "smoke_count": sum(q["smoke"] for q in questions),
            "answer_type_counts": {kind: sum(q["answer_type"] == kind for q in questions)
                                   for kind in sorted({q["answer_type"] for q in questions})},
            "question_ids_sha256": sha256_text("\n".join(q["id"] for q in questions)),
            "document_ids_sha256": sha256_text("\n".join(d["id"] for d in documents)),
            "group_ids_sha256": sha256_text("\n".join(sorted({q["group"] for q in questions})))
        }
        manifest["audit_sample_ids"][dataset] = [q["id"] for q in random.Random(SEED + 10).sample(questions, 10)]
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({name: manifest["datasets"][name] for name in ("squad", "drop")}, indent=2))


if __name__ == "__main__":
    main()
