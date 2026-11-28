"""scripts/traj_digest.py on a synthetic trajectory in the Harbor layout (no containers)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("traj_digest", ROOT / "scripts" / "traj_digest.py")
td = importlib.util.module_from_spec(spec)
spec.loader.exec_module(td)


def call(name: str, command: str, description: str = "") -> dict:
    return {"tool_call_id": "c", "function_name": name,
            "arguments": {"command": command, "description": description}}


def make_state(tmp_path: Path) -> tuple[Path, Path]:
    trial = tmp_path / "state" / "jobs" / "test-H_raw" / "harbor" / "test-H_raw-H_raw-x-s0" / "x__abc"
    steps = [
        {"timestamp": "2026-11-26T01:00:00.000Z", "source": "agent", "message": "start",
         "tool_calls": [call("Bash", "cat GUIDE.md", "Read the guide")],
         "observation": {"results": [{"content": "guide text"}]}},
        {"timestamp": "2026-11-26T01:12:00.000Z", "source": "agent", "reasoning_content": "t" * 5000,
         "tool_calls": [call("Bash", "klayout -b -r sky130.lvs -rd input=a.gds", "Run LVS")],
         "observation": {"results": [{"content": "ERROR : Netlists don't match"}]}},
        {"timestamp": "2026-11-26T01:13:00.000Z", "source": "user", "message": "Output token limit hit. Resume"},
        {"timestamp": "2026-11-26T01:30:00.000Z", "source": "agent",
         "tool_calls": [call("Bash", "cp a.gds submission/layout.gds")], "observation": None},
        {"timestamp": "2026-11-26T01:31:00.000Z", "source": "agent", "model_name": "<synthetic>",
         "message": "API Error: Claude's response exceeded the 32000 output token maximum."},
    ]
    (trial / "agent").mkdir(parents=True)
    (trial / "agent" / "trajectory.json").write_text(json.dumps({"steps": steps, "final_metrics": {"total_steps": 5}}))
    (trial / "verifier" / "evaluation").mkdir(parents=True)
    (trial / "verifier" / "evaluation" / "metrics.json").write_text(json.dumps(
        {"status": "evaluated", "score": 0.1, "design_passed": False, "scoring_inputs": {"lvs_pass": False}}))
    return tmp_path / "state", trial / "agent" / "trajectory.json"


def test_modes(tmp_path, capsys):
    state, traj = make_state(tmp_path)
    assert td.main(["list", str(state)]) == 0
    assert "test-H_raw\ttest-H_raw-H_raw-x-s0" in capsys.readouterr().out

    assert td.main(["overview", "H_raw-x-s0", "--state", str(state)]) == 0
    out = capsys.readouterr().out
    assert "steps=5 minutes=31" in out and '"lvs_pass": false' in out
    assert "output-limit prompts=1 api errors=1 thinking chars=5000" in out
    assert " 10- 20m steps=  2" in out and "lvs=1" in out and "LIMIT=1" in out
    lines = [l for l in out.splitlines() if l.startswith("[")]
    acts = [l.split()[3] for l in lines if "think=" in l]
    assert acts == ["read", "lvs", "LIMIT", "deliver", "APIERR"]
    assert "Run LVS" in out                                   # the agent's own description is shown

    assert td.main(["steps", str(traj), "1-2", "--think", "90"]) == 0
    out = capsys.readouterr().out
    assert "===== step 1  12.0 min" in out and "Netlists don't match" in out and "chars omitted" in out
    assert "===== step 2" in out and "step 3" not in out

    assert td.main(["grep", str(traj), "don.t match", "--where", "obs"]) == 0
    out = capsys.readouterr().out
    assert "[   1]" in out and "# 1 matches" in out


def stream_lines(with_result: bool) -> str:
    def a(ts, *blocks):
        return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": list(blocks)}}
    lines = [
        {"type": "system", "subtype": "init", "cwd": "/alb-workspace"},
        {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 10},
        a("2026-11-26T08:00:00.000Z", {"type": "thinking", "thinking": "plan " * 50}),
        a("2026-11-26T08:00:30.000Z", {"type": "tool_use", "id": "c1", "name": "Bash",
                                       "input": {"command": "klayout -b -r sky130.lvs", "description": "Run LVS"}}),
        {"type": "user", "timestamp": "2026-11-26T08:02:00.000Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1",
                                                  "content": "ERROR : Netlists don't match"}]}},
        {"type": "system", "subtype": "compact_boundary"},
        {"type": "user", "timestamp": 1795680300000,
         "message": {"role": "user", "content": "Output token limit hit. Resume directly"}},
        a("2026-11-26T08:06:00.000Z", {"type": "text", "text": "done"}),
        {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 14224},
    ]
    if with_result:
        lines.append({"type": "result", "subtype": "error_max_turns", "num_turns": 81, "duration_ms": 360000,
                      "usage": {"input_tokens": 5, "output_tokens": 7}, "result": "long text"})
    return "\n".join(json.dumps(l) for l in lines) + "\n"


def test_stream_json_log(tmp_path, capsys):
    snap = tmp_path / "trial" / "agent" / "mcr_snapshot"
    snap.mkdir(parents=True)
    (tmp_path / "trial" / "verifier" / "evaluation").mkdir(parents=True)
    (tmp_path / "trial" / "verifier" / "evaluation" / "metrics.json").write_text(json.dumps({"status": "evaluated", "score": 0.1}))
    log = snap / "phase_b_stream.jsonl"
    log.write_text(stream_lines(with_result=True))
    assert td.main(["overview", str(log)]) == 0
    out = capsys.readouterr().out
    assert "steps=5 minutes=6" in out and '"subtype": "error_max_turns"' in out and '"num_turns": 81' in out
    assert '"score": 0.1' in out and "output-limit prompts=1" in out and "long text" not in out
    assert "lvs" in out and "Run LVS" in out and "[context compacted]" not in out.split("## steps")[0]
    td.main(["steps", str(log), "1"])
    out = capsys.readouterr().out
    assert "step 1  0.5 min" in out and "Netlists don't match" in out
    td.main(["grep", str(log), "compacted", "--where", "msg"])
    assert "# 1 matches" in capsys.readouterr().out

    lone = tmp_path / "proposer_stream.jsonl"
    lone.write_text(stream_lines(with_result=False))
    td.main(["overview", str(lone), "--no-steps"])
    out = capsys.readouterr().out
    assert "stopped from outside, while the model was thinking" in out and "14224" in out
    assert "not a benchmark run" in out

