"""Checker registry and the ALW ``H_base`` evaluation suite.

Class 1 (``overwrite_authority=True``): concordance-verified mirror checkers; only these enter
the certificate vector ``m`` and decide ratchet promotion. Class 2 (``overwrite_authority=False``):
advisory probes; they may emit notices and (if ``gate_trigger``) request a ratchet-protected
terminal repair, never promote, demote or veto.

``scope``: ``"trajectory"`` checkers run on every mid-trajectory promotion (changing one changes
Phase A: Path 2); ``"terminal_only"`` checkers run only in the terminal gate after Phase A
(changing one keeps Path 1 replay valid).
"""
from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from ...kernel.promote import Workspace
from ...kernel.ratchet import Cert, TierSpec
from . import area_fill, drc, gds_geom, lvs, morphology
from .runtime import ToolTimeout, load_runtime

SCOPES = ("trajectory", "terminal_only")
ARTIFACT_NAME = "layout.gds"
AUX_NAME = "src"


@dataclass(frozen=True)
class CheckerSpec:
    name: str
    klass: int
    scope: str = "trajectory"
    enabled_in_hbase: bool = True
    eps: float | None = 0.0
    depends_on: tuple[str, ...] = ()
    gate_trigger: bool = False
    description: str = ""

    @property
    def overwrite_authority(self) -> bool:
        return self.klass == 1


class Registry:
    def __init__(self, specs: list[CheckerSpec]):
        names = [s.name for s in specs]
        if len(set(names)) != len(names):
            raise ValueError("duplicate checker names")
        for s in specs:
            if s.klass not in (1, 2):
                raise ValueError(f"{s.name}: class must be 1 or 2")
            if s.scope not in SCOPES:
                raise ValueError(f"{s.name}: scope must be one of {SCOPES}")
            if s.klass == 2 and s.eps is not None:
                raise ValueError(f"{s.name}: Class-2 probes have no guard band (no m component)")
            if s.klass == 1 and (s.eps is None or s.eps < 0):
                raise ValueError(f"{s.name}: Class-1 checkers need eps >= 0")
            for d in s.depends_on:
                if d not in names:
                    raise ValueError(f"{s.name}: unknown dependency {d}")
        self.specs = list(specs)

    def get(self, name: str) -> CheckerSpec:
        return next(s for s in self.specs if s.name == name)

    def mirror(self, scope: str = "trajectory") -> list[CheckerSpec]:
        """Enabled Class-1 checkers of *scope*: the components of ``m`` (in order)."""
        return [s for s in self.specs if s.klass == 1 and s.enabled_in_hbase and s.scope == scope]

    def advisories(self) -> list[CheckerSpec]:
        return [s for s in self.specs if s.klass == 2 and s.enabled_in_hbase]

    def gate_triggers(self) -> list[str]:
        return [s.name for s in self.advisories() if s.gate_trigger]

    def with_change(self, name: str, **changes) -> "Registry":
        return Registry([replace(s, **changes) if s.name == name else s for s in self.specs])

    def evaluation_path(self, other: "Registry") -> str:
        """Path of a candidate registry relative to this one: ``"path1"`` when only
        terminal-only gates / Class-2 probes differ, ``"path2"`` when the trajectory mirror changes."""
        return "path2" if [(s.name, s.eps) for s in self.mirror()] != \
            [(s.name, s.eps) for s in other.mirror()] else "path1"

    def tier_spec(self, tiers: list[list[int]]) -> TierSpec:
        mir = self.mirror()
        return TierSpec.from_lists(tiers, [s.eps for s in mir], [s.name for s in mir])


ALW_TIERS = [[0], [1], [2, 3, 4, 5]]


