#!/usr/bin/env python3
"""Score exact saved SN component samples. Calls only the explicit judge, never SN Ask."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True, help='Native run containing agent/components.jsonl')
    parser.add_argument('--output', type=Path, required=True, help='New scoring batch; source stays read-only')
    parser.add_argument('--sample-id', dest='sample_ids', action='append', required=True)
    parser.add_argument('--metric', dest='metrics', action='append', required=True,
                        choices=('contextual_relevancy', 'faithfulness', 'answer_relevancy'))
    parser.add_argument('--judge-config', type=Path, required=True)
    parser.add_argument('--project-root', type=Path, required=True, help='SN checkout used only for its model client')
    parser.add_argument('--task-timeout', type=float, required=True,
                        help='Total SDK budget for one sample/metric; separate from judge request timeout')
    args = parser.parse_args(argv)
    os.environ.update(DEEPEVAL_TELEMETRY_OPT_OUT='YES', DEEPEVAL_DISABLE_DOTENV='1',
                      DEEPEVAL_NO_INSPECT_PROMPT='1', CONFIDENT_TRACE_FLUSH='0')
    os.environ.pop('CONFIDENT_API_KEY', None)
    from rag_eval.native_sdk import configure_local_sdk
    from rag_eval.native_component_scoring import rescore_components
    from rag_eval.starter_runtime import resolve_models, configure_environment, make_adapter
    from rag_eval.artifacts import save_json, digest

    source, output, project = args.source_run.resolve(), args.output.resolve(), args.project_root.resolve()
    for forbidden in (ROOT / 'src', ROOT / 'scripts', project):
        if output.is_relative_to(forbidden) or forbidden.is_relative_to(output):
            parser.error('Output must be separate from code/product')
    def judge_factory(sink):
        # No Repository, dotenv, notebook, import/indexing or Ask is constructed.
        settings, _ = configure_environment(project, output, product_track=False)
        save_json(output / 'judge-client-source.json', {
            name: digest(project / name) for name in ('backend/app/core/llm.py', 'backend/app/core/config.py')})
        return make_adapter(resolved['judge'], 'judge', settings, sink)
    try:
        configure_local_sdk(task_timeout=args.task_timeout)
        resolved, public = resolve_models(args.judge_config.resolve(), ['judge'])
        result = rescore_components(source, output, sample_ids=args.sample_ids, metrics=args.metrics,
                                   judge_factory=judge_factory, judge_identity=public['judge'])
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print('Component scoring failed: ' + type(exc).__name__ + '; inspect input selection and saved artifacts', file=sys.stderr)
        return 2
    print(output)
    return 0 if result['status'] == 'finished' else 2


if __name__ == '__main__':
    raise SystemExit(main())
