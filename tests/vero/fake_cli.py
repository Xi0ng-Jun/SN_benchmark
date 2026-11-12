"""rag_eval.vero.cli with the offline ``fake`` preset registered (subprocess entry point of test_run_series)."""
import sys

from rag_eval.vero import adapter, cli

adapter.PRESETS["fake"] = adapter.Preset(
    name="fake", agent_subdir="agent", module="fake_agent.vero_tasks", task="fake_qa", import_root=".",
    dataset="fake", description="a toy QA agent", gold_keys=("answer",))
sys.exit(cli.main(sys.argv[1:]))
