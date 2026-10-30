"""Transaction-level interfaces of a DUT, found from its ports.

A port list says how a design moves data, within a few naming conventions:
``x`` with ``x_valid`` is a ``valid`` stream, with ``x_ready`` too a
``ready_valid`` stream; AXI4-Stream's ``x_tdata``/``x_tvalid``/``x_tready``
is ``ready_valid`` as well; EMP's link word fields ``x_data``, ``x_valid``,
``x_start``, ``x_last``, ``x_start_of_orbit``, ``x_strobe`` are an
``emp_lword``; ``x`` with Vitis HLS's ``x_ap_vld`` is ``valid``. A data port
with none of these is ``scalar``: a value per clock cycle, except in a block
with HLS's ``ap_start``/``ap_ready``/``ap_done`` (``ap_ctrl_hs``): there the
scalar inputs are one call, held with ``ap_start`` until ``ap_ready``
(``ready_valid``), and the scalar outputs its result, taken at ``ap_done``
(``valid``). What the ports cannot say (a different name for the valid, a port that
only looks like a stream) is given in the plan's ``protocols``.

Each protocol is versioned: evidence records the version a result was
obtained with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

PROTOCOLS = {"scalar": 1, "valid": 1, "ready_valid": 1, "emp_lword": 1}
HLS_CONTROL = ("ap_start", "ap_ready", "ap_done")
EMP_FIELDS = ("valid", "start", "last", "start_of_orbit", "strobe")


@dataclass(frozen=True)
class Port:
    name: str
    direction: str      # "in" | "out"
    width: int


@dataclass
class Interface:
    """One stream of transactions: its data ports (the word, packed in order,
    first port least significant) and its handshake ports."""

    name: str
    protocol: str
    direction: str
    data: List[Port]
    valid: Optional[Port] = None
    ready: Optional[Port] = None
    # emp_lword: field -> port
    fields: Dict[str, Port] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return sum(p.width for p in self.data)

    @property
    def version(self) -> str:
        return f"{self.protocol}@{PROTOCOLS[self.protocol]}"

    def ports(self) -> List[Port]:
        out = list(self.data)
        out += [p for p in (self.valid, self.ready) if p is not None]
        out += [p for k, p in self.fields.items() if p not in out]
        return out


class InterfaceError(ValueError):
    """The ports do not form the interfaces asked for."""


def _strip(name: str, suffixes: Sequence[str]) -> Optional[Tuple[str, str]]:
    for s in suffixes:
        if name.endswith(s) and len(name) > len(s):
            return name[: -len(s)], s
    return None


def infer(ports: Sequence[Port], *, exclude: Sequence[str] = (),
          declared: Optional[Mapping[str, Mapping]] = None) -> List[Interface]:
    """The interfaces of *ports* (clock and reset in *exclude*), in port order.

    *declared* maps an interface name to ``{protocol, data: [ports], valid,
    ready}`` for what naming does not reveal; its ports are taken first.
    """
    by_name = {p.name: p for p in ports if p.name not in set(exclude)}
    used: set = set()
    out: List[Interface] = []

    def take(name: str) -> Port:
        if name not in by_name:
            raise InterfaceError(f"no port {name!r}")
        if name in used:
            raise InterfaceError(f"port {name!r} is in two interfaces")
        used.add(name)
        return by_name[name]

    for iname, spec in (declared or {}).items():
        protocol = str(spec.get("protocol", "valid"))
        if protocol not in PROTOCOLS:
            raise InterfaceError(f"{iname}: protocol {protocol!r}, want one of {', '.join(PROTOCOLS)}")
        dports = [take(str(n)) for n in (spec.get("data") or [iname])]
        dirs = {p.direction for p in dports}
        if len(dirs) != 1:
            raise InterfaceError(f"{iname}: data ports in both directions")
        valid = take(str(spec["valid"])) if spec.get("valid") else None
        ready = take(str(spec["ready"])) if spec.get("ready") else None
        if protocol in ("valid", "ready_valid") and valid is None:
            raise InterfaceError(f"{iname}: a {protocol} interface needs its valid port")
        if protocol == "ready_valid" and ready is None:
            raise InterfaceError(f"{iname}: a ready_valid interface needs its ready port")
        out.append(Interface(iname, protocol, dirs.pop(), dports, valid, ready))

    names = [n for n in by_name if n not in used]
    # emp_lword: <base>_data plus at least valid among the EMP fields
    for n in list(names):
        hit = _strip(n, ("_data",))
        if not hit or n in used:
            continue
        base = hit[0]
        fields = {f: f"{base}_{f}" for f in EMP_FIELDS if f"{base}_{f}" in by_name}
        if "valid" in fields and len(fields) >= 3:
            word = take(n)
            iface = Interface(base, "emp_lword", word.direction, [word],
                              fields={f: take(p) for f, p in fields.items()})
            iface.valid = iface.fields["valid"]
            out.append(iface)
    for n in names:
        if n in used:
            continue
        p = by_name[n]
        if _strip(n, ("_valid", "_ready", "_tvalid", "_tready", "_ap_vld")) or n in HLS_CONTROL + ("ap_idle",):
            continue
        axis = _strip(n, ("_tdata",))
        base = axis[0] if axis else n
        vname, rname = (f"{base}_tvalid", f"{base}_tready") if axis else (f"{n}_valid", f"{n}_ready")
        if not axis and vname not in by_name and f"{n}_ap_vld" in by_name:
            vname = f"{n}_ap_vld"
        if vname in by_name and vname not in used:
            valid = take(vname)
            word = take(n)
            # ready flows against the data
            ready = take(rname) if rname in by_name and by_name[rname].direction != p.direction else None
            out.append(Interface(base, "ready_valid" if ready else "valid", p.direction, [word], valid, ready))
        else:
            out.append(Interface(n, "scalar", p.direction, [take(n)]))
    if all(c in by_name and c not in used for c in HLS_CONTROL):
        out = _hls_call(out, take, by_name)
    left = [n for n in by_name if n not in used]
    if left:
        raise InterfaceError(f"ports in no interface: {', '.join(left)} (declare them under protocols)")
    order = {p.name: i for i, p in enumerate(ports)}
    return sorted(out, key=lambda i: order[i.ports()[0].name])


def _hls_call(ifaces: List[Interface], take, by_name: Mapping[str, Port]) -> List[Interface]:
    """The scalar interfaces of an ``ap_ctrl_hs`` block, as its call and its result."""
    start, ready, done = (take(c) for c in HLS_CONTROL)
    if "ap_idle" in by_name:
        take("ap_idle")
    ins = [i for i in ifaces if i.protocol == "scalar" and i.direction == "in"]
    outs = [i for i in ifaces if i.protocol == "scalar" and i.direction == "out"]
    rest = [i for i in ifaces if i not in ins and i not in outs]
    if ins:
        rest.append(Interface("call", "ready_valid", "in", [p for i in ins for p in i.data], start, ready))
    if outs:
        rest.append(Interface("result", "valid", "out", [p for i in outs for p in i.data], done))
    return rest
