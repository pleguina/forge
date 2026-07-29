"""
Comprehensive validation for design configurations.

This module ensures design.yml is correct before generation:
- Required fields present
- Valid value ranges
- Consistent references
- Proper module definitions
- Valid connections
"""

from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass
from .config import DESIGN_SCHEMA_VERSION, MODULE_REGISTRY_SCHEMA_VERSION, DesignConfig, Module
from forge.core.schema_version import check_schema_version


@dataclass
class ValidationError:
    """A single validation error with context."""
    severity: str  # 'error', 'warning', 'info'
    category: str  # 'yaml', 'module', 'connection', 'timing', etc.
    message: str
    location: Optional[str] = None  # Where in YAML: "modules[0].name"
    suggestion: Optional[str] = None  # How to fix it
    
    def __str__(self) -> str:
        icon = {'error': '❌', 'warning': '⚠️', 'info': 'ℹ️'}[self.severity]
        msg = f"{icon} [{self.category.upper()}] {self.message}"
        if self.location:
            msg += f"\n   Location: {self.location}"
        if self.suggestion:
            msg += f"\n   💡 Suggestion: {self.suggestion}"
        return msg


class DesignValidator:
    """Validates design configuration for correctness and consistency."""
    
    def __init__(self, cfg: DesignConfig, design_path: Optional[Path] = None):
        self.cfg = cfg
        self.design_path = design_path
        self.errors: List[ValidationError] = []
        self.warnings: List[ValidationError] = []
        self.infos: List[ValidationError] = []
    
    def add_error(self, category: str, message: str, location: Optional[str] = None, suggestion: Optional[str] = None):
        """Add a validation error."""
        self.errors.append(ValidationError('error', category, message, location, suggestion))
    
    def add_warning(self, category: str, message: str, location: Optional[str] = None, suggestion: Optional[str] = None):
        """Add a validation warning."""
        self.warnings.append(ValidationError('warning', category, message, location, suggestion))
    
    def add_info(self, category: str, message: str, location: Optional[str] = None, suggestion: Optional[str] = None):
        """Add a validation info message."""
        self.infos.append(ValidationError('info', category, message, location, suggestion))
    
    def validate_all(self) -> bool:
        """Run all validations. Returns True if no errors."""
        self.validate_basic_fields()
        self.validate_schema_version()
        self.validate_timing()
        self.validate_modules()
        self.validate_connections()
        self.validate_control_signals()
        self.validate_consistency()

        return len(self.errors) == 0

    def validate_schema_version(self):
        """release-plan §2.7: an undeclared schema_version is silent (fully
        backward compatible); a declared one is checked for compatibility
        against DESIGN_SCHEMA_VERSION."""
        for issue in check_schema_version(self.cfg.schema_version, DESIGN_SCHEMA_VERSION, schema_name="design.yml"):
            if issue.severity == "error":
                self.add_error("schema", issue.message)
            else:
                self.add_warning("schema", issue.message)

    def validate_basic_fields(self):
        """Validate basic required fields."""
        # FPGA part
        if not self.cfg.part:
            self.add_error('yaml', 
                          "Missing 'part' field (FPGA part number)",
                          suggestion="Add: part: xcvu13p-fsga2577-1-e")
        elif not self.cfg.part.startswith('xc'):
            self.add_warning('yaml',
                           f"Part number '{self.cfg.part}' doesn't look like Xilinx part",
                           suggestion="Xilinx parts start with 'xc' (e.g., xcvu13p, xcku, xc7)")
        
        # Clock period
        if not self.cfg.clock_period or self.cfg.clock_period <= 0:
            self.add_error('timing',
                          f"Invalid clock_period: {self.cfg.clock_period}",
                          suggestion="Add: clock_period: 2.77  # nanoseconds")
        elif self.cfg.clock_period < 1.0:
            self.add_warning('timing',
                           f"Very fast clock: {self.cfg.clock_period}ns ({1000/self.cfg.clock_period:.0f} MHz)",
                           suggestion="Verify this is achievable on your FPGA")
        elif self.cfg.clock_period > 100:
            self.add_warning('timing',
                           f"Very slow clock: {self.cfg.clock_period}ns ({1000/self.cfg.clock_period:.1f} MHz)",
                           suggestion="Verify that this frequency is intentional for the target design")
        
        # Modules
        if not self.cfg.modules or len(self.cfg.modules) == 0:
            self.add_error('module',
                          "No modules defined in design",
                          suggestion="Add at least one module in 'modules:' section")
    
    def validate_timing(self):
        """Validate timing-related settings."""
        if not self.cfg.clock_period:
            return  # Already reported in basic validation
        
        # Calculate batches against the design's reference period (defaults
        # to the LHC 40 MHz / 25 ns BX period when not declared).
        ref_period = self.cfg.reference_period_ns if self.cfg.reference_period_ns is not None else 25.0
        batches = round(ref_period / self.cfg.clock_period)

        if abs(batches * self.cfg.clock_period - ref_period) > 0.5:
            self.add_warning('timing',
                           f"Clock period {self.cfg.clock_period}ns doesn't divide reference period ({ref_period}ns) evenly",
                           suggestion=f"Batches = {batches}, actual period = {batches * self.cfg.clock_period}ns")
        
    def validate_modules(self):
        """Validate module definitions."""
        module_names = set()
        
        for i, mod in enumerate(self.cfg.modules):
            loc = f"modules[{i}]"
            
            # Required fields
            if not mod.name:
                self.add_error('module', f"Module missing 'name' field", location=loc,
                             suggestion="Add: name: my_module")
                continue
            
            # Duplicate names
            if mod.name in module_names:
                self.add_error('module', f"Duplicate module name: '{mod.name}'", location=loc,
                             suggestion=f"Module names must be unique. Rename to '{mod.name}_v2' or similar")
            module_names.add(mod.name)
            
            # Top entity
            if not mod.top:
                self.add_error('module', f"Module '{mod.name}' missing 'top' (entity/module name)",
                             location=f"{loc}.top",
                             suggestion="Add: top: my_module_entity")
            
            # Source files
            if not mod.src or len(mod.src) == 0:
                self.add_error('module', f"Module '{mod.name}' has no source files",
                             location=f"{loc}.src",
                             suggestion="Add: src: [path/to/source.cpp] or src: [path/to/file.v]")
            else:
                # Validate source file paths (check both design-relative and workspace-relative)
                for src_file in mod.src:
                    if self.design_path:
                        import os as _os
                        # Expand environment variables (e.g. ${FORGE_RTL_ROOT})
                        src_file_expanded = _os.path.expandvars(src_file)
                        # If fully resolved after expansion, check directly
                        if _os.path.isabs(src_file_expanded):
                            if not _os.path.exists(src_file_expanded):
                                self.add_warning('module',
                                              f"Module '{mod.name}' source file not found: {src_file}",
                                              location=f"{loc}.src",
                                              suggestion=f"Check path. Resolved to:\n      - {src_file_expanded}")
                            continue
                        # Try relative to design.yml directory
                        src_path_design = self.design_path.parent / src_file_expanded
                        # Try relative to workspace root (2 levels up from designs/)
                        src_path_workspace = self.design_path.parent.parent / src_file_expanded
                        
                        if not src_path_design.exists() and not src_path_workspace.exists():
                            self.add_warning('module', 
                                          f"Module '{mod.name}' source file not found: {src_file}",
                                          location=f"{loc}.src",
                                          suggestion=f"Check path. Tried:\n" +
                                                    f"      - {src_path_design}\n" +
                                                    f"      - {src_path_workspace}")
            
            # Kind validation
            if not mod.kind:
                self.add_error('module', f"Module '{mod.name}' missing 'kind' (hls or rtl)",
                             location=f"{loc}.kind",
                             suggestion="Add: kind: hls  or  kind: rtl")
            elif mod.kind not in ['hls', 'rtl']:
                self.add_error('module', f"Module '{mod.name}' has invalid kind: '{mod.kind}'",
                             location=f"{loc}.kind",
                             suggestion="Use 'hls' for Vitis HLS or 'rtl' for hand-written HDL")
            
            # HLS-specific validation
            if mod.kind == 'hls':
                if not mod.stages or len(mod.stages) == 0:
                    self.add_warning('module', f"HLS module '{mod.name}' has no build stages",
                                  location=f"{loc}.stages",
                                  suggestion="Add: stages: [project, synth, ip]")
                
                # Check source file extensions
                for src_file in mod.src or []:
                    ext = Path(src_file).suffix.lower()
                    if ext not in ['.cpp', '.c', '.cxx', '.cc', '.h', '.hpp']:
                        self.add_warning('module',
                                      f"HLS module '{mod.name}' has non-C++ source: {src_file}",
                                      location=f"{loc}.src",
                                      suggestion="HLS modules typically use .cpp files")
            
            # RTL-specific validation
            if mod.kind == 'rtl':
                if not mod.rtl_lang:
                    self.add_warning('module', f"RTL module '{mod.name}' missing 'rtl_lang'",
                                  location=f"{loc}.rtl_lang",
                                  suggestion="Add: rtl_lang: verilog  or  rtl_lang: vhdl")
                elif mod.rtl_lang not in ['verilog', 'vhdl']:
                    self.add_error('module', f"RTL module '{mod.name}' has invalid rtl_lang: '{mod.rtl_lang}'",
                                 location=f"{loc}.rtl_lang",
                                 suggestion="Use 'verilog' or 'vhdl'")
                
                # Check source file extensions
                expected_ext = {'.v', '.sv'} if mod.rtl_lang == 'verilog' else {'.vhd', '.vhdl'}
                for src_file in mod.src or []:
                    ext = Path(src_file).suffix.lower()
                    if ext not in expected_ext:
                        self.add_warning('module',
                                      f"RTL module '{mod.name}' language is '{mod.rtl_lang}' but source is {ext}",
                                      location=f"{loc}.src")
            
            # Instances
            if not mod.instances or mod.instances < 1:
                self.add_error('module', f"Module '{mod.name}' has invalid instances: {mod.instances}",
                             location=f"{loc}.instances",
                             suggestion="Add: instances: 1  (or higher)")
            elif mod.instances > 100:
                self.add_warning('module', f"Module '{mod.name}' has many instances: {mod.instances}",
                               location=f"{loc}.instances",
                               suggestion="Verify this is intentional (will create large design)")
            
            # Debug flag
            if mod.debug:
                self.add_info('debug', f"Module '{mod.name}' has debug probes enabled",
                            location=f"{loc}.debug",
                            suggestion=f"Will expose all ports for {mod.instances} instances")
    
    def validate_connections(self):
        """Validate module connections."""
        if not self.cfg.connections:
            self.add_warning('connection', "No connections defined between modules",
                          suggestion="Add 'connections:' section to wire modules together")
            return
        
        module_names = {m.name for m in self.cfg.modules}
        
        for i, conn in enumerate(self.cfg.connections):
            loc = f"connections[{i}]"
            
            # Check from/to modules exist
            if not conn.from_:
                self.add_error('connection', "Connection missing 'from' module", location=loc,
                             suggestion="Add: from: source_module")
                continue
            
            if not conn.to:
                self.add_error('connection', "Connection missing 'to' module", location=loc,
                             suggestion="Add: to: dest_module")
                continue
            
            if conn.from_ not in module_names:
                self.add_error('connection', 
                             f"Connection from unknown module: '{conn.from_}'",
                             location=f"{loc}.from",
                             suggestion=f"Available modules: {', '.join(sorted(module_names))}")
            
            if conn.to not in module_names:
                self.add_error('connection',
                             f"Connection to unknown module: '{conn.to}'",
                             location=f"{loc}.to",
                             suggestion=f"Available modules: {', '.join(sorted(module_names))}")
            
            # Port map (can be empty if relying on auto-matching)
            if not conn.port_map or len(conn.port_map) == 0:
                self.add_info('connection',
                             f"Connection {conn.from_}→{conn.to} has no explicit port_map (will use auto-matching)",
                             location=f"{loc}.port_map",
                             suggestion="This is OK if port names match. For explicit mapping, add: port_map:\n  - [out_port, in_port]")
            
            # Delay validation
            if conn.delay_cycles and conn.delay_cycles < 0:
                self.add_error('connection',
                             f"Connection {conn.from_}→{conn.to} has negative delay: {conn.delay_cycles}",
                             location=f"{loc}.delay_cycles",
                             suggestion="Delay must be >= 0")
            elif conn.delay_cycles and conn.delay_cycles > 1000:
                self.add_warning('connection',
                               f"Connection {conn.from_}→{conn.to} has very large delay: {conn.delay_cycles}",
                               location=f"{loc}.delay_cycles",
                               suggestion="Verify this delay is intentional")
            
            # Register stages validation
            if conn.register_stages and conn.register_stages < 0:
                self.add_error('connection',
                             f"Connection {conn.from_}→{conn.to} has negative register_stages: {conn.register_stages}",
                             location=f"{loc}.register_stages",
                             suggestion="Register stages must be >= 0")
    
    def validate_control_signals(self):
        """Validate control signal distribution."""
        if not self.cfg.control_signals:
            return  # Optional feature
        
        module_names = {m.name for m in self.cfg.modules}
        
        for sig_name, sig_config in self.cfg.control_signals.items():
            loc = f"control_signals.{sig_name}"
            
            # Width validation
            if not sig_config.width or sig_config.width < 1:
                self.add_error('control',
                             f"Control signal '{sig_name}' has invalid width: {sig_config.width}",
                             location=f"{loc}.width",
                             suggestion="Add: width: 1")
            
            # Distribution targets
            if not sig_config.distribution or len(sig_config.distribution) == 0:
                self.add_warning('control',
                               f"Control signal '{sig_name}' has no distribution targets",
                               location=f"{loc}.distribution",
                               suggestion="Add targets or remove this control signal")
            else:
                for j, target in enumerate(sig_config.distribution):
                    if target.module not in module_names:
                        self.add_error('control',
                                     f"Control signal '{sig_name}' targets unknown module: '{target.module}'",
                                     location=f"{loc}.distribution[{j}].module",
                                     suggestion=f"Available: {', '.join(sorted(module_names))}")
                    
                    if target.delay_cycles is not None and target.delay_cycles < 0:
                        self.add_error('control',
                                     f"Control signal '{sig_name}' has negative delay for '{target.module}': {target.delay_cycles}",
                                     location=f"{loc}.distribution[{j}].delay_cycles",
                                     suggestion="Delay must be >= 0")
    
    def validate_consistency(self):
        """Validate cross-cutting consistency."""
        # Check for isolated modules (no connections in or out)
        if self.cfg.connections or (hasattr(self.cfg, 'topology_groups') and self.cfg.topology_groups):
            connected_modules = set()
            for conn in self.cfg.connections:
                connected_modules.add(conn.from_)
                connected_modules.add(conn.to)
            # topology_groups are also real connections — include their endpoints
            for tg in (getattr(self.cfg, 'topology_groups', None) or []):
                connected_modules.add(tg.from_)
                connected_modules.add(tg.to)
            
            for mod in self.cfg.modules:
                if mod.name not in connected_modules:
                    # Check if it has external ports (might be intentional)
                    has_external = (mod.external_in_ports and len(mod.external_in_ports) > 0) or \
                                  (mod.external_out_ports and len(mod.external_out_ports) > 0)
                    
                    if not has_external:
                        self.add_warning('consistency',
                                       f"Module '{mod.name}' is not connected to anything",
                                       suggestion="Either connect it or add external_in_ports/external_out_ports")
        
        # Check for modules with external_out_ports that also connect to other modules
        # (data might be going two places)
        if self.cfg.connections:
            for mod in self.cfg.modules:
                if mod.external_out_ports and len(mod.external_out_ports) > 0:
                    # Check if this module is a source in connections
                    is_source = any(conn.from_ == mod.name for conn in self.cfg.connections)
                    if is_source:
                        self.add_info('consistency',
                                    f"Module '{mod.name}' outputs go to both external ports AND other modules",
                                    suggestion="Verify this is intentional (fanout)")
    
    def print_report(self):
        """Print validation report."""
        total_issues = len(self.errors) + len(self.warnings) + len(self.infos)
        
        if total_issues == 0:
            print("✅ Design validation passed with no issues!")
            return
        
        print(f"\n{'='*70}")
        print(f"📋 DESIGN VALIDATION REPORT")
        print(f"{'='*70}\n")
        
        if self.errors:
            print(f"❌ ERRORS ({len(self.errors)}):")
            print(f"{'-'*70}")
            for err in self.errors:
                print(f"{err}\n")
        
        if self.warnings:
            print(f"⚠️  WARNINGS ({len(self.warnings)}):")
            print(f"{'-'*70}")
            for warn in self.warnings:
                print(f"{warn}\n")
        
        if self.infos:
            print(f"ℹ️  INFO ({len(self.infos)}):")
            print(f"{'-'*70}")
            for info in self.infos:
                print(f"{info}\n")
        
        print(f"{'='*70}")
        print(f"Summary: {len(self.errors)} errors, {len(self.warnings)} warnings, {len(self.infos)} info")
        
        if self.errors:
            print("\n⛔ Cannot proceed with generation due to errors. Fix them first.")
        else:
            print("\n✅ No errors found. You may proceed with generation.")
        
        print(f"{'='*70}\n")
    
    def has_errors(self) -> bool:
        """Check if there are any errors."""
        return len(self.errors) > 0


