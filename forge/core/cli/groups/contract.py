"""``forge contract`` — author and inspect interface contracts.

Writing interface contracts is the single largest recurring authoring cost
in a FORGE project: across this repo's reference plugins they run 79-174
lines each, and 308 of their 324 roles carry no integration decision at all
— they restate the port name, direction and width the module's own source
already declares.

A generator for that existed, but it was filed under
``forge topgen migrate --kind infer-contract``: a verb that means "convert
an old project", not "author a new module". It also required the caller to
produce an ``ip_info.yaml`` first, and handed every data port back as a
TODO *comment* the author then retyped as YAML. This group is that
generator given the name of the job it actually does, pointed straight at
a module registry so no intermediate file is needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

from forge.core.cli._shared import print_cli_error
from forge.core.cli.envelope import CommandEnvelope, emit, status_for_exception


def cmd_infer(args) -> None:
    from forge.contracts.contract_loader import _scan_module_ports
    from forge.generation.migrate import infer_contract_skeleton

    json_mode = getattr(args, "json", False)
    dry_run = getattr(args, "dry_run", False)

    try:
        import yaml

        module = args.module
        entry: dict
        source_type = "rtl"

        if args.ip_info:
            # Explicit port list: the only route for an HLS module before
            # its IP is built, since there is no HDL to scan yet.
            ip_info_path = Path(args.ip_info).expanduser().resolve()
            if not ip_info_path.is_file():
                raise FileNotFoundError(f"ip_info file not found: {ip_info_path}")
            ip_info = yaml.safe_load(ip_info_path.read_text()) or {}
            if module not in ip_info:
                available = ", ".join(sorted(ip_info)) or "(none)"
                raise ValueError(
                    f"module {module!r} not found in {ip_info_path.name}. "
                    f"Available: {available}"
                )
            entry = ip_info[module] or {}
            source_type = args.source_type or "hls"
        else:
            registry_path = Path(args.contracts_from).expanduser().resolve()
            if not registry_path.is_file():
                raise FileNotFoundError(f"registry not found: {registry_path}")
            registry = yaml.safe_load(registry_path.read_text()) or {}
            mod_entry = next(
                (m for m in (registry.get("modules") or []) if m.get("name") == module),
                None,
            )
            if mod_entry is None:
                available = ", ".join(
                    sorted(str(m.get("name")) for m in (registry.get("modules") or []))
                ) or "(none)"
                raise ValueError(
                    f"module {module!r} not found in {registry_path.name}. "
                    f"Available: {available}"
                )
            ports = _scan_module_ports(mod_entry, registry_path.parent)
            if not ports:
                raise ValueError(
                    f"could not scan any ports for module {module!r}. An HLS "
                    f"module has no HDL until its IP is built — build it, then "
                    f"pass the result with --ip-info."
                )
            entry = {
                "ports": [
                    {"name": n, "direction": p["direction"], "width": p["width"]}
                    for n, p in ports.items()
                ]
            }
            source_type = args.source_type or str(mod_entry.get("kind") or "rtl")

        skeleton = infer_contract_skeleton(module, entry, source_type=source_type)

        artifacts = []
        output = Path(args.output).expanduser().resolve() if args.output else None
        if output and output.exists():
            raise ValueError(f"{output} already exists — refusing to overwrite")
        if output and not dry_run:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(skeleton)
            artifacts.append(str(output))

        if not json_mode:
            print(skeleton)
            if artifacts:
                print(f"✅ wrote {artifacts[0]}")
            elif dry_run and output:
                print(f"[dry-run] would write {output}")

        n_roles = skeleton.count("\n    ") and len(
            [l for l in skeleton.splitlines() if l.startswith("    ") and l.rstrip().endswith(":")]
        )
        envelope = CommandEnvelope(
            status="pass",
            artifacts=artifacts,
            metrics={"module": module, "source_type": source_type, "roles": n_roles},
            next_actions=[
                "Review the role names, add the wiring_kind/protocol/partition "
                "semantics that can't be inferred from a port name, then set "
                "normalization_status: ready",
            ],
        )
        sys.exit(emit(envelope, json_mode=json_mode))

    except SystemExit:
        raise
    except Exception as exc:
        envelope = CommandEnvelope(
            status=status_for_exception(exc),
            diagnostics=[{"severity": "error", "message": str(exc)}],
        )
        if json_mode:
            sys.exit(emit(envelope, json_mode=True))
        print_cli_error("Contract inference failed", exc)
        sys.exit(envelope.exit_code())


def register(sub) -> None:
    p = sub.add_parser(
        "contract",
        help="Author interface contracts (infer a skeleton from a module's ports)",
        description="Author and inspect module interface contracts.",
    )
    cmds = p.add_subparsers(dest="contract_cmd", required=True, metavar="COMMAND")

    infer = cmds.add_parser(
        "infer",
        help="Infer an interface contract skeleton from a module's real ports",
        description=(
            "Emit an interface-contract skeleton with every observed port as a "
            "role. RTL roles are emitted by name alone — raw_port, direction "
            "and width are derived from the module's own source at load time. "
            "HLS roles carry direction and width, since an HLS module has no "
            "HDL to scan until its IP is built."
        ),
    )
    infer.add_argument("module", help="Module name (as it appears in modules.yml)")
    infer.add_argument(
        "--contracts-from", dest="contracts_from", default="modules.yml",
        help="Module registry to look the module up in (default: modules.yml)",
    )
    infer.add_argument(
        "--ip-info", dest="ip_info", default=None,
        help="Use an ip_info.yaml port list instead of scanning the source "
             "(required for an HLS module before its IP is built)",
    )
    infer.add_argument(
        "--source-type", dest="source_type", choices=("rtl", "hls"), default=None,
        help="Override the source type (default: the module's 'kind')",
    )
    infer.add_argument("--output", "-o", default=None, help="Write the skeleton to this path")
    infer.add_argument("--dry-run", dest="dry_run", action="store_true", default=False,
                       help="Print the skeleton without writing it")
    infer.add_argument("--json", action="store_true", default=False,
                       help="Machine-readable JSON output")
    infer.set_defaults(func=cmd_infer)
