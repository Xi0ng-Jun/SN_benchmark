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
