"""
Contract verifier for HLS/HDL IP interfaces.

Reads an ip_interface.yaml mapping spec and validates it against the raw
port metadata in ip_info.yaml:

  - required roles (clock_primary, reset_primary) are present
  - every scalar role's raw_port exists in the IP
  - direction and width match the spec
  - every array role's prefix resolves to at least ``count`` ports with
    the expected width

Usage (Python API)::

    from forge.contracts.contract_verifier import ContractVerifier

    verifier = ContractVerifier(ip_info_path, contract_path)
    issues = verifier.verify()
    ok = verifier.report()          # prints and returns True/False

Usage (CLI)::

    topgen verify-contract --ip-info ip_info.yaml \\
        --contract ips/dt_interface_ip/ip_interface.yaml

Exit codes: 0 = pass, 1 = warnings only, 2 = one or more errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .coordinates import coordinate_key, coordinate_label
from .cardinality import CardinalityError, parse_cardinality
from .contract_loader import INTERFACE_CONTRACT_SCHEMA_VERSION
from forge.core.schema_version import check_schema_version

_CANONICAL_ROLES_FILE = Path(__file__).parent / "canonical_roles.yaml"

# Roles that are unconditionally required on every IP.
_ALWAYS_REQUIRED = {"clock_primary", "reset_primary"}

# Documented built-in protocol values a role may declare. Optional;
# absent means unspecified, not a validation failure.
KNOWN_PROTOCOLS = frozenset({"combinational", "valid-only", "ready-valid", "fixed-frame"})

# Recognized interface-member names. A role opts into
# member grouping by declaring both `interface:` (the shared group name)
# and `member:` (its role within that group). See
# docs/IP_INTERFACE_POLICY.md "Interface members".
KNOWN_MEMBERS = frozenset({"data", "valid", "ready", "last", "metadata"})


@dataclass
class ContractIssue:
    severity: str   # 'error' | 'warning'
    role: str
    message: str

    def __str__(self) -> str:
        icon = "❌" if self.severity == "error" else "⚠️ "
        return f"  {icon} [{self.role}] {self.message}"


@dataclass
class VerifyResult:
    contract_path: Path
    ip_key: str
    module_name: str
    issues: List[ContractIssue] = field(default_factory=list)

    @property
    def errors(self) -> List[ContractIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[ContractIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def exit_code(self) -> int:
        if self.errors:
            return 2
        if self.warnings:
            return 1
        return 0

    def print_report(self, verbose: bool = False) -> None:
        if self.passed and not self.warnings:
            status = "✅ PASS"
        elif self.passed:
            status = "⚠️  PASS (with warnings)"
        else:
            status = "❌ FAIL"

        print(f"{status}  {self.module_name}  [{self.ip_key}]  ({self.contract_path.name})")
        for issue in self.issues:
            print(str(issue))
        if verbose and self.passed and not self.warnings:
            print("    (no issues)")


def load_canonical_role_vocab() -> Dict[str, Any]:
    """Load the ``roles:`` vocabulary from ``canonical_roles.yaml``.

    Shared by ``ContractVerifier`` and ``forge.ir.build`` (the canonical IR
    builder) so reserved-role status (e.g. ``clock_secondary``) is looked up
    from one place.
    """
    if _CANONICAL_ROLES_FILE.exists():
        raw = yaml.safe_load(_CANONICAL_ROLES_FILE.read_text()) or {}
        return raw.get("roles", {})
    return {}


class ContractVerifier:
    """Verify one ip_interface.yaml contract against ip_info.yaml raw port metadata."""

    def __init__(self, ip_info_path: Path, contract_path: Path) -> None:
        self.ip_info_path = Path(ip_info_path)
        self.contract_path = Path(contract_path)

        self._ip_info: Dict[str, Any] = yaml.safe_load(self.ip_info_path.read_text()) or {}
        self._contract: Dict[str, Any] = yaml.safe_load(self.contract_path.read_text()) or {}
        self._vocab: Dict[str, Any] = load_canonical_role_vocab()

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def verify(self) -> VerifyResult:
        """Run all checks and return a VerifyResult."""
        spec = self._contract.get("ip_interface", {})
        ip_key = spec.get("ip_info_key", "")
        module_name = spec.get("module_name", "<unknown>")

        result = VerifyResult(
            contract_path=self.contract_path,
            ip_key=ip_key,
            module_name=module_name,
        )

        def err(role: str, msg: str) -> None:
            result.issues.append(ContractIssue("error", role, msg))

        def warn(role: str, msg: str) -> None:
            result.issues.append(ContractIssue("warning", role, msg))

        # ── Structural checks ────────────────────────────────────────────────

        for issue in check_schema_version(
            spec.get("schema_version"), INTERFACE_CONTRACT_SCHEMA_VERSION, schema_name="interface contract",
        ):
            (err if issue.severity == "error" else warn)("(meta)", issue.message)

        if not ip_key:
            err("(meta)", "Missing 'ip_info_key' in contract")
            return result

        if ip_key not in self._ip_info:
            err("(meta)", f"ip_info_key '{ip_key}' not found in {self.ip_info_path.name}")
            return result

        roles_in_spec: Dict[str, Any] = spec.get("roles", {})
        if not roles_in_spec:
            warn("(meta)", "No roles defined in contract; nothing to verify")
            return result

        # Build flat port lookup from ip_info
        raw_ports: Dict[str, Dict[str, Any]] = {
            p["name"]: p
            for p in self._ip_info[ip_key].get("ports", [])
        }

        # ── Required roles ───────────────────────────────────────────────────

        clock_free = spec.get("clock_free", False)
        reset_free = spec.get("reset_free", False)
        # combinatorial: true is a convenience shorthand for clock_free + reset_free
        if spec.get("combinatorial", False):
            clock_free = True
            reset_free = True
        skip = set()
        if clock_free:
            skip.add("clock_primary")
        if reset_free:
            skip.add("reset_primary")
        effective_required = _ALWAYS_REQUIRED - skip

        for required_role in effective_required:
            if required_role not in roles_in_spec:
                err(required_role, f"Required role '{required_role}' is missing from the contract")

        # ── Per-role checks ──────────────────────────────────────────────────

        for role_name, role_spec in roles_in_spec.items():
            if "raw_port_tpl" in role_spec:
                self._check_nd_tpl_role(role_name, role_spec, raw_ports, err, warn)
            elif role_spec.get("array") or "raw_port_prefix" in role_spec:
                self._check_array_role(role_name, role_spec, raw_ports, err, warn)
            else:
                self._check_scalar_role(role_name, role_spec, raw_ports, err, warn)

            canonical = self._vocab.get(role_name)
            if canonical and canonical.get("status") == "reserved":
                warn(
                    role_name,
                    f"role '{role_name}' is RESERVED — declared for a future "
                    "release and has no functional effect in this version of "
                    "FORGE (no clock-domain model, no CDC validation, no "
                    "special wiring). See docs/IP_INTERFACE_POLICY.md "
                    "\"Reserved roles\".",
                )

            protocol = role_spec.get("protocol")
            if protocol is not None and protocol not in KNOWN_PROTOCOLS:
                err(
                    role_name,
                    f"role '{role_name}' declares unknown protocol {protocol!r} "
                    f"— must be one of {sorted(KNOWN_PROTOCOLS)}. See "
                    "docs/IP_INTERFACE_POLICY.md \"Protocol semantics\".",
                )

            member = role_spec.get("member")
            group = role_spec.get("interface")
            if member is not None and group is None:
                warn(
                    role_name,
                    f"role '{role_name}' declares 'member: {member}' without "
                    "'interface:' — it has no grouping effect on its own. See "
                    "docs/IP_INTERFACE_POLICY.md \"Interface members\".",
                )
            if member is not None and member not in KNOWN_MEMBERS:
                err(
                    role_name,
                    f"role '{role_name}' declares unknown member {member!r} "
                    f"— must be one of {sorted(KNOWN_MEMBERS)}. See "
                    "docs/IP_INTERFACE_POLICY.md \"Interface members\".",
                )

            try:
                parse_cardinality(role_spec, direction=role_spec.get("direction", ""))
            except CardinalityError as exc:
                err(role_name, f"role '{role_name}' has an invalid cardinality declaration: {exc}")

        # ── Interface-member group checks ───────────────────────────────────

        groups: Dict[str, Dict[str, List[str]]] = {}
        for role_name, role_spec in roles_in_spec.items():
            group = role_spec.get("interface")
            if group is None:
                continue
            member = role_spec.get("member", role_name)
            groups.setdefault(group, {}).setdefault(member, []).append(role_name)

        for group, members in groups.items():
            for member, role_names in members.items():
                if len(role_names) > 1:
                    err(
                        role_names[0],
                        f"interface group '{group}' declares member '{member}' "
                        f"more than once: {sorted(role_names)}. See "
                        "docs/IP_INTERFACE_POLICY.md \"Interface members\".",
                    )

        return result

    def report(self, verbose: bool = False) -> bool:
        """Verify, print a human-readable report, and return True if passed."""
        result = self.verify()
        result.print_report(verbose=verbose)
        return result.passed

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _check_scalar_role(
        self,
        role_name: str,
        role_spec: Dict[str, Any],
        raw_ports: Dict[str, Dict[str, Any]],
        err,
        warn,
    ) -> None:
        raw_port = role_spec.get("raw_port")
        if raw_port is None:
            err(role_name, "Scalar role is missing 'raw_port' field")
            return

        if raw_port not in raw_ports:
            err(role_name, f"raw_port '{raw_port}' does not exist in the IP")
            return

        port = raw_ports[raw_port]

        # Direction check
        expected_dir = str(role_spec.get("direction", "")).upper()
        actual_dir = str(port.get("direction", "")).upper()
        if expected_dir and actual_dir:
            # Normalise: the ip_info uses 'IN'/'OUT'; spec uses 'input'/'output'
            exp_norm = "IN" if expected_dir.startswith("IN") else "OUT"
            act_norm = "IN" if actual_dir.startswith("IN") else "OUT"
            if exp_norm != act_norm:
                err(
                    role_name,
                    f"raw_port '{raw_port}' direction is {actual_dir} "
                    f"but contract specifies {expected_dir}",
                )

        # Width check (only if spec provides a width)
        expected_width = role_spec.get("width")
        if expected_width is not None:
            actual_width = port.get("width")
            if actual_width != int(expected_width):
                err(
                    role_name,
                    f"raw_port '{raw_port}' width is {actual_width} "
                    f"but contract specifies {expected_width}",
                )

    def _check_array_role(
        self,
        role_name: str,
        role_spec: Dict[str, Any],
        raw_ports: Dict[str, Dict[str, Any]],
        err,
        warn,
    ) -> None:
        prefix = role_spec.get("raw_port_prefix")
        count = role_spec.get("count")

        if prefix is None:
            err(role_name, "Array role is missing 'raw_port_prefix' field")
            return
        if count is None:
            err(role_name, "Array role is missing 'count' field")
            return

        count = int(count)
        matching = [n for n in raw_ports if n.startswith(prefix)]

        if len(matching) < count:
            err(
                role_name,
                f"Expected {count} ports with prefix '{prefix}', "
                f"found {len(matching)} in the IP",
            )

        # Width check — verify all found ports (up to count) share the expected width
        expected_width = role_spec.get("width")
        if expected_width is not None:
            for port_name in sorted(matching)[:count]:
                actual_width = raw_ports[port_name].get("width")
                if actual_width != int(expected_width):
                    err(
                        role_name,
                        f"Array port '{port_name}' width is {actual_width} "
                        f"but contract specifies {expected_width}",
                    )
                    break  # one diagnostic per array role is sufficient

    def _check_nd_tpl_role(
        self,
        role_name: str,
        role_spec: Dict[str, Any],
        raw_ports: Dict[str, Dict[str, Any]],
        err,
        warn,
    ) -> None:
        tpl = role_spec.get("raw_port_tpl")
        dims = role_spec.get("dims")

        if tpl is None:
            err(role_name, "N-D template role is missing 'raw_port_tpl' field")
            return
        if dims is None:
            err(role_name, "N-D template role is missing 'dims' field")
            return

        # dims must be a list of positive integers
        if not isinstance(dims, list) or len(dims) == 0:
            err(role_name, f"'dims' must be a non-empty list of positive integers, got: {dims!r}")
            return
        for i, d in enumerate(dims):
            if not isinstance(d, int) or d <= 0:
                err(role_name, f"dims[{i}] must be a positive integer, got: {d!r}")
                return

        # Verify placeholder count matches dimensionality
        n_dims = len(dims)
        for i in range(n_dims):
            if f"{{{i}}}" not in tpl:
                err(
                    role_name,
                    f"raw_port_tpl '{tpl}' is missing placeholder {{{{{i}}}}} "
                    f"for {n_dims}-D dims",
                )
                return

        # wiring_kind should be present for generation-oriented N-D roles
        if not role_spec.get("wiring_kind"):
            warn(role_name, "N-D template role has no 'wiring_kind'; contract-driven wiring requires it")

        # Verify that at least one expanded port exists in ip_info
        first_idx = [0] * n_dims
        first_port = tpl.format(*first_idx)
        last_idx = [d - 1 for d in dims]
        last_port = tpl.format(*last_idx)

        first_ok = first_port in raw_ports
        last_ok = last_port in raw_ports

        if not first_ok and not last_ok:
            err(
                role_name,
                f"No expanded ports found in IP for template '{tpl}' with dims={dims}; "
                f"checked '{first_port}' and '{last_port}'",
            )
            return

        if not first_ok:
            warn(role_name, f"First expanded port '{first_port}' not found in IP (last '{last_port}' exists)")

        if not last_ok:
            warn(role_name, f"Last expanded port '{last_port}' not found in IP (first '{first_port}' exists)")

        # Direction check on the first found port
        check_port_name = first_port if first_ok else last_port
        expected_dir = str(role_spec.get("direction", "")).upper()
        actual_dir = str(raw_ports[check_port_name].get("direction", "")).upper()
        if expected_dir and actual_dir:
            exp_norm = "IN" if expected_dir.startswith("IN") else "OUT"
            act_norm = "IN" if actual_dir.startswith("IN") else "OUT"
            if exp_norm != act_norm:
                err(
                    role_name,
                    f"Template port '{check_port_name}' direction is {actual_dir} "
                    f"but contract specifies {expected_dir}",
                )

        # Width check on the first found port (if spec provides a width)
        expected_width = role_spec.get("width")
        if expected_width is not None:
            actual_width = raw_ports[check_port_name].get("width")
            if actual_width != int(expected_width):
                err(
                    role_name,
                    f"Template port '{check_port_name}' width is {actual_width} "
                    f"but contract specifies {expected_width}",
                )


# ─────────────────────────────────────────────────────────────────────────────
# Batch verification helpers (used by the CLI)
# ─────────────────────────────────────────────────────────────────────────────

def verify_contract_file(
    ip_info_path: Path,
    contract_path: Path,
    verbose: bool = False,
) -> VerifyResult:
    """Verify a single contract file. Convenience wrapper."""
    verifier = ContractVerifier(ip_info_path, contract_path)
    result = verifier.verify()
    if verbose:
        result.print_report(verbose=verbose)
    return result


def find_contracts(search_dirs: List[Path]) -> List[Path]:
    """
    Recursively find all interface contract YAML files under the given directories.

    Matches both legacy ``ip_interface*.yaml`` names and the preferred
    ``*.interface.yaml`` convention used in plugin source trees.
    """
    _PATTERNS = [
        "ip_interface*.yaml",
        "ip_interface*.yml",
        "*.interface.yaml",
        "*.interface.yml",
    ]
    contracts: List[Path] = []
    seen: set = set()

    for d in search_dirs:
        d = Path(d)
        if d.is_file() and d.suffix in (".yaml", ".yml"):
            if d not in seen:
                contracts.append(d)
                seen.add(d)
        elif d.is_dir():
            for pattern in _PATTERNS:
                for p in sorted(d.rglob(pattern)):
                    if p not in seen:
                        contracts.append(p)
                        seen.add(p)
    return contracts


def verify_all(
    ip_info_path: Path,
    search_dirs: List[Path],
    verbose: bool = False,
) -> List[VerifyResult]:
    """Find and verify all ip_interface.yaml contracts under search_dirs."""
    contracts = find_contracts(search_dirs)
    if not contracts:
        print(f"⚠️  No ip_interface*.yaml files found under: {[str(d) for d in search_dirs]}")
        return []

    results: List[VerifyResult] = []
    for contract_path in contracts:
        verifier = ContractVerifier(ip_info_path, contract_path)
        result = verifier.verify()
        result.print_report(verbose=verbose)
        results.append(result)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Topology group verification
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TopologyGroupIssue:
    severity: str  # 'error' | 'warning'
    group: str
    message: str

    def __str__(self) -> str:
        icon = "❌" if self.severity == "error" else "⚠️ "
        return f"  {icon} [{self.group}] {self.message}"


def verify_topology_groups(
    design_cfg: Any,
    contracts: Dict[str, Any],
) -> List[TopologyGroupIssue]:
    """
    Validate topology_groups entries against module definitions and contracts.

    Checks:
    - Both from/to modules exist in design
    - Both endpoints have loaded contracts
    - instance_assign ranges are non-overlapping and cover the producer instance count
    - instance_assign partition labels exist in the consumer contract
    - role_pairs reference valid role names
    - auto_match wiring_kind has at least one matching pair
    """
    issues: List[TopologyGroupIssue] = []
    module_map = {m.name: m for m in design_cfg.modules}

    def err(group_name: str, msg: str) -> None:
        issues.append(TopologyGroupIssue("error", group_name, msg))

    def warn(group_name: str, msg: str) -> None:
        issues.append(TopologyGroupIssue("warning", group_name, msg))

    for tg in design_cfg.topology_groups:
        gn = tg.name

        # Check modules exist
        src_mod = module_map.get(tg.from_)
        dst_mod = module_map.get(tg.to)
        if src_mod is None:
            err(gn, f"source module '{tg.from_}' not found in design")
            continue
        if dst_mod is None:
            err(gn, f"destination module '{tg.to}' not found in design")
            continue

        # Look up contracts
        src_key = src_mod.ip_info_key or tg.from_
        dst_key = dst_mod.ip_info_key or tg.to
        sc = contracts.get(src_key) or contracts.get(tg.from_)
        dc = contracts.get(dst_key) or contracts.get(tg.to)
        if sc is None:
            err(gn, f"no contract found for source '{tg.from_}' (key: {src_key})")
            continue
        if dc is None:
            err(gn, f"no contract found for destination '{tg.to}' (key: {dst_key})")
            continue

        # Validate instance_assign
        if tg.instance_assign:
            total_assigned = 0
            seen_ranges: list = []
            dst_roles = dc.get_connection_roles("input")
            wk_filter = tg.wiring_kind
            if wk_filter:
                dst_roles = [r for r in dst_roles if r.get("wiring_kind") == wk_filter]

            dst_coord_keys: set = set()
            seen_coord_labels: Dict[Any, str] = {}
            for r in dst_roles:
                ck = coordinate_key(r)
                if ck is None:
                    continue
                if ck in seen_coord_labels:
                    err(gn, f"duplicate coordinate '{coordinate_label(ck)}' "
                            f"declared by consumer roles '{seen_coord_labels[ck]}' "
                            f"and '{r['role_name']}'")
                else:
                    seen_coord_labels[ck] = r["role_name"]
                dst_coord_keys.add(ck)

            for ia in tg.instance_assign:
                start, end = ia.instances
                if start >= end:
                    err(gn, f"instance_assign range [{start}, {end}) is empty or inverted")
                    continue
                # Check overlap with previous ranges
                for prev_s, prev_e in seen_ranges:
                    if start < prev_e and end > prev_s:
                        err(gn, f"instance_assign range [{start}, {end}) overlaps with [{prev_s}, {prev_e})")
                        break
                seen_ranges.append((start, end))
                total_assigned += end - start

                ia_coord = ia.coordinate_key()
                if ia_coord not in dst_coord_keys:
                    err(gn, f"coordinate '{coordinate_label(ia_coord)}' not found in "
                            f"consumer contract (available: "
                            f"{sorted(coordinate_label(k) for k in dst_coord_keys)})")

            if total_assigned != src_mod.instances:
                warn(gn, f"instance_assign covers {total_assigned} instances "
                         f"but producer '{tg.from_}' has {src_mod.instances}")

        # Validate role_pairs
        if tg.role_pairs is not None:
            all_src = {r["role_name"] for r in sc.get_connection_roles("output")}
            all_dst = {r["role_name"] for r in dc.get_connection_roles("input")}
            # Also include roles without wiring_kind (accessible via has_role)
            all_src_names = {rn for rn in sc._roles if sc._roles[rn].get("direction", "") == "output"}
            all_dst_names = {rn for rn in dc._roles if dc._roles[rn].get("direction", "") == "input"}
            all_src |= all_src_names
            all_dst |= all_dst_names
            for src_rn, dst_rn in tg.role_pairs:
                if src_rn not in all_src:
                    err(gn, f"role_pairs source role '{src_rn}' not found in producer contract")
                if dst_rn not in all_dst:
                    err(gn, f"role_pairs destination role '{dst_rn}' not found in consumer contract")

        # Validate auto_match (no role_pairs, no instance_assign) has potential matches
        if tg.role_pairs is None and not tg.instance_assign and tg.wiring_kind:
            src_roles = sc.get_connection_roles("output")
            dst_roles = dc.get_connection_roles("input")
            src_wks = {r.get("wiring_kind") for r in src_roles}
            dst_wks = {r.get("wiring_kind") for r in dst_roles}
            if tg.wiring_kind not in src_wks:
                err(gn, f"wiring_kind '{tg.wiring_kind}' not found in any source output role")
            if tg.wiring_kind not in dst_wks:
                err(gn, f"wiring_kind '{tg.wiring_kind}' not found in any destination input role")

    return issues
