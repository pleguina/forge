"""``forge.yml`` — the small file a user actually writes.

A FORGE project's authored surface is currently three files
(``modules.yml``, ``design.yml``, one ``*.interface.yaml`` per module) whose
combined length across this repository's reference plugins runs into the
hundreds of lines, most of it restating what the sources already declare.
The plan's Phase B replaces the *authored* part of that with one concise
file and moves the expanded form under ``.forge/``, generated.

This module is that file's model. It only holds what genuinely cannot be
derived from sources: where the sources are, which module is the top, which
port is the clock and how fast it runs, which port is the reset and at which
level, the target part, and where the verification dataset lives. Everything
else is expanded into ``.forge/project/`` by
:mod:`forge.project.adopt`.

Round-tripping matters here: :meth:`ForgeConfig.to_yaml` writes a file that
:meth:`ForgeConfig.load` reads back identically, because ``forge adopt``
writes it and every later command reads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from forge.project.paths import ROOT_CONFIG_NAME, ProjectPaths

#: Bumped when the shape of ``forge.yml`` changes incompatibly. Written into
#: every generated file so ``forge migrate`` has something to migrate from.
FORGE_YML_SCHEMA_VERSION = "1.0"

#: Used when the user names no part. An UltraScale+ device large enough that
#: an unsized design fits, matching what the scaffolders already default to
#: — so a project adopted without a part still validates and builds.
DEFAULT_PART = "xcvu9p-flga2104-2L-e"

#: Nanoseconds. 250 MHz — a middle-of-the-road default that is honest about
#: being a placeholder rather than implying a timing decision was made.
DEFAULT_CLOCK_PERIOD_NS = 4.0

_FREQUENCY_RE = re.compile(r"^\s*(?P<value>[\d.]+)\s*(?P<unit>[gmk]?hz)\s*$", re.I)
_UNIT_HZ = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}


def parse_frequency_to_period_ns(frequency: str) -> float:
    """``"360MHz"`` -> ``2.7778`` ns.

    Accepts Hz/kHz/MHz/GHz in any case, with or without a space. Raises
    ``ValueError`` on anything else rather than silently defaulting — a
    mistyped clock frequency that quietly became 250 MHz would be a timing
    bug that never announces itself.
    """
    match = _FREQUENCY_RE.match(str(frequency))
    if not match:
        raise ValueError(
            f"cannot read {frequency!r} as a frequency — expected a value and a "
            f"unit, e.g. '360MHz'"
        )
    value = float(match.group("value"))
    if value <= 0:
        raise ValueError(f"clock frequency must be positive, got {frequency!r}")
    hertz = value * _UNIT_HZ[match.group("unit").lower()]
    return round(1e9 / hertz, 6)


@dataclass
class ClockSpec:
    """The design's functional clock."""

    port: str = "clk"
    frequency: Optional[str] = None
    period_ns: Optional[float] = None

    def resolved_period_ns(self) -> float:
        """Period in ns, from whichever of the two spellings was given.

        ``frequency`` wins when both are present — it is the one a hardware
        engineer states, and letting a stale ``period_ns`` override it would
        be the wrong direction to resolve a contradiction.
        """
        if self.frequency:
            return parse_frequency_to_period_ns(self.frequency)
        if self.period_ns:
            return float(self.period_ns)
        return DEFAULT_CLOCK_PERIOD_NS

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"port": self.port}
        if self.frequency:
            out["frequency"] = self.frequency
        if self.period_ns is not None and not self.frequency:
            out["period_ns"] = self.period_ns
        return out


@dataclass
class ResetSpec:
    """The design's reset, and the level at which it asserts."""

    port: str = "rst"
    active: str = "high"  # "high" | "low"

    @property
    def active_low(self) -> bool:
        return self.active.lower() == "low"

    def to_dict(self) -> Dict[str, Any]:
        return {"port": self.port, "active": self.active}


#: The two ways FORGE can treat a module.
MANAGEMENT_MANAGED = "managed"
#: Integrated structurally, but not modelled internally. FORGE knows its
#: external ports, its primary clock/reset and how it connects; it does not
#: claim to understand its internal clock structure or implementation. The
#: escape hatch for vendor IP, encrypted blocks, and anything whose internals
#: are outside FORGE's current envelope.
MANAGEMENT_OPAQUE = "opaque"
MANAGEMENT_KINDS = (MANAGEMENT_MANAGED, MANAGEMENT_OPAQUE)


