"""Synthetic saved artifacts only: no SN runtime or model calls."""
import json, resource, sys, tempfile, time, tracemalloc
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src')]
from rag_eval.notebook_bundle import prepare
from rag_eval.bundle_index import install_bundle, load_partition
from rag_eval.artifact_store import write_run_refs, reference_identity
from rag_eval.artifacts import save_json, save_jsonl, digest
from rag_eval.identity import fingerprint
from rag_eval.notebook_runner import plan_rows
from rag_eval.notebook_data import VERSION
from rag_eval.benchmark_submission import export_sn_runs
from rag_eval.run_reader import RunReadContext, load_run
from rag_eval.result_package import package_campaign, validate_package, inventory

count = int(sys.argv[1])
with tempfile.TemporaryDirectory(prefix=f'sn-synthetic-{count}-') as temporary:
    base = Path(temporary); campaign=base/'campaign'; campaign.mkdir()
    raw=[dict(meeting_id='synthetic',meeting_transcripts=[dict(speaker='S',content='Meeting material. '+('A'*4096))],
              general_query_list=[dict(query=f'Summary {i}?',answer='Gold summary') for i in range(count)],specific_query_list=[])]
    (base/'raw.jsonl').write_text(json.dumps(raw[0])+'\n')
    (base/'source.json').write_text(json.dumps(dict(dataset='qmsum',split='test',revision='synthetic',source_url='https://example.org/synthetic',license='fixture')))
    started=time.perf_counter()
    bundle=prepare('qmsum',base/'raw.jsonl',base/'source.json',base/'bundle',adaptation_revision='notebook-data-v3')
    refs=install_bundle(base/'bundle',campaign/'artifacts')
    part=bundle['partitions'][0]['partition_id']; product=load_partition(campaign/'artifacts',refs,part,'notebook-request-v3')['product']
    run=campaign/'runs/r'; run.mkdir(parents=True); write_run_refs(run,campaign/'artifacts',refs)
    identity=dict(source=bundle['manifest'],product_bundle=product['manifest'],notebook_context=dict(partition_id=part,
        selected_cases=bundle['manifest']['selected_cases'],partition_count=bundle['manifest']['partition_count'],request_revision='notebook-request-v3'),
        code={'revision':'synthetic-no-code'},models={},runtime_settings='synthetic',product_services='synthetic',track='R')
    protocol=fingerprint(dict(identity,mode='chunk')); planned=plan_rows(bundle['cases'],'r',protocol,'chunk')
    save_jsonl(run/'planned.jsonl',planned);save_jsonl(run/'scores.jsonl',[])
    # Extra context is intentional, authoritative observations remain full.
    outputs=[dict(case_id=c['case_id'],sample_id=c['sample_id'],suite='qmsum',task=c['task'],product_protocol=VERSION,
        material_role='source_documents',status='success',output_available=True,prediction='Observed summary',
        product_record=dict(status='success',answer='Observed summary',context='X'*4096)) for c in bundle['cases']]
    save_jsonl(run/'outputs.jsonl',outputs);save_json(run/'product-bundle.json',product)
    save_json(run/'manifest.json',dict(**reference_identity(run),format='public-starter-run-v1',run_id='r',suite='qmsum',track='R',mode='chunk',
        product_protocol=VERSION,protocol_id=protocol,pairing_id=fingerprint(identity),identity=identity,source_manifest=bundle['manifest'],
        release_gate=False,planned_sha256=digest(run/'planned.jsonl'),planned_predictions=count,planned_scores=len(planned)))
    save_json(run/'state.json',dict(phase='finished'));setup=time.perf_counter()-started
    # Inventory excludes payload reads. Add private fake runtime to verify exclusion.
    (run/'runtime').mkdir(); (run/'runtime/database.db').write_bytes(b'R'*(8*1024*1024))
    # Actual public export uses fresh canonical context; instrument rebuild and read bytes.
    rebuilds=[]; reads=[0]; original_bundle=RunReadContext.bundle; original_open=Path.open
    def tracked_bundle(self, directory):
        result=original_bundle(self,directory); rebuilds.append(self.stats['canonical_rebuilds']); return result
    def tracked_open(self,*a,**kw):
        mode=a[0] if a else kw.get('mode','r')
        if 'r' in mode and self.is_file(): reads[0]+=self.stat().st_size
        return original_open(self,*a,**kw)
    RunReadContext.bundle=tracked_bundle; Path.open=tracked_open
    tracemalloc.start(); start=time.perf_counter()
    submission=export_sn_runs(base/'bundle',[run],campaign/'submissions/sn')
    export=time.perf_counter()-start; _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    RunReadContext.bundle=original_bundle;Path.open=original_open
    start=time.perf_counter();results=package_campaign(campaign,base/'results.tar.gz',mode='results');results_seconds=time.perf_counter()-start
    start=time.perf_counter();review=package_campaign(campaign,base/'review',mode='review');review_seconds=time.perf_counter()-start
    validate_package(base/'review'); reader=RunReadContext(); loaded=load_run(base/'review/runs/r',context=reader)
    assert len(loaded['outputs'])==count
    assert not (base/'review/runs/r/runtime').exists()
    print(json.dumps(dict(cases=count,setup_seconds=setup,export_seconds=export,export_peak_traced_bytes=peak,
        export_file_open_logical_bytes=reads[0],canonical_rebuilds=max(rebuilds),submission_bytes=submission.stat().st_size,
        full_outputs_bytes=(run/'outputs.jsonl').stat().st_size,campaign=inventory(campaign),results_package_bytes=results['package_bytes'],
        results_seconds=results_seconds,review_package_bytes=review['package_bytes'],review_seconds=review_seconds,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,relocated_readback_cases=len(loaded['outputs']))))
