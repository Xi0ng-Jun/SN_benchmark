"""Feature facts and identity tiers, without any SN/model execution."""
from copy import deepcopy
import importlib
import json

import pytest

from rag_eval.identity import fingerprint


def api():
    return importlib.import_module('rag_eval.feature_identity')


def runtime():
    return {'overrides': {
        'CHUNK_KG_OVERLAY_ENABLED': 'false', 'KG_AUTO_EXTRACT': 'false',
        'AGENT_PROFILE_ENABLED': 'false', 'USER_SEARCH_PROFILE_ENABLED': 'false',
        'RETRIEVAL_EXPERIENCE_ENABLED': 'false', 'RETRIEVAL_EXPERIENCE_INJECT_ENABLED': 'false',
        'REASONING_CONSULT_MEMORY_ENABLED': 'false', 'GENERATED_QUESTION_INDEX_MODE': 'off',
        'LLM_CACHE_ENABLED': 'false'},
        'loaded_model_services': {'observation': {'bindings': [
            {'workload_id': 'ask_answer', 'status': 'bound', 'thinking_mode': 'disabled'},
            {'workload_id': 'ask_rewrite', 'status': 'bound', 'thinking_mode': 'provider_default'}]}}}


def test_runner_switches_stay_independent_and_modes_are_not_collapsed():
    saved = runtime()
    saved['overrides'].update(CHUNK_KG_OVERLAY_ENABLED='true',
        AGENT_PROFILE_ENABLED='true', GENERATED_QUESTION_INDEX_MODE='shadow')
    before = deepcopy(saved)
    actual = api().derive_feature_set(saved)
    assert actual == {
        'chunk_kg_overlay_enabled': True, 'kg_auto_extract': False,
        'agent_profile_enabled': True, 'user_search_profile_enabled': False,
        'retrieval_experience_enabled': False, 'retrieval_experience_inject_enabled': False,
        'reasoning_consult_memory_enabled': False, 'generated_question_index_mode': 'shadow',
        'llm_cache_enabled': False,
        'thinking_modes': {'ask_answer': 'disabled', 'ask_rewrite': 'provider_default'}}
    assert saved == before


def test_effective_settings_win_over_declared_overrides_and_have_provenance():
    saved = runtime()
    saved['effective_settings'] = {'llm_cache_enabled': True, 'kg_auto_extract': True,
                                  'generated_question_index_mode': 'on'}
    identity = api().build_feature_identity(saved)
    assert identity['feature_set']['llm_cache_enabled'] is True
    assert identity['feature_set']['kg_auto_extract'] is True
    assert identity['feature_set']['generated_question_index_mode'] == 'on'
    assert identity['sources']['llm_cache_enabled'] == 'effective_settings'
    assert identity['sources']['agent_profile_enabled'] == 'overrides'


def test_missing_facts_remain_unknown_even_when_some_effective_settings_exist():
    identity = api().build_feature_identity({'effective_settings': {'llm_cache_enabled': False}})
    features = identity['feature_set']
    assert features['llm_cache_enabled'] is False
    assert all(value is None for name, value in features.items() if name != 'llm_cache_enabled')
    assert identity['sources']['thinking_modes'] == 'unknown'
    assert api().derive_feature_set({'loaded_model_services': {'observation': {'bindings': []}}})['thinking_modes'] == {}


def test_fingerprint_ignores_unrelated_private_values_order_and_run_coordinates():
    a, b = runtime(), runtime()
    b['overrides'] = dict(reversed(list(b['overrides'].items())))
    a['overrides']['DATABASE_URL'] = 'private-database-a'
    b['overrides']['DATABASE_URL'] = 'private-database-b'
    b['effective_settings'] = {'api_key': 'private-key', 'base_url': 'private-address'}
    result = api().build_feature_identity(b)
    assert result['fingerprint'] == api().build_feature_identity(a)['fingerprint']
    assert 'private-' not in json.dumps(result)
    b['overrides']['KG_AUTO_EXTRACT'] = 'true'
    assert api().build_feature_identity(b)['fingerprint'] != result['fingerprint']


@pytest.mark.parametrize('mutate', [
    lambda x: x['overrides'].update(LLM_CACHE_ENABLED='secret-invalid-value'),
    lambda x: x.update(effective_settings={'llm_cache_enabled': 'false'}),
    lambda x: x['overrides'].update(GENERATED_QUESTION_INDEX_MODE='invalid'),
    lambda x: x.update(overrides=[]),
    lambda x: x['loaded_model_services']['observation']['bindings'][0].update(thinking_mode='invalid'),
    lambda x: x['loaded_model_services']['observation']['bindings'].append(
        {'workload_id': 'ask_answer', 'thinking_mode': 'enabled'}),
])
def test_malformed_known_observations_are_rejected_without_echoing_input(mutate):
    saved = runtime()
    mutate(saved)
    with pytest.raises(ValueError) as caught:
        api().build_feature_identity(saved)
    assert 'secret-invalid-value' not in str(caught.value)


