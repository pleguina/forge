"""
catalog.py
----------
Core HLS catalog loading and TCL generation logic.

This module is the single implementation source for all HLS orchestration
operations. It is consumed by:
  - topgen hls gen-tcl  (via cli/main.py)
    - framework/hls/generate_hls_tcl.py  (compatibility entry point)
"""

from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader


SUPPORTED_STAGES = ("csim", "synth", "cosim", "export")
FILE_ARG_PATTERN = re.compile(r"(\S+\.(?:xml|csv|txt|dat|bin))")


def _find_hls_dir() -> Path | None:
    """Locate hls/ by walking up from CWD then package location."""
    for start in (Path.cwd(), Path(__file__).resolve()):
        p = start
        for _ in range(15):
            candidate = p / "hls"
            if (candidate / "templates").is_dir():
                return candidate
            if p.parent == p:
                break
            p = p.parent
    return None


def default_template_dir() -> Path:
    """Return the default template directory (framework/hls/templates/)."""
    hls_dir = _find_hls_dir()
    if hls_dir is not None:
        return hls_dir / "templates"
    # Post-split fallback: templates shipped alongside this package
    return Path(__file__).resolve().parent / "templates"


def resolve_config_path(config_path: Path | str | None = None) -> Path:
    if config_path:
        return Path(config_path).expanduser().resolve()

    env_path = os.environ.get("HLS_CONFIG") or os.environ.get("HLS_CONFIG_FILE")
    if env_path:
        return Path(env_path).expanduser().resolve()

    raise FileNotFoundError(
        "No HLS config file provided. Pass --hls-config or set HLS_CONFIG/HLS_CONFIG_FILE."
    )


def _normalize_stage_flags(raw_stages: Any) -> dict[str, bool]:
    flags = {stage: False for stage in SUPPORTED_STAGES}
    if isinstance(raw_stages, list):
        for stage in raw_stages:
            if stage in flags:
                flags[stage] = True
        return flags
    if isinstance(raw_stages, dict):
        for stage in SUPPORTED_STAGES:
            flags[stage] = bool(raw_stages.get(stage, False))
    return flags


def load_hls_catalog(config_path: Path | str | None = None) -> dict[str, Any]:
    """Load and parse a plugin-owned HLS catalog (hls_config.yaml)."""
    catalog_path = resolve_config_path(config_path)
    if not catalog_path.exists():
        raise FileNotFoundError(f"HLS config file not found: {catalog_path}")

    raw_catalog = yaml.safe_load(catalog_path.read_text()) or {}
    defaults = raw_catalog.get("defaults", {}) or {}
    raw_modules = raw_catalog.get("modules", []) or []

    # Normalize registry format (modules.yml) to flat catalog format (hls_config.yaml).
    # Registry format is detected by the presence of a top-level 'registry_version' key
    # or a 'build:' sub-section in any module entry.
    _is_registry = raw_catalog.get("registry_version") or any(
        "build" in m for m in raw_modules
    )
    if _is_registry:
        normalized: list[dict[str, Any]] = []
        for m in raw_modules:
            mod = copy.deepcopy(m)
            build = mod.pop("build", {}) or {}
            verify = mod.pop("verify", {}) or {}
            # Only include modules that have HLS build stages (skip pure RTL entries)
            if mod.get("kind", "hls") != "hls" and not build.get("stages"):
                continue
            # Flatten build section into module root for catalog consumers
            mod["stages"] = build.get("stages", mod.get("stages", []))
            mod.setdefault("csim_opts", build.get("csim_opts", ""))
            mod.setdefault("csynth_opts", build.get("csynth_opts", ""))
            mod.setdefault("cosim_opts", build.get("cosim_opts", ""))
            mod.setdefault("aliases", build.get("aliases", []))
            mod.setdefault("design_aliases", build.get("design_aliases", []))
            # Flatten verify section: tb_src → tb
            mod.setdefault("tb", verify.get("tb_src", []))
            mod.setdefault("tb_args", verify.get("tb_args", ""))
            mod.setdefault("cosim_tb_args", verify.get("cosim_tb_args", ""))
            mod.setdefault("testbench_target", verify.get("testbench_target", ""))
            normalized.append(mod)
        raw_modules = normalized

    modules: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    design_aliases: dict[str, str] = {}
    stage_modules = {stage: [] for stage in SUPPORTED_STAGES}
    ordered_modules: list[str] = []

    for raw_module in raw_modules:
        module = copy.deepcopy(defaults)
        module.update(raw_module)

        name = module.get("name")
        if not name:
            raise ValueError(f"HLS config module entry is missing 'name': {raw_module}")
        if name in modules:
            raise ValueError(f"Duplicate HLS module name in catalog: {name}")

        module.setdefault("src", [])
        module.setdefault("tb", [])
        module.setdefault("includes", [])
        module.setdefault("cflags", [])
        module.setdefault("csim_opts", "")
        module.setdefault("csynth_opts", "")
        module.setdefault("cosim_opts", "")
        module.setdefault("cosim_tb_args", "")
        module.setdefault("tb_args", "")
        module.setdefault("aliases", [])
        module.setdefault("design_aliases", [])
        module["stages"] = _normalize_stage_flags(module.get("stages", {}))

        modules[name] = module
        ordered_modules.append(name)
        aliases[name] = name

        for alias in module["aliases"]:
            aliases[alias] = name

        for design_alias in module["design_aliases"]:
            design_aliases[design_alias] = name

        for stage in SUPPORTED_STAGES:
            if module["stages"].get(stage, False):
                stage_modules[stage].append(name)

    return {
        "path": catalog_path,
        "modules": modules,
        "aliases": aliases,
        "design_aliases": design_aliases,
        "ordered_modules": ordered_modules,
        "stage_modules": stage_modules,
    }


