"""A board's control and status registers, declared once and exported per framework.

A register map (YAML) lists typed registers::

    registers:
      - name: control
        kind: config            # config | status | counter | snapshot | latency_monitor
        width: 64
        reset: 0
        clock_domain: logic_clk
        port: cfg_control       # config: the algorithm input it drives
        fields:
          - {name: enable, bits: 0}
          - {name: mode, bits: [3, 1]}
      - name: lock
        kind: status
        width: 32
        net: lock_status        # status kinds: the payload net read back
        fields:
          - {name: locked, bits: 0}

``config`` registers are read-write; every other kind is read-only. A counter
may declare ``clear_on_read``; a register wider than the bus word that must be
read in one piece declares ``atomic`` and is refused by an export whose bus
cannot give that.

Exports: an IPbus (uHAL) address table, the Blobfish payload services that
place the registers in an SLR's config and readback words
(:mod:`forge.integration.payload_generator`), and a Markdown table. FORGE does
not talk to hardware: :class:`FakeBus` gives a map's read/write semantics to
the tests of whatever reads the exports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple
from xml.sax.saxutils import quoteattr

KINDS = ("config", "status", "counter", "snapshot", "latency_monitor")


@dataclass(frozen=True)
class Field:
    name: str
    hi: int
    lo: int
    description: str = ""

    @property
    def width(self) -> int:
        return self.hi - self.lo + 1

    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.lo


@dataclass
class Register:
    name: str
    kind: str
    width: int
    reset: int = 0
    clock_domain: str = ""
    description: str = ""
    port: str = ""
    net: str = ""
    clear_on_read: bool = False
    atomic: bool = False
    fields: List[Field] = field(default_factory=list)

    @property
    def writable(self) -> bool:
        return self.kind == "config"

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Register":
        name = str(d["name"])
        kind = str(d.get("kind", ""))
        if kind not in KINDS:
            raise ValueError(f"register {name}: kind must be one of {', '.join(KINDS)}, got {kind!r}")
        width = int(d.get("width", 32))
        if width < 1:
            raise ValueError(f"register {name}: width must be positive")
        fields = []
        for f in d.get("fields") or []:
            bits = f["bits"]
            hi, lo = (int(bits), int(bits)) if isinstance(bits, int) else (int(bits[0]), int(bits[1]))
            if not 0 <= lo <= hi < width:
                raise ValueError(f"register {name}.{f['name']}: bits [{hi}:{lo}] outside a {width}-bit register")
            fields.append(Field(str(f["name"]), hi, lo, str(f.get("description", ""))))
        for i, a in enumerate(fields):
            for b in fields[i + 1:]:
                if a.mask() & b.mask():
                    raise ValueError(f"register {name}: fields {a.name} and {b.name} overlap")
        reset = int(d.get("reset", 0))
        if not 0 <= reset < 1 << width:
            raise ValueError(f"register {name}: reset 0x{reset:x} does not fit {width} bits")
        if d.get("clear_on_read") and kind != "counter":
            raise ValueError(f"register {name}: only a counter clears on read")
        reg = cls(name, kind, width, reset, str(d.get("clock_domain", "")), str(d.get("description", "")),
                  str(d.get("port", "")), str(d.get("net", "")), bool(d.get("clear_on_read", False)),
                  bool(d.get("atomic", False)), fields)
        if reg.writable and reg.net:
            raise ValueError(f"register {name}: a config register drives a port, it reads no net")
        if not reg.writable and reg.port:
            raise ValueError(f"register {name}: a {kind} register reads a net, it drives no port")
        return reg


@dataclass
class RegisterMap:
    registers: List[Register]

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "RegisterMap":
        regs = [Register.from_dict(r) for r in d.get("registers") or []]
        names = [r.name for r in regs]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"registers declared twice: {', '.join(dup)}")
        return cls(regs)

    def layout(self, word_bits: int) -> List[Tuple[Register, int, int]]:
        """(register, first word, words) in declaration order, each starting a word."""
        out, at = [], 0
        for r in self.registers:
            n = -(-r.width // word_bits)
            out.append((r, at, n))
            at += n
        return out


def _check_bus(regs: RegisterMap, word_bits: int, bus: str) -> None:
    for r in regs.registers:
        if r.width > word_bits and r.atomic:
            raise ValueError(f"{bus}: {r.name} is atomic and {r.width} bits; the bus reads {word_bits} at a time")
        for f in r.fields:
            if f.lo // word_bits != f.hi // word_bits:
                raise ValueError(f"{bus}: {r.name}.{f.name} [{f.hi}:{f.lo}] crosses a {word_bits}-bit word")


def to_ipbus_xml(regs: RegisterMap, *, top: str = "payload", base: int = 0) -> str:
    """A uHAL address table: one 32-bit word per node, fields as masked children."""
    _check_bus(regs, 32, "IPbus")
    lines = ['<?xml version="1.0" encoding="ISO-8859-1"?>',
             "<!-- Written by forge (forge.integration.registers); do not edit. -->",
             f'<node id={quoteattr(top)} address="0x{base:08x}">']
    for r, first, n in regs.layout(32):
        perm = "rw" if r.writable else "r"
        for k in range(n):
            nid = r.name if n == 1 else f"{r.name}_w{k}"
            desc = f" description={quoteattr(r.description)}" if r.description and k == 0 else ""
            fields = [f for f in r.fields if f.lo // 32 == k]
            attrs = f'id={quoteattr(nid)} address="0x{first + k:08x}" permission="{perm}"{desc}'
            if not fields:
                lines.append(f"  <node {attrs}/>")
                continue
            lines.append(f"  <node {attrs}>")
            for f in fields:
                mask = f.mask() >> (32 * k)
                fd = f" description={quoteattr(f.description)}" if f.description else ""
                lines.append(f'    <node id={quoteattr(f.name)} mask="0x{mask:08x}"{fd}/>')
            lines.append("  </node>")
    lines.append("</node>")
    return "\n".join(lines) + "\n"


def to_blobfish(regs: RegisterMap, *, slr: int, config_port: Optional[str] = None,
                readback_port: Optional[str] = None) -> Dict[str, Any]:
    """Payload services placing the map in SLR *slr*'s 64-bit config and readback words.

    Config registers take config words in declaration order, one word each (the
    algorithm port gets the whole word); the read-only kinds fill the readback
    words in order, each zero-padded to its word.
    """
    _check_bus(regs, 64, "Blobfish")
    config_port = config_port or f"slr{slr}_config_registers_in"
    readback_port = readback_port or f"slr{slr}_readback_registers_out"
    config_words: Dict[str, Any] = {}
    status: List[Dict[str, Any]] = []
    table = []
    cfg_word = rb_word = 0
    for r in regs.registers:
        if r.width > 64:
            raise ValueError(f"Blobfish: {r.name} is {r.width} bits; one register is one 64-bit word")
        if r.writable:
            if not r.port:
                raise ValueError(f"Blobfish: config register {r.name} names no algorithm port")
            config_words[r.port] = {"register": config_port, "word": cfg_word}
            table.append({"register": r.name, "port": config_port, "word": cfg_word})
            cfg_word += 1
        else:
            if not r.net:
                raise ValueError(f"Blobfish: {r.kind} register {r.name} names no net")
            status.append({"net": r.net, "width": r.width})
            if r.width < 64:
                status.append({"zero": 64 - r.width})
            table.append({"register": r.name, "port": readback_port, "word": rb_word})
            rb_word += 1
    services: Dict[str, Any] = {}
    if config_words:
        services["config_words"] = config_words
    if status:
        services["status_words"] = {readback_port: status}
    return {"control_policies": services, "words": table}


def to_markdown(regs: RegisterMap, *, word_bits: int = 32) -> str:
    rows = [f"| Register | Word ({word_bits}-bit) | Kind | Access | Width | Reset | Clock | Fields |",
            "|---|---|---|---|---|---|---|---|"]
    for r, first, n in regs.layout(word_bits):
        words = str(first) if n == 1 else f"{first}-{first + n - 1}"
        access = "rw" if r.writable else ("r, clears on read" if r.clear_on_read else "r")
        fields = ", ".join(f"{f.name} [{f.hi}:{f.lo}]" if f.hi != f.lo else f"{f.name} [{f.lo}]"
                           for f in r.fields) or "-"
        rows.append(f"| `{r.name}` | {words} | {r.kind} | {access} | {r.width} | 0x{r.reset:x} | "
                    f"{r.clock_domain or '-'} | {fields} |")
    return "\n".join(rows) + "\n"


class FakeBus:
    """A register map's bus semantics without hardware, word-addressed like the export."""

    def __init__(self, regs: RegisterMap, word_bits: int = 32) -> None:
        self.word_bits = word_bits
        self._where: Dict[int, Tuple[Register, int]] = {}
        self.values: Dict[str, int] = {}
        for r, first, n in regs.layout(word_bits):
            self.values[r.name] = r.reset
            for k in range(n):
                self._where[first + k] = (r, k)

    def _at(self, address: int) -> Tuple[Register, int]:
        if address not in self._where:
            raise KeyError(f"no register at word 0x{address:x}")
        return self._where[address]

    def read(self, address: int) -> int:
        r, k = self._at(address)
        mask = (1 << self.word_bits) - 1
        word = (self.values[r.name] >> (self.word_bits * k)) & mask
        if r.clear_on_read:
            self.values[r.name] &= ~(mask << (self.word_bits * k))
        return word

    def write(self, address: int, value: int) -> None:
        r, k = self._at(address)
        if not r.writable:
            raise PermissionError(f"{r.name} is a {r.kind} register, read-only")
        mask = (1 << self.word_bits) - 1
        shift = self.word_bits * k
        full = (1 << r.width) - 1
        self.values[r.name] = ((self.values[r.name] & ~(mask << shift)) | ((value & mask) << shift)) & full

    def hardware_sets(self, name: str, value: int) -> None:
        """What the firmware drives into a read-only register."""
        self.values[name] = value
