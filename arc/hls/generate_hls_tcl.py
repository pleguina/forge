#!/usr/bin/env python3
"""
generate_hls_tcl.py
-------------------

Compatibility entry point: generates Vitis HLS TCL scripts from a plugin-owned
HLS catalog.

The core logic now lives in topgen.hls.catalog.
This script is kept as a standalone convenience wrapper so that parallel_hls.sh
and legacy Makefile recipes can call it directly via ``python3 generate_hls_tcl.py``
without installing the full Python package.

Preferred interface (requires installed topgen):
    topgen hls gen-tcl --module <name> --hls-config <path>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Import core logic from the topgen package when available; fall back to
# a local copy of the functions so the script still works without the package.
# ---------------------------------------------------------------------------
try:
    from topgen.hls.catalog import (  # type: ignore[import]
        SUPPORTED_STAGES,
        load_hls_catalog,
        resolve_module_name,
        module_field,
        modules_for_stage,
        modules_from_design,
        generate_tcl,
        print_value as _print_value,
    )
except ImportError as _import_error:
    print(
        f"ERROR: topgen package is not installed.\n"
        f"  {_import_error}\n"
        f"Install it with:  pip install -e framework/topgen/\n"
        f"Then retry:       python3 generate_hls_tcl.py ...",
        file=sys.stderr,
    )
    sys.exit(1)

import yaml  # needed for --config YAML loading in main()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Vitis HLS TCL scripts from plugin-provided HLS catalog data.")
    parser.add_argument("module", nargs="?", help="Module name or 'all'")
    parser.add_argument("--template-dir", default=Path(__file__).resolve().parent / "templates", help="Directory containing .tcl.j2 templates")
    parser.add_argument("--output-dir", default=Path("build_hls"), help="Directory to place generated module dirs")
    parser.add_argument("--ip-packages-dir", type=Path, help="Directory where exported IP archives should be written")
    parser.add_argument("--config", help="Optional YAML file with per-run overrides")
    parser.add_argument("--config-file", help="Plugin HLS config file")
    parser.add_argument("--list", action="store_true", help="List available modules and exit")
    parser.add_argument("--list-modules", action="store_true", help="Print available module names separated by spaces")
    parser.add_argument("--list-stage", choices=SUPPORTED_STAGES, help="Print modules that enable the requested stage")
    parser.add_argument("--resolve-design-modules", help="Print HLS module names required by a design YAML")
    parser.add_argument("--resolve-module", help="Resolve a module alias to its canonical module name")
    parser.add_argument("--get-field", nargs=2, metavar=("MODULE", "FIELD"), help="Print a module config field, e.g. stages.csim")
    parser.add_argument(
        "--flow",
        choices=["export", "syn", "impl"],
        default="impl",
        help="IP export flow: export (no synth), syn (RTL synthesis only), or impl (synth + place & route)",
    )
    args = parser.parse_args()

    try:
        catalog = load_hls_catalog(args.config_file)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    if args.list:
        print("Available modules:")
        for name in catalog["ordered_modules"]:
            print(f"  - {name}")
        return 0

    if args.list_modules:
        print(" ".join(catalog["ordered_modules"]))
        return 0

    if args.list_stage:
        print(" ".join(modules_for_stage(catalog, args.list_stage)))
        return 0

    if args.resolve_design_modules:
        print(" ".join(modules_from_design(catalog, args.resolve_design_modules)))
        return 0

    if args.resolve_module:
        resolved = resolve_module_name(catalog, args.resolve_module)
        if not resolved:
            return 1
        print(resolved)
        return 0

    if args.get_field:
        value = module_field(catalog, args.get_field[0], args.get_field[1])
        return _print_value(value)

    if not args.module:
        parser.print_help()
        return 1

    config_override = None
    if args.config:
        config_override = yaml.safe_load(Path(args.config).read_text()) or {}
    if config_override is None:
        config_override = {}
    config_override["flow"] = args.flow

    modules = catalog["ordered_modules"] if args.module == "all" else [args.module]
    ok = True
    for module_name in modules:
        print(f"\n=== Generating TCL files for {module_name} ===")
        if not generate_tcl(catalog, module_name, args.template_dir, args.output_dir, args.ip_packages_dir, config_override):
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
