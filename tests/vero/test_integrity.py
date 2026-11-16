"""Integrity verdicts over GEPA logs, v4.1 state and ``cli eval`` JSON (pure, offline)."""
from __future__ import annotations

import pytest

from rag_eval.vero.integrity import gepa_report, gepa_verdict, parse_metrics, probe_verdict, run_verdict


def metric_line(samples=115, errors=0, score=0.8) -> str:
    return ("INFO vero.evaluator: Task completed with metrics: "
            f"{{'num_samples': {samples}, 'num_errors': {errors}, 'avg_score': {score}, "
            f"'avg_filled_score': {score}, 'num_successes': {samples - errors}}}")


def metric(samples=115, errors=0, score=0.8) -> dict:
    return dict(num_samples=samples, num_errors=errors, avg_score=score,
                avg_filled_score=score, num_successes=samples - errors)


def gepa_log(seed="0.640", *, final_samples=115, final_errors=0, final_score=0.84,
             evaluations=3) -> str:
    lines = [f"INFO gepa: Initial test score: {seed}",
             "INFO gepa: optimizing the agent"]
    for n in range(evaluations):
        last = n == evaluations - 1
        lines.append(metric_line(samples=final_samples if last else 115,
                                 errors=final_errors if last else 0,
                                 score=final_score if last else 0.80 + 0.01 * n))
    return "\n".join(lines) + "\n"


def test_parse_metrics_in_order_with_none_and_failures():
    text = "\n".join(["INFO noise: Task completed with metrics: not really",
                      metric_line(),
                      "INFO vero.evaluator: Task completed with metrics: None",
                      "INFO vero.evaluator: Task completed with metrics: {'num_samples': oops}",
                      "INFO vero.evaluator: Task completed with metrics: {'num_samples': 3,",
                      "  'num_errors': 1}"])
    assert parse_metrics(text) == [None, metric(), None, None, dict(num_samples=3, num_errors=1)]


def test_parse_metrics_unclosed_dict_does_not_hide_later_lines():
    text = "\n".join(["INFO vero.evaluator: Task completed with metrics: {'num_samples': 1",
                      metric_line()])
    assert parse_metrics(text) == [None, metric()]


def test_parse_metrics_empty_log():
    assert parse_metrics("INFO gepa: nothing was evaluated") == []


def test_gepa_report_reads_seed_and_first_last_metrics():
    report = gepa_report(gepa_log())
    assert report["seed_score"] == pytest.approx(0.64)
    assert report["evaluations"] == 3
    assert report["initial"] == metric(score=0.80)
    assert report["final"] == metric(score=0.84)


def test_gepa_verdict_accepts_healthy_run():
    verdict = gepa_verdict(gepa_log())
    assert verdict["valid"] is True
    assert verdict["reasons"] == []
    assert verdict["error_rate"] == 0.0
    assert verdict["seed_score"] == pytest.approx(0.64)


def test_gepa_verdict_rejects_low_seed():
    verdict = gepa_verdict(gepa_log(seed="0.100"))
    assert verdict["valid"] is False
    assert verdict["reasons"] == [
        "seed scored 0.100 on the test split (floor 0.25): the services were not healthy"]


def test_gepa_verdict_rejects_final_error_rate():
    verdict = gepa_verdict(gepa_log(final_errors=40))
    assert verdict["valid"] is False
    assert verdict["reasons"] == ["final evaluation: 40 of 115 samples errored"]
    assert verdict["error_rate"] == pytest.approx(40 / 115)


def test_gepa_verdict_rejects_final_sample_shortage():
    verdict = gepa_verdict(gepa_log(final_samples=80))
    assert verdict["valid"] is False
    assert verdict["reasons"] == ["final evaluation covered only 80 of 115 samples"]


def test_gepa_verdict_all_none_metrics_are_not_an_evaluation():
    log = "\n".join(["INFO gepa: Initial test score: 0.640"] +
                    ["INFO vero.evaluator: Task completed with metrics: None"] * 3)
    assert parse_metrics(log) == [None, None, None]
    report = gepa_report(log)
    assert report["evaluations"] == 3
    assert report["initial"] is None and report["final"] is None
    verdict = gepa_verdict(log)
    assert verdict["valid"] is False
    assert verdict["reasons"] == ["final test evaluation missing"]


def test_gepa_verdict_without_metrics():
    verdict = gepa_verdict("INFO gepa: starting, nothing completed\n")
    assert verdict["report"]["evaluations"] == 0
    assert verdict["valid"] is False
    assert verdict["reasons"] == [
        "no evaluation completed", "no initial test score", "final test evaluation missing"]
    assert verdict["error_rate"] is None


def test_gepa_verdict_single_metrics_record_has_no_final():
    verdict = gepa_verdict(gepa_log(evaluations=1))
    assert verdict["report"]["evaluations"] == 1
    assert verdict["report"]["final"] is None
    assert verdict["valid"] is False
    assert verdict["reasons"] == ["final test evaluation missing"]


def test_run_verdict_without_calibration():
    verdict = run_verdict({})
    assert verdict == dict(valid=False, reasons=["no seed calibration"], seed_val=None, rerun_var=None)


def test_run_verdict_without_validation_mean():
    state = {"calibration": {"rerun_var": None, "validation": {"mean": None}}}
    verdict = run_verdict(state)
    assert verdict == dict(valid=False, reasons=["no seed calibration"], seed_val=None, rerun_var=None)


def test_run_verdict_healthy_and_low_seed():
    calibration = {"rerun_var": 0.04, "validation": {"mean": 0.62}}
    good = run_verdict({"calibration": calibration})
    assert good == dict(valid=True, reasons=[], seed_val=0.62, rerun_var=0.04)

    calibration["validation"]["mean"] = 0.12
    low = run_verdict({"calibration": calibration})
    assert low["valid"] is False
    assert low["reasons"] == [
        "seed scored 0.120 on validation (floor 0.25): the services were not healthy"]
    assert low["seed_val"] == 0.12
    assert low["rerun_var"] == 0.04


def test_probe_verdict_unscored_samples():
    verdict = probe_verdict(dict(tasks=12, scored=9, mean_score=0.7, answered_rate=0.9))
    assert verdict["valid"] is False
    assert verdict["reasons"] == ["only 9 of 12 samples were scored"]
    assert verdict["scored"] == 9 and verdict["tasks"] == 12


def test_probe_verdict_without_score():
    verdict = probe_verdict(dict(tasks=12, scored=12, mean_score=None, answered_rate=0.8))
    assert verdict["valid"] is False
    assert verdict["reasons"] == ["no score"]


def test_probe_verdict_low_mean_score():
    verdict = probe_verdict(dict(tasks=12, scored=12, mean_score=0.1, answered_rate=0.8))
    assert verdict["valid"] is False
    assert verdict["reasons"] == [
        "probe scored 0.100 (floor 0.25): the services were not healthy"]


def test_probe_verdict_low_answered_rate():
    verdict = probe_verdict(dict(tasks=12, scored=12, mean_score=0.7, answered_rate=0.5),
                            answered_floor=0.8)
    assert verdict["valid"] is False
    assert verdict["reasons"] == ["answered rate 0.50 (floor 0.80)"]


def test_probe_verdict_healthy():
    verdict = probe_verdict(dict(tasks=12, scored=12, mean_score=0.7, answered_rate=0.9))
    assert verdict == dict(valid=True, reasons=[], mean_score=0.7, answered_rate=0.9,
                           scored=12, tasks=12)
