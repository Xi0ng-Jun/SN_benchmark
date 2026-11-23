"""Write configs/alw/catalog.json from an AnalogLayoutWorld checkout (read as data only).

    python scripts/make_catalog.py --alw-repo "$ALW_REPO" --out configs/alw/catalog.json

The checkout path is not recorded; the catalog records the checkout commit instead.
"""
import argparse
import json
import os
import statistics
import sys
from pathlib import Path

from ssap.targets.alw.catalog import catalog_document, load_catalog, repo_commit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alw-repo", default=os.environ.get("ALW_REPO"))
    ap.add_argument("--out", default="configs/alw/catalog.json")
    a = ap.parse_args(argv)
    if not a.alw_repo:
        ap.error("--alw-repo or $ALW_REPO is required")
    tasks = load_catalog(a.alw_repo)
    doc = catalog_document(tasks, repo_commit(a.alw_repo))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    devs = [t.devices for t in tasks]
    print(f"{len(tasks)} tasks, commit {doc['commit']}, devices median {statistics.median(devs)} "
          f"[{min(devs)}-{max(devs)}] -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
