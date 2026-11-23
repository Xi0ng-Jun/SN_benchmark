"""Quote/brace-aware SPICE reader for the fixed task netlist (``circuit.spice``).

Statement splitting mirrors the reviewed reference parser of the benchmark (``_without_comment``,
``_logical_lines``, ``_split_statement``): ``;`` / ``$`` start a comment only outside quotes and
braces, ``+`` continues a statement, and the node list ends at the first ``name=`` assignment or
``params:`` marker, so parameter expressions such as ``ad='int(nf/2) * W/nf * 0.29'`` never leak
into node lists.

Two modes:
* ``strict=True`` raises exactly where the reference parser raises (parameterized subcircuit
  definitions, duplicate names, duplicate ports, orphan continuation, unclosed scopes). Used for
  the floating-port exemption, which must fall back to "nothing exempt" in the same cases.
* ``strict=False`` (contract resolution) accepts parameterized definitions.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

_PARAMETER = re.compile(r"([a-zA-Z_]\w*)\s*=\s*(\{[^{}]*\}|'[^']*'|\"[^\"]*\"|[^\s]+)")
# Device model names the reference parser reserves (cannot be subcircuit names).
RESERVED_MODELS = frozenset({
    "sky130_fd_pr__nfet_01v8", "sky130_fd_pr__pfet_01v8", "sky130_fd_pr__nfet_01v8_lvt",
    "sky130_fd_pr__pfet_01v8_lvt", "sky130_fd_pr__nfet_05v0_nvt",
    "sky130_fd_pr__res_generic_po", "sky130_fd_pr__res_high_po", "sky130_fd_pr__res_high_po_0p35",
    "sky130_fd_pr__res_high_po_0p69", "sky130_fd_pr__res_high_po_1p41",
    "sky130_fd_pr__res_high_po_5p73", "sky130_fd_pr__res_xhigh_po",
    "sky130_fd_pr__res_xhigh_po_0p35", "sky130_fd_pr__res_xhigh_po_0p69",
    "sky130_fd_pr__res_xhigh_po_1p41", "sky130_fd_pr__res_generic_m1",
    "sky130_fd_pr__cap_mim_m3_1", "sky130_fd_pr__cap_mim_m3_2",
    "sky130_fd_pr__npn_05v5_w1p00l1p00", "sky130_fd_pr__pnp_05v5_w0p68l0p68",
})


class SpiceError(ValueError):
    pass


@dataclass
class Device:
    name: str
    card: str
    nodes: list[str]
    model: str
    parameters: dict[str, str]
    line: int


@dataclass
class Circuit:
    name: str
    ports: list[str]
    parent: str | None                 # casefolded key of the enclosing .subckt (nested scope)
    devices: list[Device] = field(default_factory=list)
    directives: list[str] = field(default_factory=list)
    parameterized: bool = False


def without_comment(line: str) -> str:
    quote = None
    braces = 0
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "{":
            braces += 1
        elif char == "}":
            braces -= 1
        elif char in ";$" and braces == 0:
            return line[:index].strip()
    return line.strip()


def logical_lines(text: str):
    current, start = "", 0
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("*"):
            continue
        line = without_comment(raw)
        if not line:
            continue
        if line.startswith("+"):
            if not current:
                raise SpiceError(f"L{number}: continuation without a statement")
            current += " " + line[1:].strip()
        else:
            if current:
                yield start, current
            current, start = line, number
    if current:
        yield start, current


def _parameters(text: str, line: int, strict: bool) -> dict[str, str]:
    values: dict[str, str] = {}
    position = 0
    while position < len(text):
        m = _PARAMETER.match(text, position)
        if m is None:
            if strict:
                raise SpiceError(f"L{line}: invalid parameter syntax near {text[position:]!r}")
            break
        key, value = m.group(1).casefold(), m.group(2)
        if key in values and strict:
            raise SpiceError(f"L{line}: duplicate parameter {key}")
        values[key] = value
        position = m.end()
        if position < len(text) and not text[position].isspace():
            if strict:
                raise SpiceError(f"L{line}: missing parameter separator")
            break
        while position < len(text) and text[position].isspace():
            position += 1
    return values


def split_statement(text: str, line: int = 0, strict: bool = True):
    """``(words_before_parameters, parameters, has_params_marker)``."""
    assignment = re.search(r"(?:^|\s)([a-zA-Z_]\w*)\s*=", text)
    marker = re.search(r"\bparams\s*:", text, re.I)
    cut = assignment.start(1) if assignment else len(text)
    if marker and marker.start() < cut:
        cut = marker.start()
        tail = text[marker.end():].strip()
    else:
        tail = text[cut:].strip()
    return text[:cut].split(), _parameters(tail, line, strict), marker is not None


def parse(text: str, *, strict: bool = True) -> dict[str, Circuit]:
    circuits: dict[str, Circuit] = {}
    stack: list[Circuit | None] = []
    active: Circuit | None = None
    for line, statement in logical_lines(text):
        head = statement.split(None, 1)[0].casefold()
        if head == ".subckt":
            words, parameters, marker = split_statement(statement, line, strict)
            if len(words) < 3:
                raise SpiceError(f"L{line}: subcircuit needs a name and ports")
            if (parameters or marker) and strict:
                raise SpiceError(f"L{line}: parameterized subcircuit definitions are unsupported")
            name, ports = words[1], words[2:]
            key = name.casefold()
            if key in circuits or (strict and key in RESERVED_MODELS):
                raise SpiceError(f"L{line}: duplicate or reserved subcircuit name {name}")
            if len({p.casefold() for p in ports}) != len(ports):
                raise SpiceError(f"L{line}: duplicate subcircuit port")
            stack.append(active)
            active = Circuit(name, ports, active.name.casefold() if active else None,
                             parameterized=bool(parameters or marker))
            circuits[key] = active
        elif head == ".ends":
            words = statement.split()
            if active is None or not stack:
                raise SpiceError(f"L{line}: .ends without .subckt")
            if len(words) > 2 or (len(words) == 2 and words[1].casefold() != active.name.casefold()):
                raise SpiceError(f"L{line}: .ends does not match {active.name}")
            active = stack.pop()
        elif active is None:
            continue
        elif head.startswith("."):
            active.directives.append(f"L{line}: {statement}")
        else:
            words, parameters, _ = split_statement(statement, line, strict)
            if len(words) < 4:
                if strict:
                    raise SpiceError(f"L{line}: incomplete device statement")
                if len(words) < 3:
                    continue
            name = words[0]
            if strict and any(d.name.casefold() == name.casefold() for d in active.devices):
                raise SpiceError(f"L{line}: duplicate device instance {name}")
            active.devices.append(Device(name, name[0].upper(), words[1:-1], words[-1],
                                         parameters, line))
    if stack or active is not None:
        raise SpiceError(f"Unclosed subcircuit {active.name if active else '<unknown>'}")
    return circuits


def call_graph_roots(circuits: dict[str, Circuit]) -> list[str]:
    referenced = {d.model.casefold() for c in circuits.values() for d in c.devices
                  if d.card == "X" and d.model.casefold() in circuits}
    return [c.name for k, c in circuits.items() if c.parent is None and k not in referenced]


def is_nmos(device: Device) -> bool:
    return device.card in ("M", "X") and "nfet" in device.model.casefold()


def nmos_bulk_counts(circuits: dict[str, Circuit], top: str) -> Counter:
    """Counter over top-level nets (exact-case as first spelled) of NMOS bulk terminals,
    followed through every subcircuit level via port bindings."""
    counts: Counter = Counter()
    root = circuits[top.casefold()]

    def walk(circ: Circuit, binding: dict[str, str], ancestors: tuple[str, ...]):
        key = circ.name.casefold()
        if key in ancestors:
            raise SpiceError(f"recursive subcircuit hierarchy at {circ.name}")

        def resolve(node: str) -> str:
            return binding.get(node.casefold(), f"{'/'.join(ancestors + (key,))}:{node}")

        for d in circ.devices:
            child = circuits.get(d.model.casefold()) if d.card == "X" else None
            if child is not None:
                if len(d.nodes) != len(child.ports):
                    raise SpiceError(f"L{d.line}: subcircuit terminal count mismatch")
                walk(child, {p.casefold(): resolve(n) for p, n in zip(child.ports, d.nodes)},
                     ancestors + (key,))
            elif is_nmos(d) and len(d.nodes) >= 4:
                counts[resolve(d.nodes[3])] += 1

    walk(root, {p.casefold(): p for p in root.ports}, ())
    return counts


def floating_ports(text: str, top: str) -> list[str]:
    """Ports of *top* whose net reaches no device or subcircuit terminal inside *top*
    (strict reference parse; raises ``SpiceError`` where the reference parser raises)."""
    if not isinstance(text, str) or not isinstance(top, str) or not top.strip():
        raise SpiceError("text and a nonempty top name must be strings")
    circuit = parse(text, strict=True).get(top.casefold())
    if circuit is None:
        raise SpiceError(f"Missing top subcircuit {top}")
    used = {node.casefold() for d in circuit.devices for node in d.nodes}
    return [p for p in circuit.ports if p.casefold() not in used]