@dataclass
class DeclaredConnection:
    """A producer/consumer pair the user has decided on.

    The one answer discovery cannot reach on its own: when several inputs
    match an output equally well, or several outputs match one input, FORGE
    refuses to guess (see :mod:`forge.project.discovery`) and asks. This is
    where the answer lives — in the file the user owns, so it survives every
    re-adoption, is reversible with an editor, and could equally well have
    been written by hand without ever running the question.
    """

    producer: str  # "module.port"
    consumer: str  # "module.port"

    def to_dict(self) -> Dict[str, Any]:
        return {"from": self.producer, "to": self.consumer}


def _endpoint(value: Any, *, field_name: str, source: Any) -> str:
    """Validate a ``module.port`` endpoint spelling."""
    text = str(value).strip()
    if text.count(".") != 1 or not all(part.strip() for part in text.split(".")):
        raise ValueError(
            f"{source}: connection {field_name}: {value!r} must be written "
            f"'module.port' (e.g. 'decoder.data_out')"
        )
    return text


@dataclass
class ModulePolicy:
    """How the user wants one specific module handled.

    Everything else about a module is derived from its source; these are the
    facts and decisions no port list can state.
    """

    management: str = MANAGEMENT_MANAGED
    #: For an HLS module whose source defines several functions and none is
    #: named after the file: which one is the kernel top. Absent for every
    #: module whose top its source already proves.
    top: Optional[str] = None
    #: The module registry's ``latency:`` block, e.g. ``{kind: fixed,
    #: cycles: 3}``. ``latency: 3`` in ``forge.yml`` is read as fixed.
    latency: Optional[Dict[str, Any]] = None
    #: A user-maintained interface contract, relative to the project root,
    #: used instead of the one inferred from the module's ports.
    contract: Optional[str] = None

    @property
    def opaque(self) -> bool:
        return self.management == MANAGEMENT_OPAQUE

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"management": self.management}
        if self.top:
            out["top"] = self.top
        if self.latency is not None:
            if self.latency.get("kind") == "fixed" and set(self.latency) == {"kind", "cycles"}:
                out["latency"] = self.latency["cycles"]
            else:
                out["latency"] = dict(self.latency)
        if self.contract:
            out["contract"] = self.contract
        return out


#: ``build:`` keys in ``forge.yml``, each the ``forge build`` option of the
#: same name. The path-valued ones resolve against the project root.
BUILD_PATH_OPTIONS = (
    "hls_build_root", "ip_root", "ip_info", "build_dir", "system", "rtl_resource_root",
)
BUILD_OPTIONS = ("mode", "top_name") + BUILD_PATH_OPTIONS


def _latency(value: Any, *, module: str, source: Any) -> Dict[str, Any]:
    """Normalise a ``modules.<name>.latency`` entry to the registry's form."""
    from forge.contracts.config import LatencyDeclaration

    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        block: Dict[str, Any] = {"kind": "fixed", "cycles": value}
    elif isinstance(value, dict):
        block = dict(value)
    else:
        raise ValueError(
            f"{source}: module {module!r} latency must be a cycle count "
            f"(e.g. `latency: 3`) or a mapping such as "
            f"`latency: {{kind: bounded, min_cycles: 2, max_cycles: 4}}`"
        )
    try:
        LatencyDeclaration(**block)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source}: module {module!r} latency: {exc}") from exc
    return block


@dataclass(frozen=True)
class ProjectDecisions:
    """The answers a project has already given, as discovery sees them.

    A decision recorded here means the corresponding question is *settled*:
    discovery uses the answer and stops asking. That is what makes an
    interactive answer indistinguishable from a hand-edited one — both end
    up in ``forge.yml``, and this is the only channel either travels.

    Every field is optional. An empty ``ProjectDecisions`` is a project that
    has decided nothing, which is exactly a first ``forge adopt``.
    """

    opaque_modules: "frozenset" = frozenset()
    clock_port: Optional[str] = None
    reset_port: Optional[str] = None
    top: Optional[str] = None
    #: ``(producer, consumer)`` endpoint pairs, each ``"module.port"``.
    connections: "tuple" = ()
    #: Module name -> the HLS kernel top function the user named.
    hls_tops: Dict[str, str] = field(default_factory=dict)

    def declares_connection(self, producer: str, consumer: str) -> bool:
        return (producer, consumer) in self.connections

    def connections_from(self, producer: str) -> List[str]:
        return [c for p, c in self.connections if p == producer]

    def connections_to(self, consumer: str) -> List[str]:
        return [p for p, c in self.connections if c == consumer]


