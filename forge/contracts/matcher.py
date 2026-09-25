"""
Automatic port matcher for hls-auto.

Highlights
──────────
* Supports templated prefixes (``src_tpl`` / ``dst_tpl``) and N-D grids.
* Backwards-compatible 1-D prefix mapping.
* Detects the pattern “scalar port fanning-out across several module instances”.
* Refuses to create two drivers for the same sink pin (first match wins).
* Robust: never fabricates non-existent port names.
* NEW: Optionally parses system.yml (framework/links) and treats those
  algorithm ports as external to avoid accidental auto-wiring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

import yaml
from .config import DesignConfig, Module
from .topology_deriver import derive_topology_group


@dataclass
class RejectedMatch:
    """A candidate pairing that was considered and semantically rejected
    *before* any wire was ever proposed — i.e. no ``ResolvedConnection``
    exists to hang this evidence on, unlike ``MatchReport.rejected_fanin``
    (which records losing candidates for a sink pin that *did* get wired
    to someone else)."""
    module_pair: Tuple[str, str]        # (src_module_name, dst_module_name)
    scope: str                          # 'contract_role' | 'auto_match_group'
    src_ref: Optional[str] = None       # wiring_kind, or src group base-name
    dst_ref: Optional[str] = None       # wiring_kind, or dst group base-name (None when nothing on the dst side qualified at all)
    reason: str = ""


@dataclass
class MatchReport:
    """Summary of the port-matching run.

    ``contract_driven_modules``:  modules whose clock/reset was wired from a
        contract's raw_port declaration (authoritative).
    ``compat_mode_modules``:  modules that were wired via heuristic name
        scanning because no contract was available.
    ``contract_wired_roles``: list of (module_name, role, raw_port) tuples,
        one per successfully contract-wired role.
    ``warnings``: non-fatal issues (e.g. contract port not found in ip_info).
    ``wiring_method_counts``: per-connection wiring method tally
        (keys: ``contract_wiring``, ``port_map_ranges``, ``port_map``, ``auto_match``).
    ``connection_evidence``: per-pin-pair wiring method, keyed by
        ``(src_instance, src_port, dst_instance, dst_port)``. One entry per
        physical connection actually recorded in the returned ``conn_map``
        (global-net clock/reset fan-out is not included — it's tracked
        separately via ``contract_wired_roles``/``compat_mode_modules``).
        A design-level ``connections:``/``topology_groups:`` entry that
        expands into several instance pairs and pin pairs attributes the
        same wiring-method label to every pair it produced — matching the
        existing per-connection classification in ``wiring_method_counts``,
        not a finer-grained per-pin classification. ``rejected_matches``/
        ``gather_scatter_evidence`` below are part of this same
        matching-evidence effort.
    ``rejected_fanin``: sinks for which more than one candidate pin wanted
        to drive them. Keyed by ``(dst_instance, dst_port)``; the value is
        the list of ``(src_instance, src_port)`` pairs that lost to the
        first-match-wins guard (i.e. every candidate *after* the one that
        was actually wired). Populated by the same first-driver-wins guard
        that has always silently dropped these —
        ``forge.contracts.contract_verifier.verify_cardinality`` is the
        first consumer.
    ``rejected_matches``: semantic candidate rejections that never produced
        any wire at all (so there's no sink pin / ``ResolvedConnection`` to
        attach ``rejected_fanin``-style evidence to) — an ambiguous
        ``wiring_kind`` match, a role with no counterpart on the other
        side, a role-``kind`` mismatch, or an auto-match group with no
        shape-compatible counterpart. See ``RejectedMatch``. Deliberately
        does **not** cover geometric slot/offset-alignment mismatches in
        the instance-replication loops below, nor ``_groupify``'s
        structural port exclusions (``ap_*``/protocol-suffix/external) —
        those are positional/structural, not "a candidate that lost a
        semantic match", and are a deliberately deferred scope.
    ``gather_scatter_evidence``: per-pin-pair gather/scatter
        classification for topology-group connections, keyed the same way
        as ``connection_evidence`` — ``"scatter"`` (one array element
        targets one specific destination instance) or ``"gather"`` (one
        scalar source fans into one specific array-element slot). A
        separate dict (not folded into ``connection_evidence``, whose
        value type is the wiring-method string) so existing consumers of
        ``connection_evidence`` are unaffected.
    """
    contract_driven_modules: List[str] = field(default_factory=list)
    compat_mode_modules: List[str] = field(default_factory=list)
    contract_wired_roles: List[Tuple[str, str, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    wiring_method_counts: Dict[str, int] = field(default_factory=lambda: {
        "contract_wiring": 0,
        "port_map_ranges": 0,
        "port_map": 0,
        "auto_match": 0,
        "topology_group": 0,
    })
    connection_evidence: Dict[Tuple[str, str, str, str], str] = field(default_factory=dict)
    rejected_fanin: Dict[Tuple[str, str], List[Tuple[str, str]]] = field(default_factory=dict)
    rejected_matches: List[RejectedMatch] = field(default_factory=list)
    gather_scatter_evidence: Dict[Tuple[str, str, str, str], str] = field(default_factory=dict)

    def has_compat_modules(self) -> bool:
        return bool(self.compat_mode_modules)


def _inst(mod: Module, idx: int) -> str:
    """Helper to get instance name."""
    return mod.name if mod.instances == 1 else f"{mod.name}_{idx}"


# ────────────────────────────────────────────────────────────
# Hand-shake helpers that we must ignore when auto-matching
_PROTOCOL_SUFFIXES = ("_ap_vld", "_ap_ack", "_ap_hs", "_ap_ovld")

# type aliases
_Port      = Dict[str, Any]
_IpInfo    = Dict[str, Dict[str, Any]]
_ConnMap   = Dict[Tuple[str, str], List[Tuple[str, str]]]
_GlobalMap = Dict[str, List[Tuple[str, str]]]

# Accept "base_7" or "base7"
_RE_IDX = re.compile(r"^(.+?)(?:_(\d+)|(\d+))$")


# ────────────────────────────────────────────────────────────
# Public helpers
# ────────────────────────────────────────────────────────────

def load_ip_info(path: Path) -> _IpInfo:
    """Thin YAML wrapper used by several sub-packages."""
    return yaml.safe_load(path.read_text())


# ────────────────────────────────────────────────────────────
# Internal utilities
# ────────────────────────────────────────────────────────────

def _base_of(name: str) -> str:
    """Return the base name without a numeric suffix (handles foo_7 and foo7)."""
    m = _RE_IDX.match(name)
    return m.group(1) if m else name


def _groupify(
    ports: List[_Port],
    external_ins: List[str],
    external_outs: List[str],
) -> Dict[str, dict]:
    """
    Collapse indexed names into a single meta-entry keyed by the base.
    Filters out ap_* and protocol suffixes; respects externals.
    """
    buckets: Dict[str, List[_Port]] = {}
    for p in ports:
        key = _base_of(p["name"])
        buckets.setdefault(key, []).append(p)

    out: Dict[str, dict] = {}
    for key, grp in buckets.items():
        if key.startswith("ap_") or key == "ap_return":
            continue
        if any(key.endswith(suf) for suf in _PROTOCOL_SUFFIXES):
            continue

        first = grp[0]
        dirn  = first["direction"].upper()

        # Skip ports that the user declared as external
        if dirn == "IN"  and key in external_ins:
            continue
        if dirn == "OUT" and key in external_outs:
            continue

        out[key] = {
            "names":     [g["name"] for g in grp],  # original names (may include indices)
            "count":     len(grp),
            "direction": dirn,
            "width":     first["width"],
            "type":      first["type"],
        }
    return out


def _strides(shape: List[int]) -> List[int]:
    """Row-major strides – helper for N-D mapping."""
    st = [1] * len(shape)
    for i in range(len(shape) - 2, -1, -1):
        st[i] = st[i + 1] * shape[i + 1]
    return st


def _has_indexed_ports(prefix: str, universe: set[str]) -> bool:
    """
    True if there exists at least one indexed form for `prefix`
    among the given universe of port names (foo_7 or foo7).
    """
    for n in universe:
        if n == prefix:
            continue
        if _RE_IDX.match(n) and _base_of(n) == prefix:
            return True
    return False


def _pick_existing(pref: str, idx: int, universe: set[str]) -> str:
    """
    Choose an existing pin name for <pref> at index <idx>.
    Tries:  pref_<idx>, pref<idx>; if neither exists, **falls back to `pref`**.
    Never fabricates non-existent names.
    """
    if pref in universe:
        # prefer scalar if present (covers idx==0 scalar case cleanly)
        return pref
    cand1 = f"{pref}_{idx}"
    if cand1 in universe:
        return cand1
    cand2 = f"{pref}{idx}"
    if cand2 in universe:
        return cand2
    # last resort: don't invent
    return pref


# ────────────────────────────────────────────────────────────
# system.yml awareness (framework/links reservations)
# ────────────────────────────────────────────────────────────

def _parse_system_reservations(system_yml: Path | None) -> tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """
    Read system.yml (if provided) and return:
      - fw_to_mod_in : module -> set(port_bases) that are driven by the framework (links→algo)
      - mod_to_fw_out: module -> set(port_bases) that feed the framework    (algo→links)
    This is used to treat those ports as 'external' during auto-matching.
    """
    fw_to_mod_in: Dict[str, Set[str]]  = {}
    mod_to_fw_out: Dict[str, Set[str]] = {}

    if not system_yml:
        return fw_to_mod_in, mod_to_fw_out
    cfg = yaml.safe_load(system_yml.read_text()) or {}
    conns = cfg.get("connections", []) or []

    def _is_fw(name: str) -> bool:
        return str(name).lower() in ("framework", "links")

    for c in conns:
        src  = c.get("from")
        dst  = c.get("to")
        sidx = c.get("from_instance")  # unused here, reservations are per-module, not per-instance
        didx = c.get("to_instance")    # unused
        for fw_sig, alg_sig in c.get("port_map", []):
            if _is_fw(src) and not _is_fw(dst) and isinstance(dst, str):
                # framework → algorithm: reserve that module input base
                fw_to_mod_in.setdefault(dst, set()).add(_base_of(str(alg_sig)))
            elif _is_fw(dst) and not _is_fw(src) and isinstance(src, str):
                # algorithm → framework: reserve that module output base
                mod_to_fw_out.setdefault(src, set()).add(_base_of(str(fw_sig)))

    return fw_to_mod_in, mod_to_fw_out


# ────────────────────────────────────────────────────────────
# N-D mapping helper
# ────────────────────────────────────────────────────────────

def _expand_nd(
    sp: str,
    dp: str,
    *,
    dims: List[int],
    order: List[int] | None = None,
    s_offs: List[int] | None = None,
    d_offs: List[int] | None = None,
) -> List[Tuple[str, str]]:
    """Return (src, dst) pairs for an N-D mapping."""
    N = len(dims)
    order  = order  or list(range(N))
    s_offs = s_offs or [0] * N
    d_offs = d_offs or [0] * N
    if not (len(order) == len(s_offs) == len(d_offs) == N):
        raise ValueError("dims, order and *_offs must all have length N")

    s_stride = _strides(dims)
    d_shape  = [dims[order[k]] for k in range(N)]
    d_stride = _strides(d_shape)

    tpl_src = "{" in sp and "}" in sp
    tpl_dst = "{" in dp and "}" in dp

    pairs: List[Tuple[str, str]] = []
    idx = [0] * N
    while True:
        s_idx = [idx[i] + s_offs[i] for i in range(N)]
        d_idx = [idx[order[k]] + d_offs[k] for k in range(N)]

        s_name = sp.format(*s_idx) if tpl_src \
                 else f"{sp}{sum(s_idx[i]*s_stride[i] for i in range(N))}"
        d_name = dp.format(*d_idx) if tpl_dst \
                 else f"{dp}{sum(d_idx[k]*d_stride[k] for k in range(N))}"
        pairs.append((s_name, d_name))

        # ++N-D counter
        for axis in reversed(range(N)):
            idx[axis] += 1
            if idx[axis] < dims[axis]:
                break
            idx[axis] = 0
        else:
            break
    return pairs


# ────────────────────────────────────────────────────────────
# Contract-driven data-path derivation
# ────────────────────────────────────────────────────────────

def _derive_from_contracts(
    src_contract: "Any",
    dst_contract: "Any",
) -> Tuple[List[Tuple[str, str, None, None]], List[RejectedMatch]]:
    """
    Generate (src_port, dst_port, None, None) pairs for all wiring_kind-matched
    roles between *src_contract* (output roles) and *dst_contract* (input roles).

    Handles two role shapes:
    * ``nd_tpl``      — ``raw_port_tpl`` + ``dims``: expands via :func:`_expand_nd`
    * ``prefix_array``— ``raw_port_prefix`` + ``count``: expands as prefix+index

    Only roles that share the same ``wiring_kind`` string are matched.

    Alongside the accepted pairs, returns every ``wiring_kind`` group this
    function considered and rejected (no counterpart, ambiguous, or a role
    ``kind`` mismatch) as ``RejectedMatch`` objects — this function has no
    module-name context, so ``module_pair`` is left ``("", "")``; the
    caller (which does have the module names) fills it in before recording
    into ``MatchReport.rejected_matches``.
    """
    pairs: List[Tuple[str, str, None, None]] = []
    rejections: List[RejectedMatch] = []

    # Build per-wiring_kind lists of roles.  We only match wiring_kinds where
    # each side has EXACTLY ONE role — this avoids incorrect cross-matching of
    # the per-layer roles that share the same wiring_kind (e.g. dt_phi_extrap_stub
    # appears 6× on phi's output; only the single N-D gen role is unambiguous).
    from collections import defaultdict
    src_by_sk: Dict[str, list] = defaultdict(list)
    for r in src_contract.get_connection_roles("output"):
        src_by_sk[r["wiring_kind"]].append(r)

    dst_by_sk: Dict[str, list] = defaultdict(list)
    for r in dst_contract.get_connection_roles("input"):
        dst_by_sk[r["wiring_kind"]].append(r)

    for sk in src_by_sk:
        if sk not in dst_by_sk:
            rejections.append(RejectedMatch(
                module_pair=("", ""),
                scope="contract_role",
                src_ref=sk,
                dst_ref=None,
                reason=f"no destination role with wiring_kind={sk!r}",
            ))
            continue
        src_roles = src_by_sk[sk]
        dst_roles = dst_by_sk[sk]
        # Only wire when there's an unambiguous 1-to-1 wiring_kind match.
        if len(src_roles) != 1 or len(dst_roles) != 1:
            rejections.append(RejectedMatch(
                module_pair=("", ""),
                scope="contract_role",
                src_ref=sk,
                dst_ref=sk,
                reason=(
                    f"ambiguous: {len(src_roles)} src role(s) and "
                    f"{len(dst_roles)} dst role(s) share wiring_kind={sk!r} "
                    "— skipped to avoid incorrect cross-matching"
                ),
            ))
            continue
        src_role = src_roles[0]
        dst_role = dst_roles[0]
        if src_role["kind"] == "nd_tpl" and dst_role["kind"] == "nd_tpl":
            expanded = _expand_nd(
                src_role["raw_port_tpl"],
                dst_role["raw_port_tpl"],
                dims=src_role["dims"],
            )
            pairs.extend((s, d, None, None) for s, d in expanded)
        elif src_role["kind"] == "prefix_array" and dst_role["kind"] == "prefix_array":
            n = src_role["count"]
            pfx_s = src_role["raw_port_prefix"]
            pfx_d = dst_role["raw_port_prefix"]
            for i in range(n):
                pairs.append((f"{pfx_s}{i}", f"{pfx_d}{i}", None, None))
        else:
            rejections.append(RejectedMatch(
                module_pair=("", ""),
                scope="contract_role",
                src_ref=sk,
                dst_ref=sk,
                reason=(
                    f"role kind mismatch: src kind={src_role['kind']!r} "
                    f"vs dst kind={dst_role['kind']!r}"
                ),
            ))

    return pairs, rejections

def auto_match_ports(
    cfg: DesignConfig,
    ip_info: _IpInfo,
    *,
    system_yml: Path | None = None,
    contracts: "Dict[str, Any] | None" = None,
) -> "Tuple[_ConnMap, _GlobalMap, MatchReport]":
    """
    Build:
      • conn_map   : (src_inst, dst_inst) → [(src_port, dst_port)]
      • global_nets: clk/rst fan-out map
      • report     : MatchReport with per-module integration mode summary

    When *contracts* is provided (a dict ip_info_key → LoadedContract), the
    global-net wiring for clock/reset/cfg uses the contract's ``raw_port``
    declarations directly for contract-supported modules instead of heuristic
    name scanning.  Modules without a contract fall back to heuristics and are
    recorded in ``report.compat_mode_modules`` with a warning.

    When system_yml is provided, algorithm ports bound to the framework are
    treated as 'external' to avoid accidental auto-wiring.
    """

    # Framework reservations (module-level, by base name)
    fw_to_mod_in, mod_to_fw_out = _parse_system_reservations(system_yml)

    conn_map:    _ConnMap   = {}
    global_nets: _GlobalMap = {}
    report = MatchReport()

    # Global guard against multiple drivers (first match wins)
    used_sinks: set[Tuple[str, str]] = set()   # (dst_inst, dst_pin)

    # Module name → ip_info key helper.
    # When a design.yml module has ref: canonical the Module.ip_info_key stores
    # the canonical name (e.g. "csp_pack_bx_sync") while Module.name is the
    # instance override (e.g. "out_csp_best_constr").
    _mod_by_name: Dict[str, Any] = {m.name: m for m in cfg.modules}

    def _ip_key(mod_name: str) -> str:
        """Return the correct ip_info lookup key for a module instance name.

        Most modules have ip_info entries keyed by their design-level name
        (m.name).  Modules that were added to ip_info using their canonical
        registry type name (e.g. csp_pack_bx_sync) but instantiated with an
        override name (e.g. out_csp_best_constr) need to fall back to the
        ip_info_key stored on the Module object.
        """
        m = _mod_by_name.get(mod_name)
        if m is None:
            return mod_name
        # Prefer instance name if ip_info has it (common case)
        if ip_info.get(m.name) is not None:
            return m.name
        # Canonical ref fallback (newer modules keyed by canonical type)
        if m.ip_info_key and ip_info.get(m.ip_info_key) is not None:
            return m.ip_info_key
        return m.name

    # ------------------------------------------------------------------
    for conn in cfg.connections:
        S, D = conn.from_, conn.to
        SK, DK = _ip_key(S), _ip_key(D)
        if ip_info.get(SK) is None:
            raise ValueError(f"Module '{S}' (ip_info key '{SK}') not found in ip_info. Available: {list(ip_info.keys())}")
        if ip_info.get(DK) is None:
            raise ValueError(f"Module '{D}' (ip_info key '{DK}') not found in ip_info. Available: {list(ip_info.keys())}")
        src_ports = {p["name"] for p in ip_info[SK]["ports"]}
        dst_ports = {p["name"] for p in ip_info[DK]["ports"]}
        src_mod   = next(m for m in cfg.modules if m.name == S)
        dst_mod   = next(m for m in cfg.modules if m.name == D)

        # Merge 'external' lists with framework reservations (by base)
        src_ext_in  = list(src_mod.external_in_ports)
        src_ext_out = list(set(src_mod.external_out_ports) | set(mod_to_fw_out.get(S, set())))
        dst_ext_in  = list(set(dst_mod.external_in_ports)  | set(fw_to_mod_in.get(D, set())))
        dst_ext_out = list(dst_mod.external_out_ports)

        # (src_pin, dst_pin, slot, meta)
        #   slot: int index where a *scalar* src pin is allowed to connect, else None
        #   meta: dict with replication details for 1-D ranges
        base_pairs: List[Tuple[str, str, int | None, dict | None]] = []
        matched_src: set[str] = set()
        matched_dst: set[str] = set()

        # helper: does this IP expose indexed pins for a given prefix?
        def _dst_is_scalar(prefix: str) -> bool:
            return (prefix in dst_ports) and (not _has_indexed_ports(prefix, dst_ports))

        # 0) explicit ranges ---------------------------------------------------
        for rng in conn.port_map_ranges:
            if "dims" in rng:                 # N-D branch
                # Normalize dims to list if it's a single integer
                dims = rng["dims"]
                if isinstance(dims, int):
                    dims = [dims]
                    
                pairs_nd = _expand_nd(
                    rng.get("src_tpl") or rng.get("src_prefix"),
                    rng.get("dst_tpl") or rng.get("dst_prefix"),
                    dims   = dims,
                    order  = rng.get("order"),
                    s_offs = [rng.get(f"src_start{k}", 0) for k in range(len(dims))],
                    d_offs = [rng.get(f"dst_start{k}", 0) for k in range(len(dims))],
                )
                base_pairs.extend((s, d, None, {"kind": "nd"}) for s, d in pairs_nd)
                matched_src.update(s for s, _ in pairs_nd)
                matched_dst.update(d for _, d in pairs_nd)
                continue

            # 1-D branch – with scalar-fan-out detection on destination
            sp, dp = rng["src_prefix"], rng["dst_prefix"]
            cnt    = rng["count"]
            si     = rng.get("src_start", 0)
            di     = rng.get("dst_start", 0)

            scalar_src = sp in src_ports  # single physical pin on source
            dst_scalar = _dst_is_scalar(dp)

            for k in range(cnt):
                s_pin = sp if scalar_src else _pick_existing(sp, si + k, src_ports)
                d_pin = dp if dst_scalar else _pick_existing(dp, di + k, dst_ports)

                meta = {
                    "kind":       "1d",
                    "si":         si,
                    "di":         di,
                    "k":          k,
                    "cnt":        cnt,
                    "scalar_src": scalar_src,
                    "dst_scalar": dst_scalar,
                    "sp":         sp,
                    "dp":         dp,
                }
                slot  = si + k if scalar_src else None
                base_pairs.append((s_pin, d_pin, slot, meta))
                matched_src.add(s_pin)
                matched_dst.add(d_pin)

        # 1) explicit one-by-one map ------------------------------------------
        for s_port, d_port in conn.port_map:
            base_pairs.append((s_port, d_port, None, None))
            matched_src.add(s_port)
            matched_dst.add(d_port)

        # 2) bulk auto-match (same count/width/type) ---------------------------
        # If a connection already contains explicit maps or ranges, do not
        # auto-fill additional groups. Mixing partial explicit ranges with
        # same-shape auto-matching can silently wire unrelated buses together.
        if not conn.port_map and not conn.port_map_ranges:
            src_grps = _groupify(ip_info[SK]["ports"], src_ext_in, src_ext_out)
            dst_grps = _groupify(ip_info[DK]["ports"], dst_ext_in, dst_ext_out)

            for sg_key, sg in src_grps.items():
                if sg["direction"] != "OUT":  continue
                if any(n in matched_src for n in sg["names"]):  continue

                for dg in dst_grps.values():
                    if dg["direction"] != "IN":   continue
                    if any(n in matched_dst for n in dg["names"]):  continue

                    if (sg["count"] == dg["count"]
                            and sg["width"] == dg["width"]
                            and sg["type"]  == dg["type"]):
                        for i in range(sg["count"]):
                            base_pairs.append((sg["names"][i], dg["names"][i], None, None))
                        break
                else:
                    report.rejected_matches.append(RejectedMatch(
                        module_pair=(S, D),
                        scope="auto_match_group",
                        src_ref=sg_key,
                        dst_ref=None,
                        reason=(
                            f"no destination group matched shape "
                            f"(count={sg['count']}, width={sg['width']}, "
                            f"type={sg['type']!r})"
                        ),
                    ))

        # 3b) contract-driven data-path wiring --------------------------------
        # When conn.contract_wiring=True, derive array wiring from contracts.
        # Works alongside explicit port_map (scalars stay), but replaces
        # explicit port_map_ranges for the contract-covered array groups.
        if conn.contract_wiring and contracts:
            c_key_s = (
                src_mod.ip_info_key or S
                if (contracts.get(src_mod.ip_info_key or S) is not None)
                else S
            )
            c_key_d = (
                dst_mod.ip_info_key or D
                if (contracts.get(dst_mod.ip_info_key or D) is not None)
                else D
            )
            sc = contracts.get(c_key_s) or contracts.get(S)
            dc = contracts.get(c_key_d) or contracts.get(D)
            if sc and dc:
                derived, derive_rejections = _derive_from_contracts(sc, dc)
                for s_pin, d_pin, _, __ in derived:
                    base_pairs.append((s_pin, d_pin, None, None))
                    matched_src.add(s_pin)
                    matched_dst.add(d_pin)
                for rej in derive_rejections:
                    rej.module_pair = (S, D)
                    report.rejected_matches.append(rej)

        # 3) optional hand-shake wiring ---------------------------------------
        proto = cfg.block_protocol.lower()

        # ── Classify connection wiring method for reporting ────────────────
        if conn.contract_wiring:
            _wiring_method = "contract_wiring"
        elif conn.port_map_ranges:
            _wiring_method = "port_map_ranges"
        elif conn.port_map:
            _wiring_method = "port_map"
        else:
            _wiring_method = "auto_match"
        report.wiring_method_counts[_wiring_method] += 1
        want_src = lambda s: s not in matched_src and \
                             _base_of(s) not in src_ext_in and \
                             _base_of(s) not in src_ext_out
        want_dst = lambda d: d not in matched_dst and \
                             _base_of(d) not in dst_ext_in and \
                             _base_of(d) not in dst_ext_out

        if proto == "chain":
            if want_src("ap_done")  and want_dst("ap_start"):
                base_pairs.append(("ap_done", "ap_start", None, None))
            if want_src("ap_ready") and want_dst("ap_continue"):
                base_pairs.append(("ap_ready", "ap_continue", None, None))
        elif proto == "hs":
            if want_src("ap_done")  and want_dst("ap_start"):
                base_pairs.append(("ap_done", "ap_start", None, None))

        # 4) replicate across instances ---------------------------------------
        _RE_IDX_LOCAL = re.compile(r"^(.+?)(?:_(\d+)|(\d+))$")
        def _idx_of(name: str) -> int | None:
            m = _RE_IDX_LOCAL.match(name)
            if not m:
                return None
            s1, s2 = m.group(2), m.group(3)
            return int(s1 or s2) if (s1 or s2) is not None else None

        # Fallback base indices (used only if needed)
        src_idxs = [i for (sp, _, _, _) in base_pairs if (i := _idx_of(sp)) is not None]
        dst_idxs = [i for (_, dp, _, _) in base_pairs if (i := _idx_of(dp)) is not None]
        src_base_idx = min(src_idxs) if src_idxs else 0
        dst_base_idx = min(dst_idxs) if dst_idxs else 0
        dst_side_has_indices = bool(dst_idxs)

        for i_s in range(max(1, src_mod.instances)):
            src_i = _inst(src_mod, i_s)
            for i_d in range(max(1, dst_mod.instances)):
                dst_i = _inst(dst_mod, i_d)

                lst = conn_map.setdefault((src_i, dst_i), [])

                for s_pin, d_pin, slot, meta in base_pairs:
                    # scalar source pin only connects on its slot
                    if slot is not None and slot != i_s:
                        continue

                    # Offset-aware alignment ONLY for explicit 1-D ranges
                    if meta and meta.get("kind") == "1d":
                        si, di, k        = meta["si"], meta["di"], meta["k"]
                        scalar_src       = meta["scalar_src"]
                        dst_scalar       = meta["dst_scalar"]

                        if (not scalar_src) and (not dst_scalar):
                            # both sides indexed → align with offsets:
                            # i_d must equal (i_s - si) + di and be within [0, cnt)
                            rel = i_s - si
                            if not (0 <= rel < meta["cnt"] and i_d == rel + di):
                                continue

                        elif scalar_src and dst_scalar:
                            # both pins scalar → 1:1 with offset:
                            # target dst instance for this pair is di + (slot - si)
                            target_d = di + (slot - si)
                            if i_d != target_d:
                                continue

                        elif (not scalar_src) and dst_scalar:
                            # indexed source pins expanded into scalar dst pins on
                            # replicated instances → pair k must land on dst instance di + k
                            target_d = di + k
                            if i_d != target_d:
                                continue

                        # other mixed cases: keep prior permissive behavior

                    # first-driver-wins guard (per destination *instance* pin)
                    sink_key = (dst_i, d_pin)
                    if sink_key in used_sinks:
                        report.rejected_fanin.setdefault(sink_key, []).append((src_i, s_pin))
                        continue
                    used_sinks.add(sink_key)

                    lst.append((s_pin, d_pin))
                    report.connection_evidence[(src_i, s_pin, dst_i, d_pin)] = _wiring_method

    # ─────────────────────────────────────────────────────────────────────────
    # Topology group wiring
    #
    # Process topology_groups entries from the design config.  Each topology
    # group resolves contract-declared roles into concrete wire pairs using
    # the topology_deriver engine.  Instance replication is handled via the
    # slot metadata returned by the deriver (for instance_assign groups) or
    # via the same instance-loop pattern as normal connections.
    # ─────────────────────────────────────────────────────────────────────────
    for tg in cfg.topology_groups:
        S, D = tg.from_, tg.to
        src_mod = next(m for m in cfg.modules if m.name == S)
        dst_mod = next(m for m in cfg.modules if m.name == D)

        # Look up contracts
        c_key_s = (src_mod.ip_info_key or S) if contracts and contracts.get(src_mod.ip_info_key or S) else S
        c_key_d = (dst_mod.ip_info_key or D) if contracts and contracts.get(dst_mod.ip_info_key or D) else D
        sc = contracts.get(c_key_s) if contracts else None
        dc = contracts.get(c_key_d) if contracts else None

        if sc is None or dc is None:
            raise ValueError(
                f"topology_group '{tg.name}': both endpoints need contracts. "
                f"Missing: {' and '.join(n for n, c in [('source', sc), ('dest', dc)] if c is None)}"
            )

        base_pairs = derive_topology_group(tg, sc, dc)
        report.wiring_method_counts["topology_group"] += 1

        src_offset = tg.src_instance_offset

        # Instance replication — same pattern as connection Step 4 but uses
        # topology_deriver's slot metadata for instance_assign groups.
        for i_s in range(max(1, src_mod.instances)):
            src_i = _inst(src_mod, i_s)
            for i_d in range(max(1, dst_mod.instances)):
                dst_i = _inst(dst_mod, i_d)
                lst = conn_map.setdefault((src_i, dst_i), [])

                for s_pin, d_pin, slot, meta in base_pairs:
                    # Instance-partition slot: scalar src only connects on its assigned instance
                    if slot is not None and slot != i_s:
                        continue

                    # Scatter: array element targets a specific destination instance
                    if meta and meta.get("kind") == "scatter":
                        if i_d != meta["target_instance"]:
                            continue

                    # For non-slotted pairs with no meta, only do 1:1 diagonal
                    # wiring when both sides are replicated, applying
                    # src_instance_offset for shifted instance mappings.
                    #
                    # 2026-09-23: added the single-instance-destination branch.
                    # src_instance_offset used to only apply when BOTH sides
                    # were multi-instance; with dst_mod.instances == 1 the
                    # offset check was skipped entirely (the outer condition
                    # was False), so every src instance 0..N-1 matched in
                    # iteration order and the first one (i_s == 0) always won
                    # via used_sinks -- src_instance_offset was silently
                    # ignored. Confirmed via a real topology_group
                    # (cfg_to_gmt_interface/cfg_to_gmt_linker,
                    # src_instance_offset: 131, dst instances: 1): the
                    # intended cfg instance 131 (the shared GMT board-id
                    # word) was left dangling, and both gmt_interface and
                    # gmt_linker were wired to cfg instance 0 (an unrelated
                    # DT link's own config word) instead. See
                    # reference_manifest.yaml's 2026-09-23 entry.
                    if slot is None and meta is None:
                        if src_mod.instances > 1 and dst_mod.instances > 1:
                            if (i_s - src_offset) != i_d:
                                continue
                        elif src_mod.instances > 1 and dst_mod.instances == 1:
                            if i_s != src_offset:
                                continue

                    sink_key = (dst_i, d_pin)
                    if sink_key in used_sinks:
                        report.rejected_fanin.setdefault(sink_key, []).append((src_i, s_pin))
                        continue
                    used_sinks.add(sink_key)

                    lst.append((s_pin, d_pin))
                    key = (src_i, s_pin, dst_i, d_pin)
                    report.connection_evidence[key] = "topology_group"
                    if meta and meta.get("kind") in ("scatter", "gather"):
                        report.gather_scatter_evidence[key] = meta["kind"]

    # ─────────────────────────────────────────────────────────────────────────
    # Global nets (clk / rst)
    #
    # Contract-driven path:
    #   For contract-supported modules, the clock_primary / reset_primary
    #   raw_port from the contract is used directly — no name-scanning.
    #
    # Heuristic compatibility path:
    #   For modules without a contract, fall back to scanning common port
    #   names (ap_clk, clk, …).  A compatibility warning is emitted.
    # ─────────────────────────────────────────────────────────────────────────

    _missing_ip_info_warned: set[str] = set()

    def _mod_port_dicts(m: Module) -> List[_Port]:
        """Raw port-dict list for *m* from ip_info, or ``[]`` (with a
        one-time warning) if the module's ip_info entry is unresolved (e.g.
        missing HLS build artifacts). Never raises — global-net wiring for
        an unresolved module is simply skipped instead of crashing."""
        key = _ip_key(m.name)
        entry = ip_info.get(key)
        if entry is None:
            if m.name not in _missing_ip_info_warned:
                _missing_ip_info_warned.add(m.name)
                report.warnings.append(
                    f"[{m.name}] no ip_info entry (ip_info key {key!r}) — "
                    "skipped for global-net (clock/reset/control-signal) wiring"
                )
            return []
        return entry["ports"]

    def _mod_ports(m: Module) -> Set[str]:
        return {p["name"] for p in _mod_port_dicts(m)}

    def _collect_heuristic(sig: str) -> None:
        """Heuristic scanner: add all module-instances that expose port *sig*."""
        binds: List[Tuple[str, str]] = []
        for m in cfg.modules:
            if sig in m.external_in_ports or sig in m.external_out_ports:
                continue
            if sig in _mod_ports(m):
                for i in range(m.instances):
                    binds.append((_inst(m, i), sig))
        if binds:
            # Merge into global_nets; don't create duplicate entries
            existing = {inst for inst, _ in global_nets.get(sig, [])}
            new_binds = [(inst, port) for inst, port in binds if inst not in existing]
            if new_binds:
                global_nets.setdefault(sig, []).extend(new_binds)

    # ── Contract-driven global-net collection ─────────────────────────────────
    #
    # Build per-module clock/reset bindings using the contract's raw_port
    # declarations.  Modules without a contract are flagged and scheduled for
    # heuristic fallback below.

    modules_needing_compat_clock: List[Any] = []
    modules_needing_compat_reset: List[Any] = []

    if contracts:
        for m in cfg.modules:
            # Contract lookup uses the canonical module type name (= the ref:
            # value stored as m.ip_info_key, or m.name when there is no ref).
            # ip_info lookup uses _ip_key (instance name preferred, then ref).
            _contract_key = m.ip_info_key or m.name
            contract = contracts.get(_contract_key)
            mod_ports = _mod_ports(m)

            if contract is None:
                # No contract → compatibility mode
                if m.name not in report.compat_mode_modules:
                    report.compat_mode_modules.append(m.name)
                if cfg.connect_clock:
                    modules_needing_compat_clock.append(m)
                if cfg.connect_reset:
                    modules_needing_compat_reset.append(m)
                continue

            report.contract_driven_modules.append(m.name)

            # Clock binding from contract
            if cfg.connect_clock and not contract.clock_free:
                clk_port = contract.get_raw_port("clock_primary")
                if clk_port and clk_port in mod_ports:
                    if clk_port not in m.external_in_ports and clk_port not in m.external_out_ports:
                        for i in range(m.instances):
                            global_nets.setdefault(clk_port, []).append((_inst(m, i), clk_port))
                        report.contract_wired_roles.append((m.name, "clock_primary", clk_port))
                else:
                    # Contract says there should be a clock but port not found → warning
                    report.warnings.append(
                        f"[{m.name}] contract declares clock_primary={clk_port!r} "
                        f"but port not found in ip_info"
                    )

            # Reset binding from contract
            if cfg.connect_reset and not contract.reset_free:
                rst_port = contract.get_raw_port("reset_primary")
                if rst_port and rst_port in mod_ports:
                    if rst_port not in m.external_in_ports and rst_port not in m.external_out_ports:
                        for i in range(m.instances):
                            global_nets.setdefault(rst_port, []).append((_inst(m, i), rst_port))
                        report.contract_wired_roles.append((m.name, "reset_primary", rst_port))
                else:
                    report.warnings.append(
                        f"[{m.name}] contract declares reset_primary={rst_port!r} "
                        f"but port not found in ip_info"
                    )

        # Heuristic fallback for compat-mode modules only
        _CLOCK_NAMES = ("ap_clk", "clk", "clock")
        _RESET_NAMES = ("ap_rst", "rst", "reset", "rst_n")

        for m in modules_needing_compat_clock:
            mod_ports_set = _mod_ports(m)
            for sig in _CLOCK_NAMES:
                if sig in mod_ports_set and sig not in m.external_in_ports and sig not in m.external_out_ports:
                    for i in range(m.instances):
                        global_nets.setdefault(sig, []).append((_inst(m, i), sig))
                    break  # first match wins per module

        for m in modules_needing_compat_reset:
            mod_ports_set = _mod_ports(m)
            for sig in _RESET_NAMES:
                if sig in mod_ports_set and sig not in m.external_in_ports and sig not in m.external_out_ports:
                    for i in range(m.instances):
                        global_nets.setdefault(sig, []).append((_inst(m, i), sig))
                    break  # first match wins per module

    else:
        # No contracts provided → full heuristic mode (legacy/backward-compat)
        for m in cfg.modules:
            if m.name not in report.compat_mode_modules:
                report.compat_mode_modules.append(m.name)

        if cfg.connect_clock:
            _collect_heuristic("ap_clk")
            _collect_heuristic("clk")
            _collect_heuristic("clock")
        if cfg.connect_reset:
            _collect_heuristic("ap_rst")
            _collect_heuristic("rst")
            _collect_heuristic("reset")
            _collect_heuristic("rst_n")

    # Collect each declared broadcast control signal (default: "new_event",
    # for designs that don't declare `control_signals`) as a global signal
    # ONLY if no module generates it internally — e.g. if a timing-generator
    # module outputs it, it shouldn't be treated as a top-level input.
    broadcast_signal_names = set(cfg.control_signals.keys()) if cfg.control_signals else {"new_event"}
    for sig_name in broadcast_signal_names:
        has_internal_source = any(
            sig_name in {
                p["name"]
                for p in _mod_port_dicts(m)
                if str(p.get("direction", "")).upper() in ("OUT", "OUTPUT", "INOUT")
            }
            for m in cfg.modules
        )
        if not has_internal_source:
            _collect_heuristic(sig_name)

    return conn_map, global_nets, report




# ────────────────────────────────────────────────────────────
# Quick CLI for manual inspection
# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, sys

    ap = argparse.ArgumentParser(description="Inspect auto-matched ports")
    ap.add_argument("design", type=Path)
    ap.add_argument("--ip-info", type=Path)
    ap.add_argument("--system", type=Path, help="system.yml (framework/links reservations)")
    ap.add_argument("-b", "--build-dir", type=Path, default=Path("build"))
    ns = ap.parse_args()

    d_yaml = ns.design if ns.design.suffix in (".yaml", ".yml") else ns.design / "design.yaml"
    cfg    = DesignConfig.load(d_yaml)
    proj   = d_yaml.parent.name
    ipf    = ns.ip_info or (ns.build_dir / proj / "ip_info.yaml")

    info = load_ip_info(ipf)
    cmap, gmap, rpt = auto_match_ports(cfg, info, system_yml=ns.system)

    for (s, d), pairs in cmap.items():
        print(f"\n{s} → {d}")
        for a, b in pairs:
            print(f"  {a:<25} → {b}")
    for net, pins in gmap.items():
        print(f"\nGLOBAL {net}")
        for mod, pin in pins:
            print(f"  {mod}.{pin}")
    print(f"\nContract-driven : {rpt.contract_driven_modules}")
    print(f"Compat-mode     : {rpt.compat_mode_modules}")
    for w in rpt.warnings:
        print(f"  WARNING: {w}")

    if ns.system and ns.system.exists():
        fw_in, fw_out = _parse_system_reservations(ns.system)
        if fw_in:
            print("\n[system.yml] framework → algorithm (reserved INs):")
            for m, ps in sorted(fw_in.items()):
                print(f"  {m}: {', '.join(sorted(ps))}")
        if fw_out:
            print("\n[system.yml] algorithm → framework (reserved OUTs):")
            for m, ps in sorted(fw_out.items()):
                print(f"  {m}: {', '.join(sorted(ps))}")

    sys.exit(0)

