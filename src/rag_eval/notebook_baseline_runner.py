"""Explicit execution boundary for the QMSum baseline; reports never import clients."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

from .artifacts import digest, save_json, save_jsonl
from .notebook_baseline import BASELINE_VERSION, baseline_config, partition_turns, plan_rows, run_qmsum_case
from .notebook_bundle import load_bundle, partition_bundle
from .starter_protocol import fingerprint
from .starter_results import EventJournal


def execute(*, root, project, bundle_dir, run, partition_id, model_config,
            top_k=8, max_context_chars=12000):
    root, project, bundle_dir, run, model_config = [Path(p).resolve() for p in
                                                  (root, project, bundle_dir, run, model_config)]
    for forbidden in (root/'src', root/'scripts', project, bundle_dir, model_config):
        if run.is_relative_to(forbidden) or forbidden.is_relative_to(run):
            raise ValueError('Run must be separate from code/product/input/model config')
    if run.exists():
        raise ValueError('Use a new run directory; no implicit resume')
    config = baseline_config(top_k, max_context_chars)
    bundle = load_bundle(bundle_dir)
    if bundle['manifest']['suite'] != 'qmsum':
        raise ValueError('Only QMSum is supported by this baseline')
    product = partition_bundle(bundle, partition_id)
    members = {q['case_id'] for q in product['questions']}
    cases = [c for c in bundle['cases'] if c['case_id'] in members]
    if not cases:
        raise ValueError('Cannot execute an empty partition')
    from .starter_runtime import configure_environment, make_adapter, resolve_models, snapshot_sources
    resolved, public_models = resolve_models(model_config, ['tested'])
    run.mkdir(parents=True, exist_ok=False)
    save_json(run/'state.json', dict(phase='initializing'))
    try:
        shutil.copytree(bundle_dir, run/'input')
        if load_bundle(run/'input') != bundle:
            raise ValueError('Input changed while copying')
        turns = partition_turns(run/'input', product)
        save_json(run/'product-bundle.json', product)
        code = snapshot_sources(root, project, run)
        # Only SN's explicit model client is reused. No production dotenv or service
        # registry, no repository/notebook creation, no ingestion or Ask pipeline.
        settings, runtime = configure_environment(project, run, product_track=False,
                                                   document_limit=bundle['manifest']['max_documents'])
        identity = dict(source=bundle['manifest'], models=public_models, code=code,
                        runtime_settings=runtime['comparable_settings_sha256'],
                        product_services=runtime['service_config_sha256'], audits_sha256=fingerprint([]),
                        track='R', product_bundle=dict(protocol_version=BASELINE_VERSION,
                                                       material_manifest=product['manifest']),
                        notebook_context=dict(partition_id=partition_id, selected_cases=bundle['manifest']['selected_cases'],
                                              partition_count=bundle['manifest']['partition_count']),
                        baseline=config)
        protocol_id = fingerprint({**identity, 'mode': 'bm25'})
        planned = plan_rows(cases, run.name, protocol_id)
        save_jsonl(run/'planned.jsonl', planned)
        save_json(run/'manifest.json', dict(format='public-starter-run-v1', run_id=run.name, suite='qmsum',
                  track='R', mode='bm25', product_protocol=BASELINE_VERSION,
                  protocol_id=protocol_id, pairing_id=fingerprint(identity), identity=identity,
                  models=public_models, source_manifest=bundle['manifest'],
                  planned_sha256=digest(run/'planned.jsonl'), planned_predictions=len(cases),
                  planned_scores=len(planned), human_calibration='pending', release_gate=False))
        from pydantic import BaseModel

        class BaselineAnswer(BaseModel):
            answer: str

        errors = False
        with EventJournal(run/'model-events.jsonl') as events, EventJournal(run/'outputs.jsonl') as outputs, \
                EventJournal(run/'scores.jsonl') as scores:
            adapter = make_adapter(resolved['tested'], 'tested', settings, events)
            for case in cases:
                save_json(run/'state.json', dict(phase='generating', case_id=case['case_id']))
                request_id = f'{protocol_id[:16]}-{uuid.uuid4().hex}'

                def generate(prompt):
                    with adapter.for_case(case['case_id'], request_id):
                        return adapter.generate(prompt, BaselineAnswer).answer

                def save_output(output):
                    outputs(output)
                    save_json(run/'state.json', dict(phase='scoring', case_id=case['case_id']))

                output, case_scores = run_qmsum_case(case, generate, turns=turns, top_k=top_k,
                    max_context_chars=max_context_chars, run_id=run.name, protocol_id=protocol_id,
                    output_sink=save_output, score_sink=scores)
                errors |= output['status'] == 'error'
                for score in case_scores:
                    errors |= score['status'] == 'error'
        save_json(run/'state.json', dict(phase='finished_with_errors' if errors else 'finished'))
    except BaseException as exc:
        previous = json.loads((run/'state.json').read_text())
        save_json(run/'state.json', dict(phase='interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed',
                  error_type=type(exc).__name__, failed_phase=previous.get('phase'), case_id=previous.get('case_id')))
        raise
    return run