def alw_registry(eps_fill: float = area_fill.EPS_FILL_DEFAULT,
                 eps_shape: float = area_fill.EPS_SHAPE) -> Registry:
    area_fill.validate_shape_eps(eps_shape)
    return Registry([
        CheckerSpec("m_gds", 1, eps=0.0, description="regular readable GDS, exact top, 38-layer bbox"),
        CheckerSpec("m_lvs", 1, eps=0.0, depends_on=("m_gds",), description="original-deck LVS"),
        CheckerSpec("m_shape", 1, eps=eps_shape, depends_on=("m_gds",), description="q <= 4.0"),
        CheckerSpec("m_fill", 1, eps=eps_fill, depends_on=("m_gds", "m_lvs"), description="rho >= 0.10"),
        CheckerSpec("m_morph", 1, eps=0.0, depends_on=("m_gds", "m_lvs"), description="not meaningless"),
        CheckerSpec("m_drc", 1, eps=0.0, depends_on=("m_gds",), description="0 DRC markers"),
        CheckerSpec("m_pex", 1, scope="terminal_only", enabled_in_hbase=False, eps=0.0,
                    depends_on=("m_lvs",), description="Phase 2/3 candidate RC extraction"),
        CheckerSpec("advisory_aspect_gt_3", 2, eps=None, gate_trigger=True,
                    description="3.0 < q <= 4.0 (fails q_max = 3.0 tasks)"),
        CheckerSpec("advisory_missing_src_py", 2, eps=None, description="no *.py in submission src/"),
        CheckerSpec("advisory_task_modified", 2, eps=None, description="live task/ differs from T=0"),
    ])


def alw_workspace(root: str | Path) -> Workspace:
    return Workspace(Path(root), ARTIFACT_NAME, AUX_NAME)


@dataclass
class AlwContext:
    trusted_task_dir: Path
    eda_env: Path
    prompt_path: Path | None = None
    live_task_dir: Path | None = None
    work_dir: Path | None = None
    timeout_sec: float = 900.0
    keep_runs: int = 5
    runtime: dict | None = None

    @classmethod
    def for_workspace(cls, root: str | Path, **kw) -> "AlwContext":
        root = Path(root)
        trusted = root / ".mcr" / "trusted_task"
        prompt = root / ".mcr" / "task_prompt.txt"
        return cls(trusted_task_dir=trusted / "task", eda_env=trusted / "eda_env.sh",
                   prompt_path=prompt if prompt.is_file() else None,
                   live_task_dir=root / "task", work_dir=root / ".mcr" / "checks", **kw)


