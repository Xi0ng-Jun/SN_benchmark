# SSAP / MCR

Mirror-Certified Ratchet (`MCR`, v2) for optimizing frozen-model agent harnesses on stateful, signoff-gated engineering tasks.

In stateful engineering domains (such as analog IC layout and physical verification), an agent modifies workspace files through external EDA tools while final acceptance is decided by an isolated multi-criteria signoff verifier $\mathcal{E}^\star(x, W) = (Y^\star, C^\star, \mathbf{d}^\star)$. Request-level proxy replay breaks on stateful files, multi-threaded tool logs, and post-processing hooks, while naive mid-trajectory rollback blocks valid multi-step edits and decorative statistical gates reject genuine improvements on heavy, small-cluster benchmarks. MCR v2 centers on three core invariants:

1. **Fixed fallback kernel ($H_{\mathrm{base}}$) and zero-parameter mirror-certified ratchet.** Agents iterate freely in a draft workspace without mid-trajectory forced rollback, while canonical submission artifacts are updated atomically only along concordance-verified Class-1 mirror checkers (Class-2 heuristic probes remain strictly advisory with zero overwrite authority). Dual wall-clock watermarks ($T_{\mathrm{warn}}, T_{\mathrm{soft}}$) pair with a bounded, stateless terminal repair sub-invocation so live deployment and offline fork-point replay share identical prefix-invariant semantics.
2. **Active Referee Bank ($\mathcal{B}_{\mathrm{dev}}$) and Stage 0 zero-LLM contracts.** Development artifacts and deterministic positive/negative mutations are re-signed offline with tri-state sub-criteria $\mathbf{d}^\star \in \{0, 1, \bot\}^K$ ($\bot$ strictly excluded from clean/defective sets) and internal blind clusters when $G_{\mathrm{dev}} \ge 8$, calibrating single-parameter guard bands $\epsilon_k$ at zero LLM cost while keeping `val` and `test` 100% sealed.
3. **Fork-aligned evaluation and pure `C1` holdout protocol.** Candidate changes are evaluated along their exact causal fork path (Path 1 stateless terminal snapshot replay with `Rescued / Neutral / Damaged` event ledger and $K_{\mathrm{idle}} = 2$ zero-rescue pruning; Path 2 conditional hook pairing with prefix-leakage audit; Path 3 full-run pooling). Holdout confirmation uses pure `C1` paired selection ($\hat{\Delta}_{\mathrm{val}}(Y^\star) > 10^{-6}$) with zero decorative statistical veto gates, followed by single-unblind multi-seed ($R \ge 3$) sealed `test` evaluation reporting three-stage attribution ($H_{\mathrm{raw}} \to H_{\mathrm{base}} \to H^\star$), $C^\star$ flip vs. partial-progress decomposition, raw physical cost ledgers, and `infra_error` separation with `delta_worst`.

## Documentation

- **MCR v2 Formal & Plain Specifications**: [docs/mcr-formal.tex](docs/mcr-formal.tex), [docs/mcr-formal.pdf](docs/mcr-formal.pdf), [docs/mcr-plain.md](docs/mcr-plain.md)
- **AnalogLayoutWorld (`ALW`) Instantiation & Rollout**:
  - Formal instantiation & $H_{\mathrm{base}}$ contract: [docs/alw-mcr-instantiation.md](docs/alw-mcr-instantiation.md)
  - Progressive Phase 0–3 execution plan & module architecture: [docs/alw-progressive-plan.md](docs/alw-progressive-plan.md)
  - Evaluation server, gateway homogeneity & proxy isolation: [docs/alw-server-env.md](docs/alw-server-env.md)
  - Pre-implementation architectural review disposition: [docs/fable-5.2-plan-review.md](docs/fable-5.2-plan-review.md)
- **Archived v1 Draft** (superseded by MCR v2): [docs/ssap.tex](docs/ssap.tex), [docs/ssap.pdf](docs/ssap.pdf)

## Package & CLI (`ssap`)

Install in a Python $\ge 3.12$ virtual environment:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Available CLI subcommands (`ssap --help`):
- `ssap alw-check-split` — verify cluster-disjoint `12/14/14` split (`ssap-alw-v2-19`), blind clusters, and static anti-leakage / forbidden-import invariants
- `ssap alw-concordance` — offline `/opt/harness` container smoke, $\mathcal{B}_{\mathrm{dev}}$ construction, synthetic mutations, Stage 0 contracts, and $\epsilon_k$ calibration
- `ssap alw-diagnose` — four zero-LLM `dev` baseline diagnostics and mechanical module switches
- `ssap alw-eval` — Harbor execution modes (`smoke-10m`, `subset`, `baseline`), Path 1 fork replay (`fork-path1`), and pure `C1` validation (`c1-val`)
- `ssap alw-loop` — progressive outer search loop with $K_{\mathrm{idle}} = 2$ pruning
- `ssap alw-report` — single-unblind multi-seed sealed `test` attribution & cost report

Run deterministic local unit tests sequentially:

```bash
.venv/bin/pytest
.venv/bin/ssap alw-check-split --config configs/alw/config.server.json
```
