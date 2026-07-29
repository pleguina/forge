"""forge inspect — canonical resolved-design IR, read-only.

This is the first CLI consumer of ``forge.ir`` (see
``docs/development/release-readiness.md`` for the migration-sequence
tracking). It resolves a design's modules, instances, interfaces, and
connections into one canonical model and can print it, export it as JSON,
or diff it against a previously exported snapshot.

Read-only by default: building the IR never writes ``ip_info.yaml`` or any
other file — the same invariant established for ``topgen gen-top --dry-run``.
The only file this command ever writes is the one explicitly named by
``--emit-ir``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional


def cmd_inspect(args):
    from forge.ir import build_project_ir, content_hash, diff_projects, to_json_dict

    design_path = Path(args.design).expanduser().resolve()
    json_mode = getattr(args, "json", False)

    if not design_path.exists():
        msg = f"Design file not found: {design_path}"
        if json_mode:
            print(json.dumps({"passed": False, "error": msg}, indent=2))
        else:
            print(f"❌ {msg}")
        sys.exit(1)

    try:
        project = build_project_ir(
            design_path,
            contracts_from=getattr(args, "contracts_from", None),
            ip_info=getattr(args, "ip_info", None),
            build_dir=getattr(args, "build_dir", None),
            ip_root=getattr(args, "ip_root", None),
            src_root=getattr(args, "src_root", None),
        )
    except Exception as e:  # noqa: BLE001 - surfaced as a clean CLI error
        msg = f"Failed to resolve design: {e}"
        if json_mode:
            print(json.dumps({"passed": False, "error": msg}, indent=2))
        else:
            print(f"❌ {msg}")
        if getattr(args, "debug", False):
            raise
        sys.exit(1)

    errors = [d for d in project.design.diagnostics if d.severity == "error"]

    if getattr(args, "diff", None):
        diff_path = Path(args.diff).expanduser().resolve()
        if not diff_path.exists():
            msg = f"--diff file not found: {diff_path}"
            if json_mode:
                print(json.dumps({"passed": False, "error": msg}, indent=2))
            else:
                print(f"❌ {msg}")
            sys.exit(1)
        previous = _project_from_json(json.loads(diff_path.read_text()))
        result = diff_projects(previous, project)
        if json_mode:
            print(json.dumps(result, indent=2))
        else:
            _print_diff(result)
        sys.exit(0 if result["hash_equal"] else 1 if errors else 0)

    if getattr(args, "explain_staleness", None):
        from forge.ir.provenance import build_provenance, explain_staleness, read_provenance

        prov_path = Path(args.explain_staleness).expanduser().resolve()
        if not prov_path.exists():
            msg = f"--explain-staleness file not found: {prov_path}"
            if json_mode:
                print(json.dumps({"passed": False, "error": msg}, indent=2))
            else:
                print(f"❌ {msg}")
            sys.exit(1)
        previous = read_provenance(prov_path)
        current = build_provenance(project, command_options=_command_options(args))
        result = explain_staleness(previous, current)
        if json_mode:
            print(json.dumps({"stale": result.stale, "reasons": result.reasons}, indent=2))
        else:
            if result.stale:
                print(f"⚠️  stale — {len(result.reasons)} reason(s):")
                for r in result.reasons:
                    print(f"    - {r}")
            else:
                print("✅ fresh — no reason to regenerate")
        sys.exit(1 if result.stale else 0)

    if getattr(args, "emit_ir", None):
        out_path = Path(args.emit_ir).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(to_json_dict(project), indent=2, sort_keys=True))
        if not json_mode:
            print(f"✅ IR written to {out_path}")

    if getattr(args, "provenance", None):
        from forge.ir.provenance import build_provenance, write_provenance

        prov_path = Path(args.provenance).expanduser().resolve()
        write_provenance(prov_path, build_provenance(project, command_options=_command_options(args)))
        if not json_mode:
            print(f"✅ provenance manifest written to {prov_path}")

    if json_mode:
        payload = to_json_dict(project)
        payload["content_hash"] = content_hash(project)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_human(project, content_hash(project))

    sys.exit(1 if errors else 0)


def _command_options(args) -> dict:
    """The subset of CLI args that affect IR resolution — recorded in the
    provenance manifest so `--explain-staleness` can tell "you changed an
    input" apart from "you changed how you called forge inspect"."""
    return {
        "contracts_from": getattr(args, "contracts_from", None),
        "ip_info": getattr(args, "ip_info", None),
        "build_dir": getattr(args, "build_dir", None),
        "ip_root": getattr(args, "ip_root", None),
        "src_root": getattr(args, "src_root", None),
    }


