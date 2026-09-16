(function (root, factory) {
  const api = factory();
  root.ExplorerCore = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const FACETS = ['suite', 'task', 'track', 'mode', 'scorer', 'status', 'output_status', 'behavior', 'partition', 'run_id', 'config_family'];
  const GROUP_FIELDS = ['suite', 'track', 'scorer', 'config_family'];
  const DISPLAY_GROUP_FIELDS = [...GROUP_FIELDS, 'mode', 'task'];

  function text(value) {
    if (value == null) return '';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    try { return JSON.stringify(value); } catch (_) { return ''; }
  }

  function facetValue(entry, field) {
    const value = entry[field];
    if (field === 'behavior' && value && typeof value === 'object') return value.kind || value.status || '';
    return value == null || value === '' ? '未记录' : String(value);
  }

  function searchableText(entry) {
    if (entry.search_text) return `${entry.search_text} ${FACETS.map((field) => facetValue(entry, field)).join(' ')} ${text(entry.case_id)} ${text(entry.reason)} ${text(entry.plan)} ${text(entry.result)}`.toLocaleLowerCase();
    return `${FACETS.map((field) => facetValue(entry, field)).join(' ')} ${text(entry.case_id)} ${text(entry.reason)} ${text(entry.plan)} ${text(entry.result)}`.toLocaleLowerCase();
  }

  function filterEntries(entries, filters, query) {
    const needle = String(query || '').trim().toLocaleLowerCase();
    return (entries || []).filter((entry) => {
      for (const field of FACETS) {
        const selected = filters && Array.isArray(filters[field]) ? filters[field] : [];
        if (selected.length && !selected.includes(facetValue(entry, field))) return false;
      }
      return !needle || searchableText(entry).includes(needle);
    });
  }

  function indexSearchText(entries, observations) {
    const source = observations || {};
    return (entries || []).map((entry) => {
      const observation = source[entry.observation_id] || {};
      return {...entry, search_text: `${entry.search_text || ''} ${text(observation.case)} ${text(observation.output)} ${text(entry.case_id)} ${text(entry.reason)} ${text(entry.plan)} ${text(entry.result)}`};
    });
  }

  function facetOptions(entries, field) {
    const counts = new Map();
    for (const entry of entries || []) {
      const value = facetValue(entry, field);
      counts.set(value, (counts.get(value) || 0) + 1);
    }
    return [...counts].map(([value, count]) => ({value, count})).sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
  }

  function observationKey(entry) {
    return entry.observation_id || `${entry.run_key || entry.run_id || 'run'}\u0000${entry.case_id || entry.id || 'case'}`;
  }

  function summarize(entries) {
    const rows = entries || [];
    return {
      predictions: new Set(rows.map(observationKey)).size,
      scoringEntries: rows.length,
      scoredEntries: rows.filter(isValidScore).length,
      missingEntries: rows.filter((row) => !isValidScore(row)).length,
    };
  }

  function isValidScore(entry) {
    return entry && entry.status === 'scored' && typeof entry.score === 'number' && Number.isFinite(entry.score);
  }

  function groupScores(entries) {
    const groups = new Map();
    for (const row of entries || []) {
      const values = DISPLAY_GROUP_FIELDS.map((field) => field === 'config_family' && (row[field] == null || row[field] === '') ? `未记录@${row.run_key || row.run_id || row.id}` : facetValue(row, field));
      const key = values.join('\u0000');
      if (!groups.has(key)) {
        const group = Object.fromEntries(DISPLAY_GROUP_FIELDS.map((field, index) => [field, values[index]]));
        groups.set(key, {...group, planned: 0, valid: 0, missing: 0, total: 0, mean: null});
      }
      const group = groups.get(key);
      group.planned += 1;
      if (isValidScore(row)) { group.valid += 1; group.total += row.score; } else group.missing += 1;
    }
    return [...groups.values()].map((group) => ({...group, mean: group.valid ? group.total / group.valid : null}))
      .map(({total, ...group}) => group)
      .sort((a, b) => DISPLAY_GROUP_FIELDS.map((field) => a[field]).join('/').localeCompare(DISPLAY_GROUP_FIELDS.map((field) => b[field]).join('/')));
  }

  function scoreHistogram(entries, binCount) {
    const valid = (entries || []).filter(isValidScore);
    const families = [...new Set(valid.map((row) => facetValue(row, 'scorer')))].sort();
    if (families.length > 1) return {available: false, reason: 'mixed_metric_families', families};
    if (!valid.length) return {available: false, reason: 'no_valid_scores', families};
    const dimensions = [...new Set(valid.map((row) => GROUP_FIELDS.map((field) => field === 'config_family' && (row[field] == null || row[field] === '') ? `未记录@${row.run_key || row.run_id || row.id}` : facetValue(row, field)).join('\u0000')))];
    if (dimensions.length > 1) return {available: false, reason: 'incompatible_dimensions', families};
    const count = Math.max(1, Math.floor(binCount || 10));
    const values = valid.map((row) => row.score);
    let min = Infinity, max = -Infinity;
    for (const value of values) { if (value < min) min = value; if (value > max) max = value; }
    const lo = Math.min(0, min), hi = Math.max(1, max), width = (hi - lo) / count || 1;
    const bins = Array.from({length: count}, (_, index) => ({from: lo + width * index, to: lo + width * (index + 1), count: 0}));
    for (const value of values) bins[Math.min(count - 1, Math.max(0, Math.floor((value - lo) / width)))].count += 1;
    return {available: true, family: families[0], valid: values.length, min, max, bins};
  }

  function cohortInfo(entries) {
    if (!(entries || []).length) return {error: 'empty_cohort'};
    if (entries.some((row) => row.config_family == null || row.config_family === '')) return {error: 'missing_dimension'};
    const modes = new Set(entries.map((row) => facetValue(row, 'mode')));
    if (modes.size !== 1) return {error: 'multiple_modes'};
    const dimensions = new Set(entries.map((row) => GROUP_FIELDS.map((field) => facetValue(row, field)).join('\u0000')));
    if (dimensions.size !== 1) return {error: 'incompatible_dimensions'};
    const pairs = new Map();
    for (const row of entries) {
      if (!row.pairing_id || !row.case_id || !row.scorer) return {error: 'missing_identity'};
      const key = `${row.pairing_id}\u0000${row.case_id}\u0000${row.scorer}`;
      if (pairs.has(key)) return {error: 'duplicate_pair'};
      pairs.set(key, row);
    }
    return {mode: [...modes][0], dimension: [...dimensions][0], pairs, runs: new Set(entries.map((row) => row.run_key || row.run_id).filter(Boolean))};
  }

  function compareCohorts(aEntries, bEntries) {
    const a = cohortInfo(aEntries), b = cohortInfo(bEntries);
    if (a.error) return {ok: false, code: a.error, cohort: 'reference'};
    if (b.error) return {ok: false, code: b.error, cohort: 'comparison'};
    if (a.dimension !== b.dimension) return {ok: false, code: 'incompatible_dimensions'};
    if (a.mode === b.mode) return {ok: false, code: 'same_mode'};
    if ([...a.runs].some((run) => b.runs.has(run))) return {ok: false, code: 'overlapping_runs'};
    const commonKeys = [...a.pairs.keys()].filter((key) => b.pairs.has(key));
    if (!commonKeys.length) return {ok: false, code: 'no_common_pairs'};
    let commonValid = 0, partialPairs = 0, missingBoth = 0, deltaTotal = 0, referenceTotal = 0, comparisonTotal = 0, wins = 0, ties = 0, losses = 0;
    for (const key of commonKeys) {
      const left = a.pairs.get(key), right = b.pairs.get(key);
      const leftValid = isValidScore(left), rightValid = isValidScore(right);
      if (leftValid && rightValid) {
        const delta = right.score - left.score;
        commonValid += 1; deltaTotal += delta; referenceTotal += left.score; comparisonTotal += right.score;
        if (Math.abs(delta) <= 1e-12) ties += 1;
        else if (delta > 0) wins += 1;
        else losses += 1;
      } else if (leftValid || rightValid) partialPairs += 1;
      else missingBoth += 1;
    }
    return {
      ok: true, referenceMode: a.mode, comparisonMode: b.mode,
      commonPlanned: commonKeys.length, commonValid, partialPairs, missingBoth,
      onlyA: a.pairs.size - commonKeys.length, onlyB: b.pairs.size - commonKeys.length,
      referenceMean: commonValid ? referenceTotal / commonValid : null,
      comparisonMean: commonValid ? comparisonTotal / commonValid : null,
      meanDelta: commonValid ? deltaTotal / commonValid : null, wins, ties, losses,
    };
  }

  return {FACETS, GROUP_FIELDS, DISPLAY_GROUP_FIELDS, facetValue, facetOptions, filterEntries, indexSearchText, summarize, groupScores, scoreHistogram, compareCohorts};
});
