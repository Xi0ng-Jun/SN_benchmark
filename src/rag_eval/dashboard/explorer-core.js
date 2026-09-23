(function (root, factory) {
  const api = factory();
  root.SNExplorerCore = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const VIEWS = ['map', 'replay', 'analysis'];
  const STEPS = ['source', 'normalize', 'partition', 'import', 'retrieve', 'synthesize', 'answer', 'score'];
  const HASH_KEYS = ['view', 'run', 'case', 'step', 'span'];
  const FACETS = ['suite', 'task', 'track', 'mode', 'scorer', 'metric_name', 'scope', 'status', 'output_status', 'config_family', 'run_id'];

  function parseHash(hash) {
    const source = String(hash || '').replace(/^#/, '');
    const params = new URLSearchParams(source);
    return Object.fromEntries(HASH_KEYS.flatMap((key) => params.has(key) ? [[key, params.get(key)]] : []));
  }

  function formatHash(state) {
    const params = new URLSearchParams();
    HASH_KEYS.forEach((key) => {
      if (state && state[key] != null && state[key] !== '') params.set(key, String(state[key]));
    });
    const value = params.toString();
    return value ? `#${value}` : '';
  }

  function runKey(run) {
    return run && (run.key || run.run_key || run.id);
  }

  function observationList(data) {
    return Object.entries((data && data.observations) || {}).map(([key, value]) => ({id: key, ...value}));
  }

  function normalizeHashState(candidate, data, detail) {
    const invalid = [];
    const runs = (data && data.runs) || [];
    const allObservations = observationList(data);
    const requestedRun = candidate && candidate.run;
    const selectedRun = runs.find((run) => runKey(run) === requestedRun) || runs[0] || null;
    if (requestedRun && (!selectedRun || runKey(selectedRun) !== requestedRun)) invalid.push('run');
    const selectedRunKey = runKey(selectedRun) || null;
    const available = allObservations.filter((observation) => !selectedRunKey || observation.run_key === selectedRunKey);
    const requestedCase = candidate && candidate.case;
    const selectedCase = available.find((observation) => observation.id === requestedCase || observation.case_id === requestedCase) || available[0] || null;
    if (requestedCase && (!selectedCase || (selectedCase.id !== requestedCase && selectedCase.case_id !== requestedCase))) invalid.push('case');
    const view = VIEWS.includes(candidate && candidate.view) ? candidate.view : 'map';
    if (candidate && candidate.view && view !== candidate.view) invalid.push('view');
    let step = candidate && candidate.step || null;
    if (step && !STEPS.includes(step)) { invalid.push('step'); step = null; }
    if (step && detail && Array.isArray(detail.steps) && !detail.steps.some((item) => item.id === step)) { invalid.push('step'); step = null; }
    let span = candidate && candidate.span || null;
    if (span && (!selectedCase || invalid.includes('case'))) { invalid.push('span'); span = null; }
    if (span && detail) {
      const nodes = detail ? flattenTraceTree(selectDisplayTraces((detail.native || {}).traces || [])) : [];
      if (!nodes.some((node) => node.id === span)) { invalid.push('span'); span = null; }
    }
    return {state: {view, run: selectedRunKey, case: selectedCase && selectedCase.id || null, step, span}, invalid};
  }

  function facetValue(entry, field) {
    const value = entry && entry[field];
    if (value == null || value === '') return '未记录';
    if (typeof value === 'object') return stableString(value);
    return String(value);
  }

  function searchable(entry) {
    return FACETS.concat(['case_id', 'reason']).map((field) => facetValue(entry, field)).join(' ').toLocaleLowerCase();
  }

  function filterEntries(entries, filters, query) {
    const needle = String(query || '').trim().toLocaleLowerCase();
    return (entries || []).filter((entry) => {
      for (const [field, selected] of Object.entries(filters || {})) {
        if (Array.isArray(selected) && selected.length && !selected.includes(facetValue(entry, field))) return false;
      }
      return !needle || searchable(entry).includes(needle);
    });
  }

  function facetOptions(entries, field, filters, query) {
    const otherFilters = {...(filters || {}), [field]: []};
    const counts = new Map();
    filterEntries(entries, otherFilters, query).forEach((entry) => {
      const value = facetValue(entry, field);
      counts.set(value, (counts.get(value) || 0) + 1);
    });
    for (const value of (filters && filters[field]) || []) if (!counts.has(value)) counts.set(value, 0);
    return [...counts].map(([value, count]) => ({value, count})).sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
  }

  function isValidScore(entry) {
    return entry && entry.status === 'scored' && typeof entry.score === 'number' && Number.isFinite(entry.score);
  }

  function summarizeEntries(entries) {
    const rows = entries || [];
    const valid = rows.filter(isValidScore);
    return {
      observations: new Set(rows.map((entry) => entry.observation_id).filter(Boolean)).size,
      scoringEntries: rows.length,
      validScores: valid.length,
      missingScores: rows.length - valid.length,
      mean: valid.length ? valid.reduce((total, entry) => total + entry.score, 0) / valid.length : null,
    };
  }

  function stableString(value) {
    if (value == null || typeof value !== 'object') return String(value == null ? '' : value);
    if (Array.isArray(value)) return `[${value.map(stableString).join(',')}]`;
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableString(value[key])}`).join(',')}}`;
  }

  function comparisonIdentity(entry) {
    return [entry.source_id || entry.pairing_id || '', entry.question_id || entry.case_id || '', entry.corpus_id || '', entry.case_id || ''].join('\\u0000');
  }

  function pairEntries(reference, comparison) {
    const left = reference || [], right = comparison || [];
    if (!left.length || !right.length) return {ok: false, code: 'empty_group'};
    const unique = (rows, field) => new Set(rows.map((row) => field === 'judge' ? stableString(row[field]) : facetValue(row, field)));
    const sameSingle = (field) => {
      const a = unique(left, field), b = unique(right, field);
      return a.size === 1 && b.size === 1 && [...a][0] === [...b][0];
    };
    const explicitSystemComparison = left.concat(right).every((row) => row.configured_system === true || (row.product_protocol && row.source_id && row.question_id && row.corpus_id));
    for (const field of ['suite', 'task', 'track']) if (!sameSingle(field)) return {ok: false, code: `different_${field}`};
    if (!sameSingle('config_family') && !explicitSystemComparison) return {ok: false, code: 'different_configuration'};
    const sourceValues = (rows) => new Set(rows.map((row) => row.source_id || row.pairing_id || ''));
    if (![...sourceValues(left)].some((value) => value && sourceValues(right).has(value))) return {ok: false, code: 'different_source'};
    const corpusValues = (rows) => new Set(rows.map((row) => row.corpus_id || '').filter(Boolean));
    if ([...corpusValues(left)].some((value) => !corpusValues(right).has(value))) return {ok: false, code: 'different_corpus'};
    if (!sameSingle('scorer') || !sameSingle('metric_name') || !sameSingle('scope')) return {ok: false, code: 'different_metric'};
    if (!sameSingle('judge')) return {ok: false, code: 'different_judge'};
    const protocols = new Set(left.map((row) => row.evaluation_protocol).filter(Boolean));
    if ([...protocols].some((protocol) => right.some((row) => row.evaluation_protocol && row.evaluation_protocol !== protocol))) return {ok: false, code: 'different_protocol'};
    if (left.some((row) => ['retrieval', 'synthesis', 'trajectory'].includes(row.scope)) || right.some((row) => ['retrieval', 'synthesis', 'trajectory'].includes(row.scope))) {
      return {ok: false, code: 'component_distribution_only', distribution: {reference: left.length, comparison: right.length}};
    }
    const collect = (rows) => {
      const map = new Map();
      for (const entry of rows) {
        const key = comparisonIdentity(entry);
        if (map.has(key)) return {error: 'duplicate_question'};
        map.set(key, entry);
      }
      return {map};
    };
    const a = collect(left), b = collect(right);
    if (a.error || b.error) return {ok: false, code: 'duplicate_question'};
    const common = [...a.map.keys()].filter((key) => b.map.has(key));
    if (!common.length) return {ok: false, code: 'no_common_questions'};
    const pairs = common.map((key) => {
      const l = a.map.get(key), r = b.map.get(key);
      const lValid = isValidScore(l), rValid = isValidScore(r);
      return {
        key, case_id: l.case_id,
        reference: l, comparison: r,
        delta: lValid && rValid ? r.score - l.score : null,
        state: lValid && rValid ? 'scored' : lValid || rValid ? 'partial' : 'missing',
      };
    });
    return {ok: true, pairs, onlyReference: a.map.size - common.length, onlyComparison: b.map.size - common.length};
  }

  function selectDisplayTraces(traces) {
    const rows = traces || [];
    const grouped = new Map();
    rows.forEach((trace, index) => {
      const key = trace.request_id || `trace-${index}`;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(trace);
    });
    return [...grouped.values()].map((group) => group.find((trace) => trace.selected_for_display === true) || group[group.length - 1]);
  }

  function spanIdentity(span, fallback) {
    return span && (span.span_id || span.uuid || span.id || span.name) || fallback;
  }

  function flattenTraceTree(traces) {
    const output = [];
    (traces || []).forEach((trace, traceIndex) => {
      const tree = trace.trace || trace;
      const roots = tree.root_spans || tree.spans || [];
      const walk = (span, parentId, depth, index) => {
        const id = String(spanIdentity(span, `${trace.request_id || traceIndex}:${parentId || 'root'}:${index}`));
        output.push({id, parentId, depth, request_id: trace.request_id || span.request_id || null, span, trace});
        (span.children || span.child_spans || []).forEach((child, childIndex) => walk(child, id, depth + 1, childIndex));
      };
      roots.forEach((span, index) => walk(span, null, 0, index));
    });
    return output;
  }

  function entriesForSpan(entries, identity) {
    if (!identity) return [];
    return (entries || []).filter((entry) => {
      if (identity.request_id && entry.request_id && identity.request_id !== entry.request_id) return false;
      if (identity.request_id && !identity.span_id && !identity.sample_id) return entry.request_id === identity.request_id;
      return Boolean((identity.span_id && entry.span_id === identity.span_id) || (identity.sample_id && entry.sample_id === identity.sample_id));
    });
  }

  function diffConfig(reference, comparison) {
    const missing = Symbol('missing');
    const rows = [];
    const walk = (left, right, path) => {
      if (stableString(left === missing ? undefined : left) === stableString(right === missing ? undefined : right) && left !== missing && right !== missing) return;
      const leftObject = left && typeof left === 'object' && !Array.isArray(left);
      const rightObject = right && typeof right === 'object' && !Array.isArray(right);
      if (leftObject && rightObject) {
        [...new Set([...Object.keys(left), ...Object.keys(right)])].sort().forEach((key) => walk(
          Object.prototype.hasOwnProperty.call(left, key) ? left[key] : missing,
          Object.prototype.hasOwnProperty.call(right, key) ? right[key] : missing,
          path ? `${path}.${key}` : key,
        ));
        return;
      }
      rows.push({path, reference: left === missing ? undefined : left, comparison: right === missing ? undefined : right});
    };
    walk(reference || {}, comparison || {}, '');
    return rows;
  }

  return {
    VIEWS, STEPS, HASH_KEYS, FACETS, parseHash, formatHash, normalizeHashState,
    facetValue, filterEntries, facetOptions, summarizeEntries, isValidScore,
    pairEntries, selectDisplayTraces, flattenTraceTree, entriesForSpan, diffConfig,
  };
});