def test_nested_effective_settings_and_unbound_thinking_are_preserved():
    saved = runtime()
    obs = saved['loaded_model_services']['observation']
    obs['effective_settings'] = {'reasoning_consult_memory_enabled': True}
    obs['bindings'][0].update(status='missing', thinking_mode='enabled')
    assert api().derive_feature_set(saved)['reasoning_consult_memory_enabled'] is True
    # A configured route without a bound service is not an active thinking mode.
    assert api().derive_feature_set(saved)['thinking_modes']['ask_answer'] is None


def test_feature_record_validation_rejects_tampering_even_if_rehashed():
    saved = api().build_feature_identity(runtime())
    assert api().validate_feature_identity(saved) == saved
    bad = deepcopy(saved)
    bad['feature_set']['llm_cache_enabled'] = True
    with pytest.raises(ValueError, match='fingerprint'):
        api().validate_feature_identity(bad)
    bad['feature_set']['llm_cache_enabled'] = 'false'
    bad['fingerprint'] = fingerprint({k: v for k, v in bad.items() if k != 'fingerprint'})
    with pytest.raises(ValueError):
        api().validate_feature_identity(bad)


def manifest():
    return {'identity': {'source': {'bundle_id': 'bundle-fixture', 'suite': 'qasper'},
        'code': {'head_revision': 'fixture-revision', 'source_manifest_sha256': 'a' * 64},
        'runtime_settings': 'b' * 64, 'product_services': 'c' * 64,
        'runtime_dependencies': {'environment_sha256': 'd' * 64, 'requirements_sha256': 'e' * 64}}}


def tiers(saved=None, observed=None, cases=None, scorer=None):
    return api().build_comparison_identity(manifest() if saved is None else saved,
        case_ids=['case-a', 'case-b'] if cases is None else cases,
        scorer_identity={'profile': 'qasper-official', 'revision': 'fixture'} if scorer is None else scorer,
        runtime_identity=runtime() if observed is None else observed)


def test_unrelated_dotenv_bytes_do_not_block_comparison_but_are_disclosed(tmp_path):
    manifests, runtimes = [manifest(), manifest()], [runtime(), runtime()]
    runtimes[0]['dotenv_snapshot'] = {'status': 'captured', 'sha256': '1' * 64}
    runtimes[1]['dotenv_snapshot'] = {'status': 'captured', 'sha256': '2' * 64}
    for index in range(2):
        run = tmp_path / f'run-{index}'
        run.mkdir()
        (run / 'manifest.json').write_text(json.dumps(manifests[index]))
        (run / 'runtime-identity.json').write_text(json.dumps(runtimes[index]))
    left, right = [tiers(json.loads((tmp_path / f'run-{i}' / 'manifest.json').read_text()),
                         json.loads((tmp_path / f'run-{i}' / 'runtime-identity.json').read_text()))
                   for i in range(2)]
    result = api().compare_identity_tiers(left, right)
    assert result['comparable'] is True
    assert result['must_equal_differences'] == {}
    assert set(result['disclosure_differences']) == {'configuration'}
    assert result['disclosure_differences']['configuration']['left']['dotenv_sha256'] == '1' * 64
    markdown = api().disclosure_markdown(result)
    assert '披露差异' in markdown and 'configuration' in markdown


@pytest.mark.parametrize('field', ['source', 'configuration', 'feature_set', 'dependencies'])
def test_disclosure_tier_changes_never_refuse_comparison(field):
    saved, observed = manifest(), runtime()
    if field == 'source':
        saved['identity']['code']['head_revision'] = 'other-revision'
    elif field == 'configuration':
        saved['identity']['runtime_settings'] = 'f' * 64
    elif field == 'feature_set':
        observed['overrides']['LLM_CACHE_ENABLED'] = 'true'
    else:
        saved['identity']['runtime_dependencies']['environment_sha256'] = 'f' * 64
    result = api().compare_identity_tiers(tiers(), tiers(saved, observed))
    assert result['comparable'] is True
    assert field in result['disclosure_differences']


@pytest.mark.parametrize('field', ['bundle', 'case_ids', 'scorer'])
def test_required_tier_changes_block_comparison(field):
    saved = manifest()
    if field == 'bundle':
        saved['identity']['source']['bundle_id'] = 'other'
    right = tiers(saved, cases=['case-a'] if field == 'case_ids' else None,
                  scorer={'profile': 'different'} if field == 'scorer' else None)
    result = api().compare_identity_tiers(tiers(), right)
    assert result['comparable'] is False
    assert field in result['must_equal_differences']