@dataclass
class AlwSuite:
    ctx: AlwContext
    registry: Registry = field(default_factory=alw_registry)

    def __post_init__(self):
        self.tier_spec = self.registry.tier_spec(ALW_TIERS)
        self._contract: lvs.TaskContract | None = None

    @property
    def backend(self) -> str:
        return lvs.detect_lvs_backend(self.ctx.eda_env)

    @property
    def contract(self) -> lvs.TaskContract:
        if self._contract is None:
            self._contract = lvs.resolve_task_contract(self.ctx.trusted_task_dir / "circuit.spice",
                                                       self.ctx.prompt_path)
        return self._contract

    def _run_dir(self) -> Path:
        base = self.ctx.work_dir or Path("/tmp/mcr_checks")
        base.mkdir(parents=True, exist_ok=True)
        runs = sorted(p for p in base.iterdir() if p.is_dir())
        for old in runs[:max(0, len(runs) - self.ctx.keep_runs + 1)]:
            shutil.rmtree(old, ignore_errors=True)
        d = base / f"{time.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
        d.mkdir()
        return d

    def evaluate(self, artifact: Path, aux: Path | None) -> Cert:
        artifact = Path(artifact)
        raw: list[float | None] = [None] * 6
        details: dict = {"backend": self.backend}
        adv: dict = {}
        timed_out = False
        deadline = time.monotonic() + self.ctx.timeout_sec

        def left() -> float:
            return max(1.0, deadline - time.monotonic())

        present = gds_geom.regular_nonempty_file(artifact)
        adv["advisory_missing_src_py"] = gds_geom.advisory_missing_src_py(aux)
        if self.ctx.live_task_dir is not None:
            adv["advisory_task_modified"] = gds_geom.advisory_task_modified(
                self.ctx.live_task_dir, self.ctx.trusted_task_dir)
        try:
            contract = self.contract
            details["contract"] = contract.as_dict()
        except (OSError, ValueError) as exc:
            details["contract_error"] = str(exc)
            return Cert.build(present, raw, self.tier_spec, advisory_flags=adv, details=details)
        try:
            geo = gds_geom.check_geometry(artifact, contract.top)
            raw[0] = 0.0
            details["geometry"] = geo.as_dict()
        except gds_geom.GeometryError as exc:
            raw[0] = -1.0
            details["geometry_error"] = str(exc)
            return Cert.build(present, raw, self.tier_spec, advisory_flags=adv, details=details)
        raw[2] = area_fill.shape_raw_margin(geo.width_um, geo.height_um)
        adv["advisory_aspect_gt_3"] = area_fill.advisory_aspect_gt_3(geo.width_um, geo.height_um)
        details["aspect_q"] = area_fill.aspect_ratio(geo.width_um, geo.height_um)
        run_dir = self._run_dir()
        details["run_dir"] = str(run_dir)
        runtime = self.ctx.runtime or load_runtime(self.ctx.eda_env)
        outcome = None
        try:
            outcome = lvs.check_lvs(artifact, contract, self.ctx.trusted_task_dir, self.backend,
                                    run_dir / "lvs", left(), runtime)
            raw[1] = 0.0 if outcome.passed else -1.0
            details["lvs"] = {"passed": outcome.passed, "reason": outcome.reason,
                              "out_dir": outcome.out_dir}
        except ToolTimeout as exc:
            timed_out = True
            details["lvs_error"] = f"timeout: {exc}"
        except Exception as exc:  # noqa: BLE001 - fail closed: m_2 = 0, never a crash
            raw[1] = -1.0
            details["lvs_error"] = f"{type(exc).__name__}: {exc}"
        lvs_ok = raw[1] == 0.0
        if lvs_ok:
            try:
                if self.backend == "magic_netgen":
                    area = area_fill.measure_extracted_area(
                        Path(outcome.out_dir) / "magic" / "extracted.spice",
                        extraction_script=Path(outcome.out_dir) / "magic" / "extract.tcl",
                        expected_top=contract.top + "_lvs")
                else:
                    area = area_fill.klayout_extracted_area(outcome.report, contract.top)
                raw[3] = area_fill.fill_raw_margin(area["area_um2"], geo.area_um2, True)
                details["fill"] = {"effective_area_um2": area["area_um2"],
                                   "rho": raw[3] + area_fill.RHO_MIN}
            except Exception as exc:  # noqa: BLE001 - fail closed (no partial area)
                details["fill_error"] = f"{type(exc).__name__}: {exc}"
            try:
                raw[4], details["morphology"] = morphology.check_morphology(
                    self.backend, True, outcome.report, geo.bbox_um)
            except Exception as exc:  # noqa: BLE001 - fail closed
                details["morphology_error"] = f"{type(exc).__name__}: {exc}"
        if not timed_out:
            try:
                raw[5], details["drc"] = drc.check_drc(artifact, contract.top, run_dir / "drc",
                                                       left(), runtime)
            except ToolTimeout as exc:
                timed_out = True
                details["drc_error"] = f"timeout: {exc}"
            except Exception as exc:  # noqa: BLE001 - fail closed
                details["drc_error"] = f"{type(exc).__name__}: {exc}"
        return Cert.build(present, raw, self.tier_spec, check_timed_out=timed_out,
                          advisory_flags=adv, details=details)

    def gate_advisories(self, cert: Cert | dict | None) -> list[str]:
        """Activated Class-2 gate triggers raised on *cert* (never part of ``m``)."""
        if cert is None:
            return []
        flags = cert.advisory_flags if isinstance(cert, Cert) else (cert.get("advisory_flags") or {})
        return [n for n in self.registry.gate_triggers() if flags.get(n)]

    @staticmethod
    def validate(artifact: Path) -> bool:
        return gds_geom.gds_header_ok(artifact)