@dataclass
class ForgeConfig:
    """The parsed contents of one ``forge.yml``.

    Attributes:
        project: Project name. Used for generated-artifact naming only.
        root: Directory the file lives in — every relative path resolves
            against it, so a project stays movable and clonable.
        rtl_globs / hls_globs: Where the user's sources are, as globs
            relative to ``root``.
        top: The user's existing top level, if any. ``None`` means FORGE
            generates the only top level there is.
        clock / reset: See :class:`ClockSpec` / :class:`ResetSpec`.
        part: Target device.
        dataset: Verification dataset path, when one is configured.
        verification_config: The user's ``design.verification.yml``,
            relative to ``root``, which ``forge test`` runs.
        design: A hand-written ``design.yml``, relative to ``root``. When
            set, it is the design every command uses as it stands, and
            nothing is derived from the sources.
        registry: The ``modules.yml`` that goes with ``design``; by default
            the one its ``registry:`` key names.
        build: ``forge build`` options, by name (:data:`BUILD_OPTIONS`).
        modules: Per-module handling, by module name. Only modules the user
            has said something about appear; everything else is managed.
        connections: Producer/consumer pairs the user has decided on, for
            the cases discovery refuses to resolve by guessing.
        schema_version: Version of this file's own shape.

    Every field here that answers a question FORGE asked is *user intent*:
    ``forge adopt`` regenerates everything under ``.forge/`` on each run and
    carries these through unchanged, so an answer given once is never
    undone by re-adopting.
    """

    project: str
    root: Path
    rtl_globs: List[str] = field(default_factory=list)
    hls_globs: List[str] = field(default_factory=list)
    top: Optional[str] = None
    clock: ClockSpec = field(default_factory=ClockSpec)
    reset: ResetSpec = field(default_factory=ResetSpec)
    part: str = DEFAULT_PART
    dataset: Optional[str] = None
    verification_config: Optional[str] = None
    #: The rest of ``verification:`` (input, expected, links, compare,
    #: protocols, backends, levels, modules), read by
    #: ``forge.verification.auto``.
    verification_spec: Dict[str, Any] = field(default_factory=dict)
    design: Optional[str] = None
    registry: Optional[str] = None
    build: Dict[str, str] = field(default_factory=dict)
    modules: Dict[str, ModulePolicy] = field(default_factory=dict)
    #: Connections the user has decided on, where discovery found more than
    #: one equally valid reading. See :class:`DeclaredConnection`.
    connections: List[DeclaredConnection] = field(default_factory=list)
    schema_version: str = FORGE_YML_SCHEMA_VERSION

    @property
    def authored(self) -> bool:
        """Whether the project names its own design rather than being derived."""
        return self.design is not None

    @property
    def opaque_modules(self) -> "frozenset":
        """Modules the user has asked FORGE not to model internally."""
        return frozenset(
            name for name, policy in self.modules.items() if policy.opaque
        )

    # ── Load / save ───────────────────────────────────────────────────────

    @classmethod
    def load(cls, path: "Path | str") -> "ForgeConfig":
        """Read a ``forge.yml``.

        Raises ``FileNotFoundError`` when it is not there and ``ValueError``
        when it is unreadable — the two exceptions
        ``forge.core.cli.envelope.status_for_exception`` classifies as the
        user's input being wrong (exit 1), never as FORGE falling over.
        """
        import yaml

        config_path = Path(path).expanduser().resolve()
        if config_path.is_dir():
            config_path = config_path / ROOT_CONFIG_NAME
        if not config_path.is_file():
            raise FileNotFoundError(f"no {ROOT_CONFIG_NAME} at {config_path}")

        raw = yaml.safe_load(config_path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{config_path} must contain a YAML mapping")

        sources = raw.get("sources") or {}
        if not isinstance(sources, dict):
            raise ValueError(f"{config_path}: 'sources' must be a mapping of rtl/hls globs")

        clock_raw = raw.get("clock") or {}
        reset_raw = raw.get("reset") or {}
        verification_raw = raw.get("verification") or {}

        modules_raw = raw.get("modules") or {}
        if not isinstance(modules_raw, dict):
            raise ValueError(
                f"{config_path}: 'modules' must be a mapping of module name to "
                f"its handling, e.g. `axi_bridge: {{management: opaque}}`"
            )
        modules: Dict[str, ModulePolicy] = {}
        for name, body in modules_raw.items():
            fields = body if isinstance(body, dict) else {}
            management = str(fields.get("management") or MANAGEMENT_MANAGED)
            if management not in MANAGEMENT_KINDS:
                raise ValueError(
                    f"{config_path}: module {name!r} declares management "
                    f"{management!r}; must be one of {MANAGEMENT_KINDS}"
                )
            top = fields.get("top")
            contract = fields.get("contract")
            modules[str(name)] = ModulePolicy(
                management=management,
                top=str(top) if top else None,
                latency=(
                    _latency(fields["latency"], module=str(name), source=config_path)
                    if fields.get("latency") is not None else None
                ),
                contract=str(contract) if contract else None,
            )

        build_raw = raw.get("build") or {}
        if not isinstance(build_raw, dict):
            raise ValueError(f"{config_path}: 'build' must be a mapping of forge build options")
        unknown = sorted(set(build_raw) - set(BUILD_OPTIONS))
        if unknown:
            raise ValueError(
                f"{config_path}: unknown build option(s) {', '.join(unknown)}; "
                f"known: {', '.join(BUILD_OPTIONS)}"
            )

        connections_raw = raw.get("connections") or []
        if not isinstance(connections_raw, list):
            raise ValueError(
                f"{config_path}: 'connections' must be a list of "
                f"`{{from: module.port, to: module.port}}` entries"
            )
        connections: List[DeclaredConnection] = []
        for entry in connections_raw:
            if not isinstance(entry, dict) or "from" not in entry or "to" not in entry:
                raise ValueError(
                    f"{config_path}: every connections entry needs 'from' and "
                    f"'to', e.g. `- {{from: decoder.data_out, to: packer.data_in}}`"
                )
            connections.append(DeclaredConnection(
                producer=_endpoint(entry["from"], field_name="from", source=config_path),
                consumer=_endpoint(entry["to"], field_name="to", source=config_path),
            ))

        return cls(
            project=str(raw.get("project") or config_path.parent.name),
            root=config_path.parent,
            rtl_globs=[str(g) for g in (sources.get("rtl") or [])],
            hls_globs=[str(g) for g in (sources.get("hls") or [])],
            top=str(raw["top"]) if raw.get("top") else None,
            clock=ClockSpec(
                port=str(clock_raw.get("port") or "clk"),
                frequency=str(clock_raw["frequency"]) if clock_raw.get("frequency") else None,
                period_ns=float(clock_raw["period_ns"]) if clock_raw.get("period_ns") else None,
            ),
            reset=ResetSpec(
                port=str(reset_raw.get("port") or "rst"),
                active=str(reset_raw.get("active") or "high"),
            ),
            part=str(raw.get("part") or DEFAULT_PART),
            dataset=str(verification_raw["dataset"]) if verification_raw.get("dataset") else None,
            verification_config=(
                str(verification_raw["config"]) if verification_raw.get("config") else None
            ),
            verification_spec={str(k): v for k, v in verification_raw.items() if k not in ("dataset", "config")},
            design=str(raw["design"]) if raw.get("design") else None,
            registry=str(raw["registry"]) if raw.get("registry") else None,
            build={str(k): str(v) for k, v in build_raw.items() if v is not None},
            modules=modules,
            connections=connections,
            schema_version=str(raw.get("schema_version") or FORGE_YML_SCHEMA_VERSION),
        )

    def to_dict(self) -> Dict[str, Any]:
        """The mapping written to ``forge.yml``.

        Keys are omitted rather than written empty: an absent
        ``verification`` block reads as "not configured yet", which is what
        ``forge check`` reports, while ``verification: {dataset: null}``
        would read as a broken configuration.
        """
        out: Dict[str, Any] = {
            "schema_version": self.schema_version,
            "project": self.project,
        }
        if self.design:
            # The part, clock and reset are the design's own.
            out["design"] = self.design
            if self.registry:
                out["registry"] = self.registry
            if self.build:
                out["build"] = dict(self.build)
        else:
            out["sources"] = {}
            if self.rtl_globs:
                out["sources"]["rtl"] = list(self.rtl_globs)
            if self.hls_globs:
                out["sources"]["hls"] = list(self.hls_globs)
            if self.build:
                out["build"] = dict(self.build)
            if self.top:
                out["top"] = self.top
            out["part"] = self.part
            out["clock"] = self.clock.to_dict()
            out["reset"] = self.reset.to_dict()
        verification: Dict[str, Any] = {}
        if self.dataset:
            verification["dataset"] = self.dataset
        if self.verification_config:
            verification["config"] = self.verification_config
        verification.update(self.verification_spec)
        if verification:
            out["verification"] = verification
        if self.modules:
            out["modules"] = {
                name: policy.to_dict() for name, policy in sorted(self.modules.items())
            }
        if self.connections:
            out["connections"] = [c.to_dict() for c in self.connections]
        return out

    def to_yaml(self) -> str:
        """``forge.yml`` as text, with the comments a new reader needs.

        Written by hand rather than dumped, because the whole point of this
        file is that a human maintains it: the comments say what each field
        controls and which ones FORGE guessed.
        """
        import yaml

        body = yaml.safe_dump(self.to_dict(), sort_keys=False, default_flow_style=False)
        if self.authored:
            return (
                f"# {self.project} — FORGE project\n"
                f"#\n"
                f"# The design is the hand-written file `design:` names, used as it\n"
                f"# stands; FORGE writes everything it generates under .forge/.\n"
                f"#\n"
                f"#   forge check    what is missing or inconsistent\n"
                f"#   forge build    generate the structural top level\n"
                f"#   forge test     simulate it against verification.config\n"
                f"#   forge report   collect the results\n"
                f"#\n"
            ) + body
        header = (
            f"# {self.project} — FORGE project\n"
            f"#\n"
            f"# Your decisions about the design live here, or in files this one\n"
            f"# names. Everything FORGE derives from it and from the sources —\n"
            f"# the module registry, the design topology and the inferred\n"
            f"# interface contracts — is written under .forge/ and can be\n"
            f"# deleted at any time; every command regenerates it.\n"
            f"#\n"
            f"#   forge check    what is missing or inconsistent\n"
            f"#   forge build    generate the structural top level\n"
            f"#   forge test     simulate it against verification.config\n"
            f"#   forge report   collect the results\n"
            f"#\n"
            f"# Per module: `modules: {{<name>: {{latency: 3}}}}` declares a fixed\n"
            f"# latency in cycles; `contract: <path>` uses your own interface\n"
            f"# contract instead of the inferred one.\n"
            f"#\n"
        )
        return header + body

    def save(self, path: Optional["Path | str"] = None) -> Path:
        """Write ``forge.yml`` and return where it went."""
        target = Path(path) if path else self.root / ROOT_CONFIG_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_yaml())
        return target

    # ── Resolution ────────────────────────────────────────────────────────

    def decisions(self) -> "ProjectDecisions":
        """Every answer this file gives to a question discovery would ask.

        Discovery consults exactly this and nothing else about the user —
        one object, so "which decisions can a project record?" has a single
        answer, and adding a new answerable question means adding a field
        here rather than another parameter threaded through every caller.
        """
        return ProjectDecisions(
            opaque_modules=self.opaque_modules,
            clock_port=self.clock.port,
            reset_port=self.reset.port,
            top=self.top,
            connections=tuple((c.producer, c.consumer) for c in self.connections),
            hls_tops={
                name: policy.top
                for name, policy in self.modules.items()
                if policy.top
            },
        )

    def paths(self) -> ProjectPaths:
        """The :class:`ProjectPaths` this project resolves to."""
        return ProjectPaths.for_root(
            self.root,
            source_roots=sorted({_glob_root(g) for g in self.rtl_globs + self.hls_globs}),
            config=self.root / ROOT_CONFIG_NAME,
        )

    def resolve_sources(self, kind: str = "rtl") -> List[Path]:
        """Every existing file matching this project's globs for *kind*.

        Sorted and de-duplicated so two overlapping globs cannot make a file
        appear twice, and so generated configuration is byte-stable across
        runs (the plan's reproducibility requirement).
        """
        globs = self.rtl_globs if kind == "rtl" else self.hls_globs
        found: List[Path] = []
        for pattern in globs:
            for path in sorted(self.root.glob(pattern)):
                if path.is_file() and path not in found:
                    found.append(path)
        return found

    def clock_period_ns(self) -> float:
        return self.clock.resolved_period_ns()


def _glob_root(pattern: str) -> str:
    """The fixed directory prefix of a glob (``rtl/**/*.sv`` -> ``rtl``).

    Used to derive source roots for :class:`ProjectPaths`. A pattern with no
    fixed prefix yields ``"."`` — the project root, which is correct: it
    really can match anywhere.
    """
    parts: List[str] = []
    for part in Path(pattern).parts:
        if any(ch in part for ch in "*?["):
            break
        parts.append(part)
    return str(Path(*parts)) if parts else "."
