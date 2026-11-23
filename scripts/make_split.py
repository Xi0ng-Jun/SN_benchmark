"""Write configs/alw/clusters.json and configs/alw/split.json (seed ssap-alw-v2-19).

    python scripts/make_split.py --catalog configs/alw/catalog.json --alw-repo "$ALW_REPO"

Cluster predicates (derived from the catalog, which is read from the checkout; no task lists):
  hard (35):  H1 shared non-ADB upstream git repository; H2 shared internal non-PDK child
              .subckt name; H3 source == "adb" and identical top .subckt name.
  conservative (32): hard clusters plus C1 identical top .subckt across different sources
              (an ADB folded_cascode_ota and a team-provided folded_cascode_ota) and C2 the
              source == "loadbench" batch. complementary_folded_cascode_ab_opamp and
              fd_two_stage_miller_opamp are different top names and stay singletons.
The split deals whole conservative clusters (see ssap.targets.alw.splits.deal). With --alw-repo
the catalog is re-read from the checkout first and must equal the given catalog.json.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from ssap.targets.alw.catalog import catalog_document, load_catalog, repo_commit
from ssap.targets.alw.splits import SEED, check_split_invariants, derive_clusters, make_split


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="configs/alw/catalog.json")
    ap.add_argument("--alw-repo", default=os.environ.get("ALW_REPO"))
    ap.add_argument("--seed", default=SEED)
    ap.add_argument("--out-dir", default="configs/alw")
    a = ap.parse_args(argv)
    catalog_data = json.loads(Path(a.catalog).read_text())
    if a.alw_repo:
        fresh = catalog_document(load_catalog(a.alw_repo), repo_commit(a.alw_repo))
        if fresh["tasks"] != catalog_data["tasks"]:
            sys.exit("catalog.json differs from the ALW checkout; rerun scripts/make_catalog.py")
    clusters = derive_clusters(catalog_data["tasks"])
    clusters["catalog_commit"] = catalog_data.get("commit")
    split = make_split(catalog_data, clusters, a.seed)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "clusters.json").write_text(json.dumps(clusters, indent=1) + "\n")
    (out / "split.json").write_text(json.dumps(split, indent=1) + "\n")
    rep = check_split_invariants(catalog_data, clusters, split, seed=a.seed)
    for k in ("dev", "val", "test"):
        print(f"{k:5s} {split['splits'][k]}  {json.dumps(split['stats'][k])}")
    print(f"hard={len(clusters['hard'])} conservative={len(clusters['conservative'])} "
          f"G_dev={split['G_dev']} blind={split['blind_clusters']} invariants_ok={rep.ok}")
    for c in rep.failures():
        print(f"FAIL {c.rule} {c.name}: {c.detail}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