def resolve_module_name(catalog: dict[str, Any], module_name: str) -> str | None:
    return catalog["aliases"].get(module_name)


def module_field(catalog: dict[str, Any], module_name: str, field_path: str) -> Any:
    def _nested_get(value: Any, fp: str) -> Any:
        current = value
        for segment in fp.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(segment)
        return current

    resolved_name = resolve_module_name(catalog, module_name)
    if not resolved_name:
        return None
    return _nested_get(catalog["modules"][resolved_name], field_path)


def modules_for_stage(catalog: dict[str, Any], stage: str) -> list[str]:
    if stage not in SUPPORTED_STAGES:
        raise ValueError(f"Unsupported HLS stage: {stage}")
    return catalog["stage_modules"][stage]


def modules_from_design(catalog: dict[str, Any], design_path: Path | str) -> list[str]:
    resolved_design = Path(design_path).expanduser().resolve()
    design_data = yaml.safe_load(resolved_design.read_text()) or {}
    modules = []
    for module in design_data.get("modules", []):
        # ref: is the primary lookup key in the new registry-based format
        ref = module.get("ref")
        if ref:
            if ref in catalog["modules"] and ref not in modules:
                modules.append(ref)
            continue
        if module.get("kind") != "hls":
            continue
        name = module.get("name")
        resolved = (
            resolve_module_name(catalog, name)
            or catalog["design_aliases"].get(name)
            or name
        )
        if resolved in catalog["modules"] and resolved not in modules:
            modules.append(resolved)
    return modules


def _absolutize_data_files(arg_string: str, base_root: Path) -> str:
    if not arg_string:
        return arg_string

    def repl(match: re.Match) -> str:
        rel_path = match.group(1)
        if os.path.isabs(rel_path):
            return rel_path
        abs_path = (base_root / rel_path).resolve()
        if not abs_path.exists():
            print(f"⚠️  Warning: data file not found: {abs_path}")
        else:
            print(f"📁 Converted to absolute path: {abs_path}")
        return str(abs_path)

    return FILE_ARG_PATTERN.sub(repl, arg_string)


def _inject_log_dir(arg_string: str, logs_dir_abs: str) -> str:
    if not arg_string:
        return arg_string

    tokens = arg_string.split()
    if "--log" in tokens:
        return arg_string

    if len(tokens) >= 2 and tokens[0] == "--backend":
        backend = tokens[0:2]
        rest = tokens[2:]
        return " ".join(backend + ["--log", logs_dir_abs] + rest)

    return arg_string + " --log " + logs_dir_abs