def test_case_set_order_is_not_a_different_set_and_duplicates_are_rejected():
    result = api().compare_identity_tiers(tiers(), tiers(cases=['case-b', 'case-a']))
    assert result['comparable'] is True
    with pytest.raises(ValueError, match='case'):
        tiers(cases=['case-a', 'case-a'])


def test_remote_generations_are_recorded_but_never_equated_or_diffed():
    saved = manifest()
    saved['identity']['runtime_model_services'] = {'observation_sha256': 'f' * 64}
    a, b = runtime(), runtime()
    for i, observed in enumerate([a, b]):
        observed['loaded_model_services']['observation']['services'] = [
            {'service_id': 'answer', 'generation': str(i + 1) * 64,
             'base_url': 'private-address', 'api_key': 'private-key'}]
    result = api().compare_identity_tiers(tiers(observed=a), tiers(saved, b))
    assert result['comparable'] is True
    assert result['disclosure_differences'] == {}
    assert result['unverifiable']['remote_models']['left'] != result['unverifiable']['remote_models']['right']
    assert result['unverifiable']['remote_models']['comparison'] == 'not_compared'
    assert 'private-' not in json.dumps(result)


def test_manifest_feature_set_is_recomputed_when_runtime_evidence_is_available():
    saved = manifest()
    saved['identity']['feature_set'] = api().build_feature_identity(runtime())
    saved['identity']['feature_set']['feature_set']['llm_cache_enabled'] = True
    with pytest.raises(ValueError):
        tiers(saved)


def test_legacy_missing_features_and_environment_are_disclosed_as_unknown():
    saved = manifest()
    saved['identity'].pop('runtime_dependencies')
    identity = api().build_comparison_identity(saved, case_ids=['case-a'], scorer_identity={'id': 'fixture'})
    assert identity['disclose']['feature_set'] is None
    assert identity['disclose']['dependencies'] is None
    assert identity['unverifiable']['remote_models'] is None


def test_unsafe_source_fields_are_hashed_and_never_emitted():
    saved = manifest()
    saved['identity']['code']['versions'] = {'private-package': '1'}
    saved['identity']['code']['private-extra'] = 'private-address'
    result = tiers(saved)
    assert 'private-address' not in json.dumps(result)


def test_explicit_effective_settings_can_be_replayed_from_the_saved_allowlist():
    observed = runtime()
    effective = {'llm_cache_enabled': True, 'api_key': 'private-key'}
    record = api().build_feature_identity(observed, effective_settings=effective)
    observed['effective_settings'] = {name: value for name, value in effective.items()
                                      if name in api().FEATURE_SETTING_FIELDS}
    assert api().build_feature_identity(observed) == record
    assert 'private-key' not in json.dumps(record)


def test_conflicting_effective_observations_are_not_silently_preferred():
    observed = runtime()
    observed['effective_settings'] = {'llm_cache_enabled': True}
    observed['loaded_model_services']['observation']['effective_settings'] = {'llm_cache_enabled': False}
    with pytest.raises(ValueError, match='Conflicting'):
        api().build_feature_identity(observed)


def test_permuting_workload_bindings_keeps_the_feature_fingerprint():
    a, b = runtime(), runtime()
    b['loaded_model_services']['observation']['bindings'].reverse()
    assert api().build_feature_identity(a)['fingerprint'] == api().build_feature_identity(b)['fingerprint']


@pytest.mark.parametrize('field,value', [
    ('bundle', ''), ('bundle', []), ('scorer', 'invalid'), ('scorer', None),
    ('case_ids', {}), ('case_ids', {'sha256': 'a' * 64, 'count': 0}),
])
def test_malformed_required_projection_never_produces_comparable(field, value):
    a, b = tiers(), tiers()
    a['must_equal'][field] = b['must_equal'][field] = value
    with pytest.raises(ValueError):
        api().compare_identity_tiers(a, b)


def test_resealed_feature_record_still_cannot_disagree_with_runtime():
    saved, observed = manifest(), runtime()
    false_claim = runtime()
    false_claim['overrides']['LLM_CACHE_ENABLED'] = 'true'
    saved['identity']['feature_set'] = api().build_feature_identity(false_claim)
    with pytest.raises(ValueError, match='differ'):
        tiers(saved, observed)


def test_effective_feature_change_cannot_be_hidden_by_matching_declared_overrides():
    a, b = runtime(), runtime()
    b['effective_settings'] = {'llm_cache_enabled': True}
    result = api().compare_identity_tiers(tiers(observed=a), tiers(observed=b))
    assert result['comparable'] is True
    assert result['disclosure_differences']['feature_set']['right']['feature_set']['llm_cache_enabled'] is True


