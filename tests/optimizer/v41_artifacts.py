"""Coherent synthetic SN answer sheets, using real bundle/scorer validators."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json

from rag_eval.benchmark_official import prepare_inputs, scorer_identity
from rag_eval.benchmark_submission import build_submission, sn_submission_identity
from rag_eval.identity import fingerprint
from rag_eval.workbench.run_views import RunEntry, RunViews
from test_benchmark_comparison import examples, rehash, save


@dataclass
class Artifact:
    entry: RunEntry
    outputs: list
    submission: dict
    scores: dict


class ArtifactFactory:
    """Two real QMSum cases; all score numbers are declared synthetic fixtures."""

    def __init__(self, root):
        self.root = root
        self.bundle, _, _, templates = examples(root, count=1)
        self.template = templates[0]
        self.ids = [case['case_id'] for case in self.bundle['cases']]

    def make(self, execution, *, statuses=('success', 'success'), values=(.25, .75),
             errors=None, records=None, source_manifest=None, packages=None):
        errors, records = errors or {}, records or {}
        run_id = execution + '-1'
        path = self.root / run_id
        path.mkdir()
        (path / 'input').symlink_to(self.root / 'bundle', target_is_directory=True)
        identity = dict(source=deepcopy(source_manifest or self.bundle['manifest']),
                        notebook_context=dict(request_revision='notebook-request-v3'))
        save(path / 'manifest.json', dict(run_id=run_id, mode='chunk', suite='qmsum', identity=identity))
        outputs = []
        for index, task in enumerate(self.ids):
            status = statuses[index]
            answer = 'Cats run.' if status == 'success' else ''
            record = dict(status=status, answer=answer, response=dict(answer=answer),
                          usage=dict(total_tokens=100 + index), **records.get(index, {}))
            if index in errors:
                record.update(error_type=errors[index], failed_phase=record.get('failed_phase', 'asking'))
            outputs.append(dict(case_id=task, status=status, prediction=answer,
                                output_available=bool(answer), product_record=record))
        output_bytes = ''.join(json.dumps(row) + '\n' for row in outputs).encode()
        (path / 'outputs.jsonl').write_bytes(output_bytes)
        (path / 'planned.jsonl').write_text(''.join(json.dumps(dict(case_id=task)) + '\n' for task in self.ids))
        save(path / 'state.json', dict(phase='finished_with_errors' if 'error' in statuses else 'finished'))
        method = dict(name='synthetic-sn-chunk', kind='sn', citation_style='sn',
                      model_identity=dict(model='synthetic-no-model'), input_policy='frozen-source-documents',
                      configuration=dict(mode='chunk', identity=sn_submission_identity(identity),
                                         comparison_category='controlled-rerun'))
        predictions = [dict(case_id=row['case_id'], status=row['status'], prediction=row['prediction'],
                            record=row['product_record'], run_id=run_id,
                            outputs_sha256=hashlib.sha256(output_bytes).hexdigest()) for row in outputs]
        submission = build_submission(self.bundle, method=method, predictions=predictions)
        prepared = prepare_inputs(self.bundle, submission)
        scores = deepcopy(self.template)
        eligible = [task for task, value in zip(self.ids, values) if value is not None]
        observed = [value for value in values if value is not None]
        scores.update(method=submission['method'], method_id=submission['method_id'],
                      submission_id=fingerprint(submission), coverage=submission['coverage'],
                      prepared_id=fingerprint(prepared), profile=prepared['profile'],
                      metrics=dict(rougeL=sum(observed) / len(observed)),
                      batch_only_metrics=[], metric_denominators=dict(rougeL=len(eligible)),
                      metric_case_ids=dict(rougeL=eligible))
        for row, status, value in zip(scores['per_case'], statuses, values):
            row.update(status=status, metrics=dict(rougeL=value) if value is not None else {})
        if packages is not None:
            scores['dependencies']['packages'] = packages
            scores['scorer_identity'] = scorer_identity(scores['profile'], scores['dependencies'])
            scores['scorer_id'] = fingerprint(scores['scorer_identity'])
        rehash(scores)
        save(path / 'submission.json', submission)
        save(path / 'prepared.json', prepared)
        save(path / 'scores.json', scores)
        entry = RunEntry(run_id, path, path / 'scores.json', case_ids=tuple(self.ids),
                         execution_state='finished', suite='qmsum', mode='chunk')
        return Artifact(entry, outputs, submission, scores)

    @staticmethod
    def views(*artifacts):
        return RunViews([artifact.entry for artifact in artifacts])