def generate_tcl(
    catalog: dict[str, Any],
    module_name: str,
    template_dir: str | Path | None = None,
    output_dir: str | Path = "build_hls",
    ip_packages_dir: str | Path | None = None,
    config_override: dict[str, Any] | None = None,
) -> bool:
    """Generate Vitis HLS TCL scripts for a single module."""
    resolved_name = resolve_module_name(catalog, module_name)
    if not resolved_name:
        print(f"ERROR: Unknown module '{module_name}'")
        print("Available modules:", ", ".join(catalog["ordered_modules"]))
        return False

    resolved_template_dir = Path(template_dir).resolve() if template_dir else default_template_dir()

    config = copy.deepcopy(catalog["modules"][resolved_name])
    if config_override:
        config.update(config_override)

    build_hls_dir = Path(output_dir).resolve()
    module_dir = build_hls_dir / resolved_name
    module_dir.mkdir(parents=True, exist_ok=True)

    logs_dir = module_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    catalog_root = catalog["path"].parent
    build_root = build_hls_dir.parent.resolve()
    resolved_ip_packages_dir = (
        Path(ip_packages_dir).resolve() if ip_packages_dir else (build_root / "ip_packages")
    )
    resolved_ip_packages_dir.mkdir(parents=True, exist_ok=True)

    def make_absolute(path_str: str) -> str:
        if Path(path_str).is_absolute():
            return path_str
        return str((catalog_root / path_str).resolve())

    config["src"] = [make_absolute(p) for p in config.get("src", [])]
    config["tb"] = [make_absolute(p) for p in config.get("tb", [])]
    config["includes"] = [make_absolute(p) for p in config.get("includes", [])]
    config["cflags"] = list(config.get("cflags", []))
    if "-DHLS_CSIM_BUILD" not in config["cflags"]:
        config["cflags"].append("-DHLS_CSIM_BUILD")

    config["project_dir"] = str(module_dir)
    config["module_name"] = resolved_name
    config["project_root"] = str(build_root)
    config["ip_packages_dir"] = str(resolved_ip_packages_dir)
    config["logs_dir"] = str(logs_dir)

    raw_tb_args = config.get("tb_args", "")
    raw_cosim_tb_args = config.get("cosim_tb_args", "")
    tb_args_abs = _absolutize_data_files(raw_tb_args, catalog_root)
    cosim_tb_args_abs = _absolutize_data_files(raw_cosim_tb_args, catalog_root)
    config["tb_args"] = _inject_log_dir(tb_args_abs, str(logs_dir)) if raw_tb_args else ""
    config["cosim_tb_args"] = (
        _inject_log_dir(cosim_tb_args_abs, str(logs_dir)) if raw_cosim_tb_args else ""
    )

    env = Environment(loader=FileSystemLoader(str(resolved_template_dir)))
    templates = [
        "project.tcl.j2",
        "csim.tcl.j2",
        "synth.tcl.j2",
        "cosim.tcl.j2",
        "ip_export.tcl.j2",
        "clean.tcl.j2",
    ]

    for template_name in templates:
        try:
            template = env.get_template(template_name)
            rendered = template.render(**config)
        except Exception as exc:
            print(f"❌ Error rendering {template_name} for {resolved_name}: {exc}")
            return False

        output_path = module_dir / template_name.replace(".j2", "")
        try:
            output_path.write_text(rendered)
        except Exception as exc:
            print(f"❌ Error writing {output_path}: {exc}")
            return False

        print(f"✅ Generated: {output_path}")

    return True


def print_value(value: Any) -> int:
    if value is None:
        print("")
        return 0
    if isinstance(value, bool):
        print("true" if value else "false")
        return 0
    if isinstance(value, list):
        print(" ".join(str(item) for item in value))
        return 0
    if isinstance(value, dict):
        print(yaml.safe_dump(value, sort_keys=False).strip())
        return 0
    print(value)
    return 0
