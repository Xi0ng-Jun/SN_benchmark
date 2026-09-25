"""QMSum author-confirmed Perl ROUGE invocation, without model/download code.

The QMSum author linked MatchSum's metrics.py in QMSum issue #5. Its flags
are -c 95 -r 1000 -n 2 -m -a; -d below only enables per-case diagnostics.
Source: maszhongming/MatchSum@c7754245a454d0ba3535db0e4cc1a13b3d35680d.

The original QMSum sentence preparation is not specified there. ``newline``
preserves supplied sentence boundaries; ``hmnet_regex`` explicitly uses the
rule in microsoft/HMNet@416966c63e3cb7a57dc59b4ce8fa76f11fd048be. Neither
option alone constitutes reproduction of the original experiment.

ROUGE's native SPL input reads the same sentence list as pyrouge's SEE HTML,
while preserving '<' characters instead of truncating the rest of a sentence.
ROUGE performs its own ASCII lowercasing and Porter stemming. No additional
case conversion, word tokenization, citation stripping or truncation occurs.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from xml.etree import ElementTree as ET


AUTHOR_SOURCE = 'https://github.com/Yale-LILY/QMSum/issues/5#issuecomment-890003212'
AUTHOR_FLAGS = ['-c', '95', '-r', '1000', '-n', '2', '-m']
HMNET_SENTENCE_PATTERN = r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?)\s'
METRICS = {'1': 'rouge1', '2': 'rouge2', 'L': 'rougeL'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _file_identity(path):
    data = path.read_bytes()
    return {'sha256': _sha(data), 'bytes': len(data)}


def _dependencies(home):
    for relative in ('ROUGE-1.5.5.pl', 'data/smart_common_words.txt', 'data/WordNet-2.0.exc.db'):
        if not (home / relative).is_file():
            raise FileNotFoundError(f'ROUGE-1.5.5 dependency missing: {home / relative}; '
                                    'provide a complete local ROUGE distribution (no automatic download).')
    perl = shutil.which('perl')
    if not perl:
        raise FileNotFoundError('Perl is required to execute ROUGE-1.5.5.')
    files = {p.relative_to(home).as_posix(): _file_identity(p)
             for p in sorted(home.rglob('*')) if p.is_file()}
    version = subprocess.run([perl, '-e', 'print $^V'], text=True, capture_output=True, check=True)
    # PERL5LIB can provide an isolated XML::Parser installation; record it too.
    perl_libraries = {}
    for name in os.environ.get('PERL5LIB', '').split(os.pathsep):
        if name and Path(name).is_dir():
            directory = Path(name).resolve()
            perl_libraries[str(directory)] = {
                p.relative_to(directory).as_posix(): _file_identity(p)
                for p in sorted(directory.rglob('*')) if p.is_file()}
    return {'rouge_home': str(home), 'files': files, 'files_sha256': _sha(_json(files).encode()),
            'perl_path': perl, 'perl_version': version.stdout, 'perl_binary': _file_identity(Path(perl)),
            'perl5lib': perl_libraries}


def _sentences(text, segmentation):
    if segmentation == 'newline':
        return text.splitlines()
    if segmentation == 'hmnet_regex':
        return re.split(HMNET_SENTENCE_PATTERN, text)
    raise ValueError('segmentation must be newline or hmnet_regex; no implicit sentence tokenizer')


def _score(value):
    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(f'Invalid ROUGE score: {value}') from exc
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError(f'ROUGE score outside finite [0,1]: {value}')
    return result


def _parse(stdout, pairs):
    metrics, intervals = {}, {}
    per_case = [{'case_id': p['case_id'], 'metrics': {}} for p in pairs]
    aggregate = re.compile(r'^1 ROUGE-(1|2|L) Average_F:\s+(\S+)'
                           r'(?:\s+\(95%-conf.int. (\S+) - (\S+)\))?$', re.M)
    for match in aggregate.finditer(stdout):
        key = METRICS[match[1]]
        if key in metrics:
            raise ValueError(f'Duplicate aggregate ROUGE metric {key}')
        metrics[key] = _score(match[2])
        if match[3] is not None:
            low, high = _score(match[3]), _score(match[4])
            if low > high:
                raise ValueError('Reversed ROUGE confidence interval')
            intervals[key] = [low, high]
    if set(metrics) != set(METRICS.values()):
        raise ValueError('ROUGE output missing complete ROUGE-1/2/L Average_F results')
    detail = re.compile(r'^1 ROUGE-(1|2|L) Eval (\d+)(?:\.1)? R:(\S+) P:(\S+) F:(\S+)\s*$', re.M)
    for match in detail.finditer(stdout):
        index, key = int(match[2]) - 1, METRICS[match[1]]
        if not 0 <= index < len(pairs) or key in per_case[index]['metrics']:
            raise ValueError('ROUGE per-case identity is out of range or duplicated')
        _score(match[3])
        _score(match[4])
        per_case[index]['metrics'][key] = _score(match[5])
    if any(set(p['metrics']) != set(METRICS.values()) for p in per_case):
        raise ValueError('ROUGE -d output missing per-case values; cannot verify scoring denominator')
    return metrics, intervals, per_case


def score_qmsum_rouge(pairs, *, rouge_home, output_dir, segmentation='newline'):
    """Score a complete ordered submission and save reproducible local artifacts.

    All values are on [0,1]. Missing cases must be resolved by the caller before
    invocation. Empty predictions remain explicit rows. Raises on invalid data,
    dependencies, process failure, malformed output, or an existing output_dir.
    No automatic downloads or retries occur. The output directory is retained
    on process/scoring failure so stderr and the exact inputs remain reviewable.
    """
    if not isinstance(pairs, (list, tuple)) or not pairs:
        raise ValueError('pairs must be a nonempty sequence')
    copied, seen = [], set()
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ValueError('each pair must contain case_id, prediction and reference')
        case_id = pair.get('case_id')
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ValueError('case_id must be unique nonempty text')
        if any(not isinstance(pair.get(k), str) for k in ('prediction', 'reference')):
            raise ValueError('prediction and reference must be strings (prediction may be empty)')
        if not pair['reference'].strip():
            raise ValueError('reference must be nonempty text')
        seen.add(case_id)
        copied.append({k: pair[k] for k in ('case_id', 'prediction', 'reference')})
    prepared = [{**p, 'prediction': '\n'.join(_sentences(p['prediction'], segmentation)),
                 'reference': '\n'.join(_sentences(p['reference'], segmentation))} for p in copied]
    home, destination = Path(rouge_home).resolve(), Path(output_dir).absolute()
    if destination.exists():
        raise FileExistsError(f'ROUGE output directory must be fresh: {destination}')
    dependencies = _dependencies(home)
    destination.mkdir(parents=True, exist_ok=False)
    for subdir in ('system', 'reference'):
        (destination / subdir).mkdir()
    input_bytes = (_json(copied) + '\n').encode('utf-8')
    (destination / 'inputs.json').write_bytes(input_bytes)
    configuration = ET.Element('ROUGE-EVAL', version='1.55')
    for index, pair in enumerate(prepared, start=1):
        filename = f'{index:06d}.txt'
        for directory, field in [('system', 'prediction'), ('reference', 'reference')]:
            (destination / directory / filename).write_text(pair[field] + '\n', encoding='utf-8')
        evaluation = ET.SubElement(configuration, 'EVAL', ID=str(index))
        ET.SubElement(evaluation, 'PEER-ROOT').text = str(destination / 'system')
        ET.SubElement(evaluation, 'MODEL-ROOT').text = str(destination / 'reference')
        ET.SubElement(evaluation, 'INPUT-FORMAT', TYPE='SPL')
        ET.SubElement(ET.SubElement(evaluation, 'PEERS'), 'P', ID='1').text = filename
        ET.SubElement(ET.SubElement(evaluation, 'MODELS'), 'M', ID='A').text = filename
    settings = destination / 'settings.xml'
    ET.ElementTree(configuration).write(settings, encoding='utf-8', xml_declaration=True)
    command = [dependencies['perl_path'], '-I', str(home), str(home / 'ROUGE-1.5.5.pl'),
               '-e', str(home / 'data'), *AUTHOR_FLAGS, '-d', '-a', str(settings)]
    metadata = {'scorer': 'qmsum-author-confirmed-rouge155-v1', 'author_source': AUTHOR_SOURCE,
                'implementation_sha256': _file_identity(Path(__file__))['sha256'],
                'command': command, 'segmentation': segmentation, 'input_format': 'SPL',
                'sentence_pattern': HMNET_SENTENCE_PATTERN if segmentation == 'hmnet_regex' else None,
                'pair_count': len(copied), 'case_ids': [p['case_id'] for p in copied],
                'empty_prediction_count': sum(not p['prediction'].strip() for p in copied),
                'input_sha256': _sha(input_bytes), 'prepared_sha256': _sha(_json(prepared).encode()),
                'dependencies': dependencies}
    (destination / 'manifest.json').write_text(_json(metadata) + '\n', encoding='utf-8')
    try:
        process = subprocess.run(command, text=True, capture_output=True, encoding='utf-8', timeout=600)
    except subprocess.TimeoutExpired as exc:
        for name, content in [('stdout', exc.stdout), ('stderr', exc.stderr)]:
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='replace')
            (destination / f'{name}.txt').write_text(content or '', encoding='utf-8')
        raise RuntimeError('ROUGE process exceeded 600 seconds; partial output retained') from exc
    (destination / 'stdout.txt').write_text(process.stdout, encoding='utf-8')
    (destination / 'stderr.txt').write_text(process.stderr, encoding='utf-8')
    if process.returncode:
        raise RuntimeError(f'ROUGE-1.5.5 failed ({process.returncode}): {process.stderr.strip()}; '
                           'install required Perl XML::DOM/XML::Parser and DB_File locally, '
                           'or set PERL5LIB to an isolated dependency directory.')
    metrics, intervals, per_case = _parse(process.stdout, copied)
    result = {**metadata, 'metrics': metrics, 'confidence_intervals_95': intervals, 'per_case': per_case,
              'stdout': process.stdout, 'stderr': process.stderr, 'returncode': process.returncode}
    (destination / 'result.json').write_text(_json(result) + '\n', encoding='utf-8')
    return result