def validate_design(cfg: DesignConfig, design_path: Optional[Path] = None) -> DesignValidator:
    """
    Validate a design configuration.
    
    Args:
        cfg: Design configuration to validate
        design_path: Path to design.yml (for checking source file paths)
    
    Returns:
        DesignValidator with results
    """
    validator = DesignValidator(cfg, design_path)
    validator.validate_all()
    return validator


# ---------------------------------------------------------------------------
# Registry validation
# ---------------------------------------------------------------------------

_KNOWN_KINDS: Set[str] = {'hls', 'rtl', 'framework-utility'}
_KNOWN_HLS_STAGES: Set[str] = {'csim', 'synth', 'cosim', 'export', 'project', 'ip'}
_KNOWN_HLS_EXTENSIONS: Set[str] = {'.cpp', '.c', '.cxx', '.cc', '.h', '.hpp'}
_KNOWN_RTL_EXTENSIONS: Dict[str, Set[str]] = {
    'verilog': {'.v', '.sv'},
    'vhdl': {'.vhd', '.vhdl'},
}
_KNOWN_MODULE_KEYS: Set[str] = {
    'name', 'kind', 'top', 'src', 'includes', 'rtl_lang', 'vhdl_library',
    'vhdl_version', 'rtl_include_dirs', 'verilog_defines', 'rtl_packages',
    'cflags', 'stages', 'build', 'verify', 'description',
    # Contract-driven topology extension fields (used by forge topgen gen-top)
    'interface_contract',
    # Latency annotation fields (used by forge analyze / the canonical IR
    # via Module.timing — see topgen/config.py::ModuleTiming)
    'latency_hint', 'latency_cycles', 'variable_latency',
    # Structured latency: {kind: fixed|bounded|elastic, ...} declaration
    # (release-plan §4.2, Phase 4 slice 2) — coexists with, does not
    # replace, the three flat fields above.
    'latency',
}
_KNOWN_LATENCY_KINDS: Set[str] = {'fixed', 'bounded', 'elastic'}
_KNOWN_BUILD_KEYS: Set[str] = {
    'stages', 'csim_opts', 'synth_opts', 'cosim_opts', 'export_opts',
    'aliases', 'clock_period', 'part',
}
_KNOWN_VERIFY_KEYS: Set[str] = {
    'tb_src', 'testbench_target', 'tb_args', 'cosim_tb_args', 'adapter',
}