def _print_human(project, ir_hash: str) -> None:
    d = project.design
    print(f"forge inspect — {d.name}")
    print(f"  IR schema version : {project.schema_version}")
    print(f"  content hash       : {ir_hash}")
    print(f"  modules            : {len(d.modules)}")
    for m in d.modules:
        flag = "" if m.ports_resolved else "  ⚠️  ports not resolved"
        print(f"    - {m.name} ({m.kind}, top={m.top}, {len(m.interfaces)} interface(s)){flag}")
    print(f"  instances          : {len(d.instances)}")
    print(f"  connections        : {len(d.connections)}")
    print(f"  clock domains      : {[c.name for c in d.clock_domains]}")
    print(f"  reset domains      : {[r.name for r in d.reset_domains]}")
    if d.diagnostics:
        print(f"  diagnostics ({len(d.diagnostics)}):")
        for diag in d.diagnostics:
            icon = {"error": "❌", "warning": "⚠️ ", "info": "ℹ️ "}.get(diag.severity, "•")
            print(f"    {icon} {diag.message}")
    else:
        print("  diagnostics        : (none)")


def _print_diff(result: dict) -> None:
    print(f"hash_equal: {result['hash_equal']}")
    print(f"  before: {result['hash_a']}")
    print(f"  after:  {result['hash_b']}")
    for kind in ("instances", "connections"):
        d = result[kind]
        if d["added"] or d["removed"] or d["changed"]:
            print(f"{kind}:")
            if d["added"]:
                print(f"  + added:   {d['added']}")
            if d["removed"]:
                print(f"  - removed: {d['removed']}")
            if d["changed"]:
                print(f"  ~ changed: {d['changed']}")


