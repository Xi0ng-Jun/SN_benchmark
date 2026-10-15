"""Operational startup errors retain their cause before a run is owned."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('problem', ['missing-source', 'invalid-expectation'])
def test_captured_input_read_errors_retain_original_diagnostic(tmp_path, problem):
    source = tmp_path / 'source.json'
    expectation = tmp_path / 'expectation.json'
    if problem == 'invalid-expectation':
        source.write_text(json.dumps({'kind': 'sn', 'id': 'a' * 64, 'manifest_sha256': 'b' * 64}))
        expectation.write_text('{invalid')
    run = tmp_path / 'run'
    child = subprocess.run([sys.executable, str(ROOT / 'scripts/run_notebook_benchmarks.py'),
        '--bundle', str(tmp_path / 'bundle'), '--partition-id', 'p', '--mode', 'chunk',
        '--run-dir', str(run), '--project-root', str(tmp_path / 'sn'),
        '--artifact-root', str(tmp_path / 'store'), '--artifact-index-id', 'i' * 64,
        '--sn-source-ref', str(source), '--runtime-expectation', str(expectation),
        '--dependency-requirements', str(tmp_path / 'requirements.txt')], capture_output=True, text=True)
    assert child.returncode == 2
    assert 'diagnostic: ' in child.stderr
    path = Path(child.stderr.strip().split('diagnostic: ', 1)[1])
    detail = path.read_text()
    assert 'FileNotFoundError' in detail if problem == 'missing-source' else 'JSONDecodeError' in detail
    assert 'main' in detail and 'Failure phase: startup_inputs' in detail
    assert not run.exists()