class RegistryValidator:
    """Validates a modules.yml registry for structural correctness.

    Rules are *optional in presence, strict in structure*:

    * Sections (build, verify) are not required on a module.  When present
      their fields are validated independently.
    * kind-conditional rules enforce what each kind needs to be buildable /
      elaboratable:
        - kind: hls          → top + src (C/C++ extensions) required
        - kind: rtl          → top + src (RTL extensions) required; rtl_lang recommended
        - kind: framework-utility → no structural requirements beyond name/kind
    * If build: is present it must be a mapping with a non-empty stages list
      whose values are drawn from the known HLS stage set.
    * If verify: is present it must be a mapping; tb_src must be a list if
      given; string fields (testbench_target, tb_args, …) must be strings.
    """

    def __init__(self, raw: Dict[str, Any], registry_path: Optional[Path] = None):
        self._raw = raw
        self.registry_path = registry_path
        self.errors: List[ValidationError] = []
        self.warnings: List[ValidationError] = []
        self.infos: List[ValidationError] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_all(self) -> bool:
        """Run all validations. Returns True when no errors were found."""
        self._validate_top_level()
        seen_names: Set[str] = set()
        for i, mod in enumerate(self._raw.get('modules', []) or []):
            loc = f"modules[{i}]"
            name = self._validate_module_identity(mod, i, loc, seen_names)
            if name:
                seen_names.add(name)
                kind = mod.get('kind')
                self._validate_kind_requirements(mod, name, kind, loc)
                self._validate_timing_fields(mod, name, loc)
                self._validate_latency_block(mod, name, loc)
                if 'build' in mod:
                    self._validate_build_section(mod['build'], name, kind, f"{loc}.build")
                if 'verify' in mod:
                    self._validate_verify_section(mod['verify'], name, f"{loc}.verify")
                self._validate_unknown_keys(mod, _KNOWN_MODULE_KEYS, loc)
        return len(self.errors) == 0

    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def print_report(self):
        total = len(self.errors) + len(self.warnings) + len(self.infos)
        if total == 0:
            print("✅ Registry validation passed with no issues!")
            return

        print(f"\n{'='*70}")
        print("📋 REGISTRY VALIDATION REPORT")
        print(f"{'='*70}\n")

        if self.errors:
            print(f"❌ ERRORS ({len(self.errors)}):")
            print(f"{'-'*70}")
            for e in self.errors:
                print(f"{e}\n")

        if self.warnings:
            print(f"⚠️  WARNINGS ({len(self.warnings)}):")
            print(f"{'-'*70}")
            for w in self.warnings:
                print(f"{w}\n")

        if self.infos:
            print(f"ℹ️  INFO ({len(self.infos)}):")
            print(f"{'-'*70}")
            for info in self.infos:
                print(f"{info}\n")

        print(f"{'='*70}")
        print(f"Summary: {len(self.errors)} errors, {len(self.warnings)} warnings, {len(self.infos)} info")
        if self.errors:
            print("\n⛔ Registry has errors. Fix them before using in a design.")
        else:
            print("\n✅ No errors. Registry is structurally sound.")
        print(f"{'='*70}\n")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add(self, bucket: List[ValidationError], severity: str, category: str,
             message: str, location: Optional[str] = None, suggestion: Optional[str] = None):
        bucket.append(ValidationError(severity, category, message, location, suggestion))

    def add_error(self, category, message, location=None, suggestion=None):
        self._add(self.errors, 'error', category, message, location, suggestion)

    def add_warning(self, category, message, location=None, suggestion=None):
        self._add(self.warnings, 'warning', category, message, location, suggestion)

    def add_info(self, category, message, location=None, suggestion=None):
        self._add(self.infos, 'info', category, message, location, suggestion)

    def _validate_top_level(self):
        if not self._raw.get('registry_version'):
            self.add_warning('schema', "Missing 'registry_version' field",
                             suggestion="Add: registry_version: '1'")
        for issue in check_schema_version(
            self._raw.get('registry_version'), MODULE_REGISTRY_SCHEMA_VERSION, schema_name="modules.yml",
        ):
            if issue.severity == "error":
                self.add_error('schema', issue.message)
            else:
                self.add_warning('schema', issue.message)
        if 'modules' not in self._raw:
            self.add_error('schema', "Registry has no 'modules' key",
                           suggestion="Add a top-level 'modules:' sequence")
        elif not isinstance(self._raw.get('modules'), list):
            self.add_error('schema', "'modules' must be a YAML sequence (list)")

    def _validate_module_identity(self, mod, idx: int, loc: str,
                                   seen_names: Set[str]) -> Optional[str]:
        if not isinstance(mod, dict):
            self.add_error('module', f"Entry {idx} is not a YAML mapping", location=loc)
            return None

        name = mod.get('name')
        if not name:
            self.add_error('module', "Module entry missing 'name'", location=loc,
                           suggestion="Add: name: my_module")
            return None
        if not isinstance(name, str):
            self.add_error('module', f"'name' must be a string (got {type(name).__name__})",
                           location=f"{loc}.name")
            return None
        if name in seen_names:
            self.add_error('module', f"Duplicate module name: '{name}'",
                           location=loc,
                           suggestion=f"Module names must be unique. Rename to '{name}_v2' or similar")

        kind = mod.get('kind')
        if not kind:
            self.add_error('module', f"Module '{name}' missing 'kind'",
                           location=f"{loc}.kind",
                           suggestion="Add: kind: hls  |  kind: rtl  |  kind: framework-utility")
        elif kind not in _KNOWN_KINDS:
            self.add_error('module', f"Module '{name}' has unknown kind: '{kind}'",
                           location=f"{loc}.kind",
                           suggestion=f"Valid kinds: {', '.join(sorted(_KNOWN_KINDS))}")

        return name

    def _validate_timing_fields(self, mod: dict, name: str, loc: str):
        """A module cannot declare both a fixed explicit latency
        (latency_cycles) and variable_latency: true — contradictory.
        Reported as a structured error here (the user-facing surface for
        YAML mistakes); ModuleTiming.__post_init__ raising ValueError is a
        defense-in-depth backstop for direct programmatic construction,
        not the primary path."""
        if mod.get('latency_cycles') is not None and mod.get('variable_latency'):
            self.add_error(
                'module',
                f"module '{name}' declares both 'latency_cycles' (a fixed "
                "explicit latency) and 'variable_latency: true' — these are "
                "contradictory",
                location=f"{loc}.latency_cycles",
                suggestion="Declare only one: latency_cycles for a known fixed "
                           "latency, or variable_latency: true for a data-dependent one.",
            )

    def _validate_latency_block(self, mod: dict, name: str, loc: str):
        """Structural validation of a ``latency: {kind: fixed|bounded|elastic,
        ...}`` declaration (release-plan §4.2). Reported as a structured
        error here (the user-facing surface for YAML mistakes);
        ``LatencyDeclaration.__post_init__`` raising ``ValueError`` is a
        defense-in-depth backstop for direct programmatic construction, not
        the primary path. Coexistence with the flat latency_cycles/
        latency_hint/variable_latency fields is enforced separately (at
        construction time in ``topgen.config._pop_timing``) — this method
        only validates the structural shape of the ``latency:`` block
        itself.
        """
        block = mod.get('latency')
        if block is None:
            return
        if not isinstance(block, dict):
            self.add_error('module', f"module '{name}' has 'latency:' that is not a mapping",
                            location=f"{loc}.latency")
            return

        kind = block.get('kind')
        if kind not in _KNOWN_LATENCY_KINDS:
            self.add_error(
                'module',
                f"module '{name}' has latency.kind {kind!r} — must be one of "
                f"{sorted(_KNOWN_LATENCY_KINDS)}",
                location=f"{loc}.latency.kind",
            )
            return

        unknown_keys = set(block) - {'kind', 'cycles', 'min_cycles', 'max_cycles'}
        if unknown_keys:
            self.add_error(
                'module',
                f"module '{name}' has unknown key(s) in 'latency:': {sorted(unknown_keys)}",
                location=f"{loc}.latency",
            )

        if kind == 'fixed':
            if block.get('cycles') is None:
                self.add_error(
                    'module', f"module '{name}' has latency.kind='fixed' but no 'cycles'",
                    location=f"{loc}.latency.cycles",
                    suggestion="Add 'cycles: <N>' or change kind to 'bounded'/'elastic'.",
                )
            if 'min_cycles' in block or 'max_cycles' in block:
                self.add_error(
                    'module',
                    f"module '{name}' has latency.kind='fixed' but also declares "
                    "min_cycles/max_cycles (only valid for kind='bounded')",
                    location=f"{loc}.latency",
                )
        elif kind == 'bounded':
            min_c, max_c = block.get('min_cycles'), block.get('max_cycles')
            if min_c is None or max_c is None:
                self.add_error(
                    'module',
                    f"module '{name}' has latency.kind='bounded' but is missing "
                    "'min_cycles'/'max_cycles'",
                    location=f"{loc}.latency",
                )
            elif (
                not isinstance(min_c, int) or not isinstance(max_c, int)
                or isinstance(min_c, bool) or isinstance(max_c, bool)
            ):
                self.add_error(
                    'module', f"module '{name}' has non-integer latency.min_cycles/max_cycles",
                    location=f"{loc}.latency",
                )
            elif min_c > max_c:
                self.add_error(
                    'module',
                    f"module '{name}' has latency.min_cycles ({min_c}) > max_cycles ({max_c})",
                    location=f"{loc}.latency",
                )
            if 'cycles' in block:
                self.add_error(
                    'module',
                    f"module '{name}' has latency.kind='bounded' but also declares "
                    "'cycles' (only valid for kind='fixed')",
                    location=f"{loc}.latency",
                )
        elif kind == 'elastic':
            stray = {'cycles', 'min_cycles', 'max_cycles'} & set(block)
            if stray:
                self.add_error(
                    'module',
                    f"module '{name}' has latency.kind='elastic' but also declares "
                    f"{sorted(stray)} — elastic timing has no fixed cycle count",
                    location=f"{loc}.latency",
                )

    def _validate_kind_requirements(self, mod: dict, name: str, kind: Optional[str], loc: str):
        """Enforce what each kind needs to be buildable / elaboratable."""
        if kind == 'hls':
            if not mod.get('top'):
                self.add_error('module', f"HLS module '{name}' missing 'top' (top-level function name)",
                               location=f"{loc}.top",
                               suggestion="Add: top: my_top_level_function")
            src = mod.get('src') or []
            if not src:
                self.add_error('module', f"HLS module '{name}' missing 'src'",
                               location=f"{loc}.src",
                               suggestion="Add: src: [algo/my_module/my_module.cpp]")
            else:
                self._check_src_paths(src, name, loc)
                for s in src:
                    if Path(str(s)).suffix.lower() not in _KNOWN_HLS_EXTENSIONS:
                        self.add_warning('module',
                                         f"HLS module '{name}' has non-C/C++ source: {s}",
                                         location=f"{loc}.src",
                                         suggestion="HLS sources are expected to have .cpp / .c / .cxx / .cc extensions")
            if 'build' not in mod:
                self.add_info('module', f"HLS module '{name}' has no build: section",
                              location=loc,
                              suggestion="Add build: with stages: when ready to orchestrate HLS")

        elif kind == 'rtl':
            if not mod.get('top'):
                self.add_error('module', f"RTL module '{name}' missing 'top' (entity/module name)",
                               location=f"{loc}.top",
                               suggestion="Add: top: my_module_entity")
            src = mod.get('src') or []
            if not src:
                self.add_error('module', f"RTL module '{name}' missing 'src'",
                               location=f"{loc}.src",
                               suggestion="Add: src: [hdl/my_module.v]")
            else:
                self._check_src_paths(src, name, loc)
                rtl_lang = mod.get('rtl_lang', 'verilog')
                expected = _KNOWN_RTL_EXTENSIONS.get(rtl_lang, {'.v', '.sv'})
                for s in src:
                    ext = Path(str(s)).suffix.lower()
                    if ext not in expected:
                        self.add_warning('module',
                                         f"RTL module '{name}' (lang={rtl_lang}) has unexpected source extension: {s}",
                                         location=f"{loc}.src",
                                         suggestion=f"Expected extensions for {rtl_lang}: {', '.join(sorted(expected))}")
            if not mod.get('rtl_lang'):
                self.add_warning('module', f"RTL module '{name}' missing 'rtl_lang'",
                                 location=f"{loc}.rtl_lang",
                                 suggestion="Add: rtl_lang: verilog  or  rtl_lang: vhdl")

        # kind: framework-utility — no structural requirements beyond name/kind

    def _check_src_paths(self, src: list, module_name: str, loc: str):
        """Fail on env-var-expanded paths — the plugin must be self-contained."""
        for s in src:
            if '${' in str(s):
                self.add_error('module',
                               f"Module '{module_name}' src contains an env-var reference: {s}",
                               location=f"{loc}.src",
                               suggestion=(
                                   "Plugin src paths must be plugin-relative and must not "
                                   "reference env vars.  Move the file into the plugin tree "
                                   "and use a relative path (e.g. algo/hdl/verilog/my_file.v)."
                               ))

    def _validate_build_section(self, build: Any, module_name: str,
                                 kind: Optional[str], loc: str):
        """Validate build: section independently when present."""
        if not isinstance(build, dict):
            self.add_error('build', f"Module '{module_name}' build: must be a mapping",
                           location=loc)
            return

        # stages — required when build: is declared
        stages = build.get('stages')
        if stages is None:
            self.add_error('build', f"Module '{module_name}' build: missing 'stages'",
                           location=f"{loc}.stages",
                           suggestion="Add: stages: [csim, synth, cosim, export]")
        elif not isinstance(stages, list) or len(stages) == 0:
            self.add_error('build', f"Module '{module_name}' build.stages must be a non-empty list",
                           location=f"{loc}.stages")
        else:
            unknown = set(stages) - _KNOWN_HLS_STAGES
            if unknown:
                self.add_error('build',
                               f"Module '{module_name}' build.stages contains unknown values: {sorted(unknown)}",
                               location=f"{loc}.stages",
                               suggestion=f"Valid stages: {sorted(_KNOWN_HLS_STAGES)}")

        # string option fields
        for opt_key in ('csim_opts', 'synth_opts', 'cosim_opts', 'export_opts'):
            val = build.get(opt_key)
            if val is not None and not isinstance(val, str):
                self.add_error('build',
                               f"Module '{module_name}' build.{opt_key} must be a string "
                               f"(got {type(val).__name__})",
                               location=f"{loc}.{opt_key}")

        # aliases
        aliases = build.get('aliases')
        if aliases is not None:
            if not isinstance(aliases, list):
                self.add_error('build', f"Module '{module_name}' build.aliases must be a list",
                               location=f"{loc}.aliases")
            else:
                for a in aliases:
                    if not isinstance(a, str):
                        self.add_error('build',
                                       f"Module '{module_name}' build.aliases entries must be strings "
                                       f"(got {type(a).__name__})",
                                       location=f"{loc}.aliases")

        # build: on a non-HLS module is unusual — warn
        if kind in ('rtl', 'framework-utility'):
            self.add_warning('build',
                             f"Module '{module_name}' (kind={kind}) has a build: section; "
                             "build: is intended for HLS modules",
                             location=loc,
                             suggestion="Verify this is intentional")

        self._validate_unknown_keys(build, _KNOWN_BUILD_KEYS, loc)

    def _validate_verify_section(self, verify: Any, module_name: str, loc: str):
        """Validate verify: section independently when present."""
        if not isinstance(verify, dict):
            self.add_error('verify', f"Module '{module_name}' verify: must be a mapping",
                           location=loc)
            return

        tb_src = verify.get('tb_src')
        if tb_src is not None:
            if not isinstance(tb_src, list) or len(tb_src) == 0:
                self.add_error('verify',
                               f"Module '{module_name}' verify.tb_src must be a non-empty list",
                               location=f"{loc}.tb_src")

        for str_key in ('testbench_target', 'tb_args', 'cosim_tb_args', 'adapter'):
            val = verify.get(str_key)
            if val is not None and not isinstance(val, str):
                self.add_error('verify',
                               f"Module '{module_name}' verify.{str_key} must be a string "
                               f"(got {type(val).__name__})",
                               location=f"{loc}.{str_key}")

        self._validate_unknown_keys(verify, _KNOWN_VERIFY_KEYS, loc)

    def _validate_unknown_keys(self, mapping: Dict[str, Any], known: Set[str], loc: str):
        for k in mapping:
            if k not in known:
                self.add_warning('schema', f"Unrecognised key '{k}' at {loc}",
                                 suggestion=f"Known keys: {', '.join(sorted(known))}")


def validate_registry(registry_path: Path) -> 'RegistryValidator':
    """Load and validate a modules.yml registry file.

    Returns a RegistryValidator populated with results.  Does not raise on
    validation failures — check .has_errors() and .errors for details.
    """
    import yaml as _yaml
    raw = _yaml.safe_load(registry_path.read_text()) or {}
    validator = RegistryValidator(raw, registry_path)
    validator.validate_all()
    return validator