def _project_from_json(payload: dict):
    """Reconstruct enough of a ResolvedProject from an emitted IR JSON dict
    to diff against — used only by ``--diff``, not a general deserializer."""
    from forge.ir.model import (
        CardinalityCheckResult, MatchingEvidence, RejectedCandidate,
        ResolvedClockDomain, ResolvedConnection, ResolvedDesign, ResolvedEndpoint,
        ResolvedInstance, ResolvedInterfaceMember, ResolvedLogicalInterface,
        ResolvedModuleDefinition, ResolvedPhysicalBinding, ResolvedProject,
        ResolvedResetDomain, ResolvedTopLevelPort, ResolvedTransformation,
        ResolvedVerificationPlan, SourceLocation, DiagnosticReference,
    )

    def _matching_evidence(me: Optional[dict]):
        if not me:
            return None
        return MatchingEvidence(
            producer_wiring_kind=me.get("producer_wiring_kind"),
            consumer_wiring_kind=me.get("consumer_wiring_kind"),
            producer_coordinates=me.get("producer_coordinates"),
            consumer_coordinates=me.get("consumer_coordinates"),
            producer_protocol=me.get("producer_protocol"),
            consumer_protocol=me.get("consumer_protocol"),
            producer_width=me.get("producer_width"),
            consumer_width=me.get("consumer_width"),
            producer_cardinality=(
                CardinalityCheckResult(**me["producer_cardinality"])
                if me.get("producer_cardinality") else None
            ),
            consumer_cardinality=(
                CardinalityCheckResult(**me["consumer_cardinality"])
                if me.get("consumer_cardinality") else None
            ),
            gather_scatter_pattern=me.get("gather_scatter_pattern"),
            cdc_declared=me.get("cdc_declared"),
            rejected_candidates=[
                RejectedCandidate(
                    producer=ResolvedEndpoint(**rc["producer"]), reason=rc["reason"],
                ) for rc in me.get("rejected_candidates", [])
            ],
        )

    def _loc(v):
        return SourceLocation(**v) if v else None

    d = payload["design"]
    modules = [
        ResolvedModuleDefinition(
            name=m["name"], kind=m["kind"], top=m["top"],
            source_files=m.get("source_files", []),
            contract_path=m.get("contract_path"),
            ports_resolved=m.get("ports_resolved", True),
            interfaces=[
                ResolvedLogicalInterface(
                    name=i["name"], direction=i["direction"],
                    wiring_kind=i.get("wiring_kind"), coordinates=i.get("coordinates"),
                    protocol=i.get("protocol"), cardinality=i.get("cardinality"),
                    members=[
                        ResolvedInterfaceMember(
                            name=mem["name"],
                            binding=ResolvedPhysicalBinding(**mem["binding"]),
                            direction=mem.get("direction"),
                        ) for mem in i.get("members", [])
                    ],
                ) for i in m.get("interfaces", [])
            ],
            parameters=m.get("parameters", {}),
            latency_cycles=m.get("latency_cycles"),
            latency_hint=m.get("latency_hint"),
            is_variable_latency=m.get("is_variable_latency", False),
            ip_info_key=m.get("ip_info_key"),
        ) for m in d.get("modules", [])
    ]
    instances = [ResolvedInstance(**i) for i in d.get("instances", [])]
    connections = [
        ResolvedConnection(
            id=c["id"],
            producer=ResolvedEndpoint(**c["producer"]),
            consumer=ResolvedEndpoint(**c["consumer"]),
            wiring_method=c.get("wiring_method"),
            transformations=[ResolvedTransformation(**t) for t in c.get("transformations", [])],
            emission_order=c.get("emission_order", 0),
            crosses_clock_domain=c.get("crosses_clock_domain", False),
            crosses_reset_domain=c.get("crosses_reset_domain", False),
            matching_evidence=_matching_evidence(c.get("matching_evidence")),
        ) for c in d.get("connections", [])
    ]
    clock_domains = [ResolvedClockDomain(**c) for c in d.get("clock_domains", [])]
    reset_domains = [ResolvedResetDomain(**r) for r in d.get("reset_domains", [])]
    top_ports = [ResolvedTopLevelPort(**p) for p in d.get("top_ports", [])]
    diagnostics = [
        DiagnosticReference(
            severity=diag["severity"], message=diag["message"], code=diag.get("code"),
            object_id=diag.get("object_id"), location=_loc(diag.get("location")),
        ) for diag in d.get("diagnostics", [])
    ]
    design = ResolvedDesign(
        name=d["name"], modules=modules, instances=instances, connections=connections,
        clock_domains=clock_domains, reset_domains=reset_domains, top_ports=top_ports,
        verification_plan=ResolvedVerificationPlan(**d.get("verification_plan", {"populated": False})),
        diagnostics=diagnostics, source=_loc(d.get("source")),
    )
    from forge.ir.model import IR_SCHEMA_VERSION
    return ResolvedProject(
        design=design,
        schema_version=payload.get("schema_version", IR_SCHEMA_VERSION),
        forge_version=payload.get("forge_version", ""),
        generated_from=payload.get("generated_from", {}),
    )


def register(sub) -> None:
    """Register the top-level ``forge inspect`` command."""
    p = sub.add_parser(
        "inspect",
        help="Resolve a design into the canonical IR and inspect it (read-only)",
    )
    p.add_argument("design", help="Path to design.yml")
    p.add_argument("--contracts-from", help="Path to modules.yml (interface_contract: entries)")
    p.add_argument("--ip-info", help="Path to a pre-built ip_info.yaml (optional)")
    p.add_argument("--build-dir", help="HLS/RTL build root to scan for component.xml (optional)")
    p.add_argument("--ip-root", help="Additional IP repo root to scan (optional)")
    p.add_argument("--src-root", help="Root for resolving relative RTL src paths (optional)")
    p.add_argument("--json", action="store_true", default=False, help="Machine-readable JSON output")
    p.add_argument("--emit-ir", help="Write the resolved IR as JSON to this path")
    p.add_argument("--diff", help="Diff against a previously --emit-ir'd IR JSON file")
    p.add_argument("--provenance", help="Write a content-hash provenance manifest to this path")
    p.add_argument(
        "--explain-staleness",
        help="Compare against a previously --provenance'd manifest and explain why it's stale",
    )
    p.set_defaults(func=cmd_inspect)