def test_env_only_difference_survives_the_existing_formal_score_comparison(tmp_path):
    """Exercise the real offline admission core, not only our identity boolean."""
    from rag_eval.benchmark_comparison import compare_submissions
    from rag_eval.benchmark_official import prepare_inputs, scorer_identity
    from rag_eval.benchmark_submission import build_submission
    from rag_eval.notebook_bundle import prepare

    raw = {'meeting_transcripts': [{'speaker': 'A', 'content': 'Cats run.'}],
           'general_query_list': [{'query': 'Summarize.', 'answer': 'Cats run.'}],
           'specific_query_list': []}
    (tmp_path / 'raw.jsonl').write_text(json.dumps(raw) + '\n')
    (tmp_path / 'source.json').write_text(json.dumps({
        'dataset': 'QMSum', 'split': 'test', 'revision': 'fixture',
        'source_url': 'https://example.org/dataset-fixture', 'license': 'test'}))
    bundle = prepare('qmsum', tmp_path / 'raw.jsonl', tmp_path / 'source.json',
                     tmp_path / 'bundle', adaptation_revision='notebook-data-v3')
    entries, projected = [], []
    for index in range(2):
        saved, observed = manifest(), runtime()
        saved['identity']['source'] = bundle['manifest']
        observed['dotenv_snapshot'] = {'status': 'captured', 'sha256': str(index + 1) * 64}
        run = tmp_path / f'run-{index}'
        run.mkdir()
        (run / 'manifest.json').write_text(json.dumps(saved))
        (run / 'runtime-identity.json').write_text(json.dumps(observed))
        method = {'name': 'synthetic-sn', 'kind': 'sn', 'citation_style': 'sn',
                  'model_identity': {'model': 'offline-fixture'}, 'input_policy': 'frozen-source-documents',
                  'configuration': {'comparison_category': 'controlled-rerun',
                                    'identity': saved['identity'],
                                    'dotenv_sha256': observed['dotenv_snapshot']['sha256']}}
        sub = build_submission(bundle, method=method, predictions=[
            {'case_id': c['case_id'], 'status': 'success', 'prediction': 'Cats run.'}
            for c in bundle['cases']])
        prepared = prepare_inputs(bundle, sub)
        dependencies = {'bridge': {'fixture.py': 'a' * 64}, 'python': 'fixture', 'packages': {}}
        scorer = scorer_identity(prepared['profile'], dependencies)
        score = {'format': 'benchmark-scored-submission-v1',
                 **{key: deepcopy(sub[key]) for key in (
                     'bundle_id', 'suite', 'scope', 'case_ids', 'method', 'method_id', 'coverage')},
                 'submission_id': fingerprint(sub), 'prepared_id': fingerprint(prepared),
                 'profile': prepared['profile'], 'dependencies': dependencies,
                 'scorer_identity': scorer, 'scorer_id': fingerprint(scorer),
                 'metrics': {'rougeL': .5}, 'pending_metrics': [],
                 'per_case': [{'case_id': c['case_id'], 'group_id': c['group_id'], 'task': c['task'],
                               'status': 'success', 'metrics': {'rougeL': .5}} for c in bundle['cases']]}
        score['scores_id'] = fingerprint(score)
        (run / 'submission.json').write_text(json.dumps(sub))
        (run / 'scores.json').write_text(json.dumps(score))
        entries.append({'submission': run / 'submission.json', 'scores': run / 'scores.json'})
        projected.append(api().build_comparison_identity(saved, case_ids=sub['case_ids'],
                            scorer_identity=scorer, runtime_identity=observed))
    report = compare_submissions(tmp_path / 'bundle', entries)
    # Concrete coordinator seam: attach before report_id is computed there.
    report['identity_disclosure'] = api().compare_identity_tiers(*projected)
    assert report['identity_disclosure']['comparable'] is True
    assert set(report['identity_disclosure']['disclosure_differences']) == {'configuration'}
    assert report['comparisons'][0]['batch_differences'] == {'rougeL': 0}
    assert '披露差异' in api().disclosure_markdown(report['identity_disclosure'])


@pytest.mark.parametrize('change', ['mode', 'request_policy'])
def test_run_configuration_outside_dotenv_is_also_disclosed(change):
    saved = manifest()
    if change == 'mode':
        saved['mode'] = 'reasoning'
    else:
        saved['identity']['notebook_context'] = {'request_revision': 'different-fixture'}
    result = api().compare_identity_tiers(tiers(), tiers(saved))
    assert result['comparable'] is True
    assert 'configuration' in result['disclosure_differences']
