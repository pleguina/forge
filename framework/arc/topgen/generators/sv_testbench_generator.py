"""SystemVerilog testbench generator for algo_top.

Generates the thin XSIM scaffold that includes tb_bindings.svh, instantiates
algo_top by name, and consumes stimulus_current.svh.
"""

import subprocess
import json
import os
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Any


class SVTestbenchGenerator:
    """Generate the SystemVerilog testbench scaffold for algo_top."""

    def __init__(
        self,
        rtl_path: Path,
        params_path: Optional[Path] = None,
        stimulus_converter_path: Optional[Path] = None,
    ):
        """
        Args:
            rtl_path: Path to algo_top.v file
            params_path: Path to design_parameters.json
                         (auto-detected from rtl_path.parent if None)
        """
        self.rtl_path = rtl_path
        self.output_dir = rtl_path.parent
        self.stimulus_converter_path = stimulus_converter_path.resolve() if stimulus_converter_path else None

        # Load design parameters
        if params_path is None:
            params_path = self.output_dir / "design_parameters.json"

        if not params_path.exists():
            raise FileNotFoundError(
                f"design_parameters.json not found at {params_path}\n"
                f"Please generate it with: topgen gen-top <design.yml> --mode verilog"
            )

        with open(params_path, 'r') as f:
            self.params = json.load(f)

        # Port/interface shape must come from generated metadata, not TB fallbacks.
        port_map_path = self.output_dir / "port_map.yaml"
        if not port_map_path.exists():
            raise FileNotFoundError(
                f"port_map.yaml not found at {port_map_path}\n"
                f"Please regenerate with: topgen gen-top <design.yml> --mode verilog"
            )

        import yaml
        with open(port_map_path, 'r') as f:
            self.port_map = yaml.safe_load(f) or {}

        if not self.port_map.get('port_groups'):
            raise ValueError(
                f"port_map.yaml at {port_map_path} is missing port_groups; regenerate the design artifacts"
            )

        # Load probe_map.yaml for Tier 2 probe enumeration when available.
        self.probe_map: Optional[Dict] = None
        probe_map_path = self.output_dir / "probe_map.yaml"
        if probe_map_path.exists():
            with open(probe_map_path, 'r') as f:
                self.probe_map = yaml.safe_load(f) or {}

    def _output_ports(self) -> List[Dict[str, Any]]:
        """Return output port descriptors from port_map.yaml, preserving group declaration order.

        port_map.yaml uses port_groups (dict of lists); falls back to flat 'ports' list.
        Group ordering follows the declaration order in port_groups, so the consumer
        controls output ordering via their port_map.yaml — no group names are hard-coded
        here.
        """
        if self.port_map is None:
            return []
        # New format: port_groups dict; old/flat format: ports list.
        # Iterate in insertion order (Python 3.7+ dicts are ordered) so that
        # the output sequence reflects the consumer's port_map.yaml declaration.
        port_groups = self.port_map.get('port_groups', {})
        if port_groups:
            all_ports: List[Dict[str, Any]] = []
            for grp_ports in port_groups.values():
                if isinstance(grp_ports, list):
                    all_ports.extend(grp_ports)
        else:
            all_ports = self.port_map.get('ports', [])
        return [p for p in all_ports if p.get('direction') == 'output']

    def _has_port(self, name: str) -> bool:
        """Return True if `name` is declared as a top-level port in port_map.yaml."""
        if self.port_map is None:
            return False
        port_groups = self.port_map.get('port_groups', {})
        for grp_ports in port_groups.values():
            if isinstance(grp_ports, list):
                for p in grp_ports:
                    if isinstance(p, dict) and p.get('name') == name:
                        return True
        # fallback: flat ports list
        for p in self.port_map.get('ports', []):
            if isinstance(p, dict) and p.get('name') == name:
                return True
        return False

    def _tier2_probes(self) -> List[Dict[str, Any]]:
        """Return Tier 2 probe descriptors from probe_map.yaml or port_map.yaml."""
        if self.probe_map is not None:
            probe_tiers = self.probe_map.get('probe_tiers', {})
            tier2 = probe_tiers.get('tier2', [])
            if isinstance(tier2, list):
                return tier2

        if self.port_map is not None:
            tier2 = self.port_map.get('tier2_probes', [])
            if isinstance(tier2, list):
                return tier2

        return []

    def _port_group(self, name: str) -> Dict[str, Any]:
        if self.port_map is None:
            return {}
        group = self.port_map.get('port_groups', {}).get(name, {})
        return group if isinstance(group, dict) else {}

    def _channel_group(self, name: str) -> List[Dict[str, Any]]:
        group = self._port_group(name)
        if group.get('channels'):
            return group['channels']
        if group.get('ports'):
            return group['ports']
        return []

    def _config_stimulus_groups(self) -> List[Dict[str, Any]]:
        """Return stimulus groups from all config-style input groups."""
        port_groups = self.port_map.get('port_groups', {}) if self.port_map else {}
        result: List[Dict[str, Any]] = []
        for group_name, group_data in port_groups.items():
            if not isinstance(group_data, dict) or 'stimulus_groups' not in group_data:
                continue
            parent_width = int(group_data.get('width', 64))
            for subgroup in group_data.get('stimulus_groups', []):
                subgroup_copy = dict(subgroup)
                subgroup_copy.setdefault('width', parent_width)
                subgroup_copy.setdefault('parent_group', group_name)
                result.append(subgroup_copy)
        return result

    def _grouped_input_groups(self) -> List[Dict[str, Any]]:
        """Return sub-groups from all grouped input groups in port_map."""
        port_groups = self.port_map.get('port_groups', {}) if self.port_map else {}
        result: List[Dict[str, Any]] = []
        for parent_name, group_data in port_groups.items():
            if not isinstance(group_data, dict) or 'groups' not in group_data:
                continue
            for subgroup in group_data.get('groups', []):
                subgroup_copy = dict(subgroup)
                subgroup_copy.setdefault('parent_group', parent_name)
                if not subgroup_copy.get('stimulus_prefix'):
                    subgroup_copy['stimulus_prefix'] = subgroup_copy.get('name', parent_name).upper()
                result.append(subgroup_copy)
        return result

    def _all_channel_groups(self) -> List[tuple]:
        """Return (group_name, entries, channel_width, stimulus_prefix) for all simple channel groups."""
        port_groups = self.port_map.get('port_groups', {}) if self.port_map else {}
        SKIP = {'clock_reset', 'bx0', 'outputs', 'unclassified'}
        result = []
        for group_name, group_data in port_groups.items():
            if group_name in SKIP:
                continue
            if not isinstance(group_data, dict):
                continue
            if 'channels' in group_data:
                channels = group_data.get('channels', [])
                width = group_data.get('channel_width', 0)
                prefix = group_data.get('stimulus_prefix') or (group_name.upper() + '_STIM')
                result.append((group_name, channels, width, prefix))
        return result

    def _find_probe(self, probe_name: str) -> Optional[Dict[str, Any]]:
        if self.probe_map is not None:
            for probe in self.probe_map.get('probe_tiers', {}).get('tier2', []):
                if isinstance(probe, dict) and probe.get('name') == probe_name:
                    return probe

        for probe in self.port_map.get('tier2_probes', []):
            if isinstance(probe, dict) and probe.get('name') == probe_name:
                return probe

        return None

    def _find_converter_tool(self) -> Optional[Path]:
        """
        Search for xml_to_sv_stimulus binary
        
        Returns:
            Path to tool if found, None otherwise
        """
        candidates: List[Path] = []
        if self.stimulus_converter_path is not None:
            candidates.append(self.stimulus_converter_path)

        env_tool = os.environ.get("TOPGEN_XML_TO_SV_STIMULUS")
        if env_tool:
            candidates.append(Path(env_tool).expanduser().resolve())

        which_tool = shutil.which("xml_to_sv_stimulus")
        if which_tool:
            candidates.append(Path(which_tool).resolve())
        
        print(f"   🔍 Searching for xml_to_sv_stimulus converter tool...")
        for candidate in candidates:
            print(f"      Checking: {candidate}")
            if candidate.exists() and candidate.is_file():
                # Check if executable
                import os
                if os.access(candidate, os.X_OK):
                    print(f"      ✓ Found: {candidate}")
                    return candidate
                else:
                    print(f"      ⚠️  File exists but not executable: {candidate}")
        
        print(f"      ✗ Not found in any location")
        return None
    
    def _generate_stimulus_include(
        self, 
        xml_path: Path, 
        output_dir: Path, 
        event_id: int = 1
    ) -> Optional[Path]:
        """
        Call C++ tool to generate .svh stimulus file from XML
        
        RESPONSIBILITY: Orchestrate C++ tool, NOT parse XML
        
        Args:
            xml_path: Path to XML test data
            output_dir: Directory to write .svh file
            event_id: Event number to extract
        
        Returns:
            Path to generated .svh file, or None if tool not available
        """
        converter_tool = self._find_converter_tool()
        
        if converter_tool is None:
            print("   ⚠️  xml_to_sv_stimulus converter not found")
            print("   📝 Testbench will use placeholder stimulus (zeros)")
            print("   🔧 Provide --xml-stimulus-tool, set TOPGEN_XML_TO_SV_STIMULUS, or put xml_to_sv_stimulus on PATH")
            return None
        
        svh_output = output_dir / f"stimulus_event_{event_id}.svh"
        
        cmd = [
            str(converter_tool),
            "--xml", str(xml_path),
            "--output", str(svh_output),
            "--event-id", str(event_id)
        ]
        
        try:
            print(f"   🔄 Converting XML → SystemVerilog stimulus...")
            print(f"      Tool: {converter_tool}")
            print(f"      XML:  {xml_path}")
            print(f"      Event ID: {event_id}")
            print(f"      Output: {svh_output}")
            
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            if result.stdout.strip():
                print(f"   ✓ {result.stdout.strip()}")
            
            if svh_output.exists():
                # Count how many BX worth of data generated
                size_kb = svh_output.stat().st_size / 1024
                print(f"   ✓ Stimulus file generated: {svh_output.name} ({size_kb:.1f} KB)")
                return svh_output
            else:
                print(f"   ⚠️  Tool ran but output file not created")
                return None
                
        except subprocess.CalledProcessError as e:
            print(f"   ⚠️  Stimulus conversion failed!")
            print(f"      Command: {' '.join(cmd)}")
            if e.stdout:
                print(f"      Output: {e.stdout}")
            if e.stderr:
                print(f"      Error: {e.stderr}")
            print(f"   📝 Will use placeholder stimulus instead")
            return None
        except Exception as e:
            print(f"   ⚠️  Unexpected error: {e}")
            print(f"   📝 Will use placeholder stimulus instead")
            return None
    
    def _generate_placeholder_stimulus(self, output_dir: Path, event_id: int = 1) -> Path:
        """
        Generate placeholder .svh file with idle patterns
        
        Used when C++ tool is not available or XML not provided
        Uses design parameters for channel counts and batches
        """
        svh_output = output_dir / f"stimulus_event_{event_id}.svh"
        
        print(f"   📝 Generating placeholder stimulus (all zeros)...")
        
        # Get parameters
        num_batches = self.params['timing']['batches_per_event']
        all_channel_groups = self._all_channel_groups()
        config_groups = self._config_stimulus_groups()
        grouped_input_groups = self._grouped_input_groups()
        
        total_channels = sum(len(chs) for _, chs, _, _ in all_channel_groups)
        print(f"      Config: {num_batches} batches, {total_channels} input channels across {len(all_channel_groups)} groups")
        
        with open(svh_output, 'w') as f:
            f.write("//============================================================================\n")
            f.write("// Placeholder Stimulus File\n")
            f.write("//\n")
            f.write("// IMPORTANT: This is a placeholder with idle patterns.\n")
            f.write("//            Real stimulus requires building build/xml_to_sv_stimulus\n")
            f.write("//============================================================================\n\n")
            
            f.write(f"localparam int NUM_BATCHES = {num_batches};\n\n")
            
            for config_group in config_groups:
                group_ports = config_group.get('ports', [])
                width = config_group.get('width', 64)
                prefix = config_group.get('stimulus_prefix', 'CFG')
                f.write(f"// {config_group.get('name', 'Configuration')} (placeholder)\n")
                for i, _port in enumerate(group_ports):
                    f.write(f"localparam logic [{width - 1}:0] {prefix}_{i} = {width}'h0;\n")
                f.write("\n")

            for grp_name, channels, grp_width, grp_prefix in all_channel_groups:
                f.write(f"// {grp_name} Stimulus (idle pattern)\n")
                for i, _port in enumerate(channels):
                    f.write(f"const logic [{grp_width - 1}:0] {grp_prefix}_{i}[NUM_BATCHES] = '{{\n")
                    for batch in range(num_batches):
                        f.write(f"    {grp_width}'h1")
                        if batch < num_batches - 1:
                            f.write(",")
                        f.write(f"  // Batch {batch}\n")
                    f.write("};\n\n")

            for grouped_input in grouped_input_groups:
                group_ports = grouped_input.get('ports', [])
                if not group_ports:
                    continue
                group_width = group_ports[0].get('width', 67)
                prefix = grouped_input.get('stimulus_prefix') or grouped_input.get('name', 'INPUT').upper()
                label = grouped_input.get('name', grouped_input.get('parent_group', 'grouped'))
                f.write(f"// {label} stimulus (idle pattern)\n")
                for i, _port in enumerate(group_ports):
                    f.write(f"const logic [{group_width - 1}:0] {prefix}_{i}[NUM_BATCHES] = '{{\n")
                    for batch in range(num_batches):
                        f.write(f"    {group_width}'h0")
                        if batch < num_batches - 1:
                            f.write(",")
                        f.write(f"  // Batch {batch}\n")
                    f.write("};\n\n")
        
        size_kb = svh_output.stat().st_size / 1024
        print(f"   ✓ Placeholder stimulus file: {svh_output.name} ({size_kb:.1f} KB)")
        return svh_output
    
    def generate_testbench(
        self, 
        output_dir: Path, 
        xml_stimulus_path: Optional[Path] = None,
        event_id: int = 1
    ) -> Path:
        """
        Generate complete SystemVerilog testbench
        
        Args:
            output_dir: Directory to write testbench and stimulus files
            xml_stimulus_path: Optional path to XML for stimulus generation
            event_id: Event ID to extract from XML (default: 1)
        
        Returns:
            Path to generated tb_algo_top.sv
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        tb_path = output_dir / "tb_algo_top.sv"
        
        print("Generating SystemVerilog testbench...")
        
        # Generate stimulus include file
        # run.sh swaps the event-specific file into stimulus_current.svh.
        has_real_stimulus = False
        stimulus_filename = "stimulus_current.svh"
        if xml_stimulus_path and xml_stimulus_path.exists():
            svh_path = self._generate_stimulus_include(xml_stimulus_path, output_dir, event_id)
            if svh_path and svh_path.exists():
                has_real_stimulus = True
                print(f"  ✓ Real stimulus from XML: {svh_path.name}")
            else:
                self._generate_placeholder_stimulus(output_dir, event_id)
                print("  ⚠ Using placeholder stimulus")
        else:
            self._generate_placeholder_stimulus(output_dir, event_id)
            print("  ℹ No XML provided, using placeholder stimulus")
        
        # Generate testbench
        with open(tb_path, 'w') as f:
            self._write_testbench(f, has_real_stimulus, stimulus_filename)
        
        print(f"✓ Generated testbench: {tb_path}")
        return tb_path
    
    def _write_testbench(self, f, has_real_stimulus: bool, stimulus_filename: str):
        """
                Write the testbench scaffold using tb_bindings.svh, algo_top dut (.*),
                and stimulus_current.svh.
        """
        timing = self.params['timing']
        clock_period = timing['clock_period_ns']
        algo_freq    = timing['algo_freq_mhz']
        all_channel_groups = self._all_channel_groups()  # (name, entries, width, prefix)
        config_groups = self._config_stimulus_groups()
        grouped_input_groups = self._grouped_input_groups()

        # Collect port sig hash from port_map if available
        port_sig_hash = ""
        if self.port_map:
            port_sig_hash = self.port_map.get('port_signature_hash', '')

        # ------------------------------------------------------------------ header
        f.write("`timescale 1ns / 1ps\n\n")
        f.write("//============================================================================\n")
        f.write("// Auto-generated SystemVerilog Testbench\n")
        f.write("//\n")
        f.write("// Generated by: topgen (Python) — XSIM_DEFINITIVE_ADAPTATION\n")
        f.write("//\n")
        f.write("// Port declarations and DUT instantiation are generated artifacts:\n")
        f.write("//   - Port decls : `include \"tb_bindings.svh\"")
        if port_sig_hash:
            f.write(f"  (port sig hash {port_sig_hash})")
        f.write("\n")
        f.write("//   - DUT inst   : algo_top dut (.*)  (name-based auto-connect)\n")
        f.write("//\n")
        f.write("// Stimulus data: " + ("C++ tool (xml_to_sv_stimulus)" if has_real_stimulus else "Placeholder") + "\n")
        f.write("//============================================================================\n\n")

        # ------------------------------------------------------------------ module
        f.write("module tb_algo_top;\n\n")

        # ------------------------------------------------------------------ includes
        f.write("// ---- Generated port declarations (DO NOT EDIT — see tb_bindings.svh) ----\n")
        f.write("`include \"tb_bindings.svh\"\n\n")
        f.write("// ---- Stimulus data (generated by xml_to_sv_stimulus) ----\n")
        f.write(f"`include \"{stimulus_filename}\"\n\n")

        # ------------------------------------------------------------------ parameters
        f.write("// ---- Simulation parameters ----\n")
        f.write(f"localparam real CLK_PERIOD_NS            = {clock_period};\n")
        f.write( "localparam int  RESET_CYCLES              = 8;\n")
        f.write( "localparam int  POST_CONFIG_SETTLE_CYCLES = 5;\n")
        f.write( "localparam int  POST_STIMULUS_DRAIN_CYCLES = 150;\n\n")

        f.write("// Probe-log flag (set via +define+PROBE_LOG=1 at compile time)\n")
        f.write("`ifndef PROBE_LOG\n")
        f.write("    localparam int PROBE_LOG = 0;\n")
        f.write("`else\n")
        f.write("    localparam int PROBE_LOG = `PROBE_LOG;\n")
        f.write("`endif\n\n")

        # ------------------------------------------------------------------ clock
        f.write(f"// ---- Clock generation ({algo_freq} MHz => {clock_period}ns period) ----\n")
        f.write("initial ap_clk = 1'b0;\n")
        f.write("always #(CLK_PERIOD_NS / 2.0) ap_clk = ~ap_clk;\n\n")

        # ------------------------------------------------------------------ DUT
        f.write("// ---- DUT instantiation (name-based — all ports resolved from tb_bindings.svh) ----\n")
        f.write("algo_top dut (.*);\n\n")

        # Some designs do not expose bx_timing_cycle_in_bx as a top-level port.
        if not self._has_port('bx_timing_cycle_in_bx'):
            cycle_probe = self._find_probe('cycle_in_bx')
            if cycle_probe is None:
                raise ValueError(
                    "probe_map.yaml/port_map.yaml is missing the cycle_in_bx Tier 2 probe; "
                    "regenerate the design artifacts"
                )
            cycle_net = cycle_probe.get('net')
            cycle_width = int(cycle_probe.get('width', 1))
            if not cycle_net:
                raise ValueError(
                    "cycle_in_bx probe metadata is missing its net binding; regenerate the design artifacts"
                )
            if cycle_width == 1:
                f.write("// bx_timing_cycle_in_bx is not a top-level port → generated probe-map alias\n")
                f.write(f"wire bx_timing_cycle_in_bx = {cycle_net};\n\n")
            else:
                f.write("// bx_timing_cycle_in_bx is not a top-level port → generated probe-map alias\n")
                f.write(f"wire [{cycle_width - 1}:0] bx_timing_cycle_in_bx = {cycle_net};\n\n")

        # ------------------------------------------------------------------ SVA
        f.write("//============================================================================\n")
        f.write("// Phase 7 — No-X / no-Z assertions on key outputs (after reset deasserts)\n")
        f.write("// Add design-specific properties here; refer to port_map.yaml for port names.\n")
        f.write("//============================================================================\n")
        f.write("// Example (replace signal names as appropriate):\n")
        f.write("// property p_no_x_out_valid;\n")
        f.write("//     @(posedge ap_clk) disable iff (ap_rst)\n")
        f.write("//     !$isunknown(out_valid_signal);\n")
        f.write("// endproperty\n")
        f.write("// assert property (p_no_x_out_valid)\n")
        f.write("//     else $warning(\"[ASSERT] out_valid is X/Z at cycle %0d\", cycle_count);\n\n")

        # ------------------------------------------------------------------ drive_all_idle
        f.write("//============================================================================\n")
        f.write("// Task: drive_all_idle\n")
        f.write("// Sets all DUT inputs to zero (use before/after stimulus windows)\n")
        f.write("//============================================================================\n")
        f.write("task automatic drive_all_idle();\n")
        f.write("    begin\n")

        for _grp_name, channels, _width, _prefix in all_channel_groups:
            if channels:
                idle_names = [f"{port['name']} = '0" for port in channels]
                for chunk_start in range(0, len(idle_names), 4):
                    chunk = idle_names[chunk_start:chunk_start + 4]
                    f.write(f"        {'; '.join(chunk)};\n")
                f.write("\n")

        # cfg_N_slr_cfg_reg is not cleared here because cfg_valid pulses are tied
        # to register changes in cfg64_from_framework.v.

        for grouped_input in grouped_input_groups:
            grouped_idle = [f"{port['name']} = '0" for port in grouped_input.get('ports', [])]
            if grouped_idle:
                f.write(f"        {'; '.join(grouped_idle)};\n")
        f.write("\n")

        f.write("        bx_timing_bx0_global = 1'b0;\n")
        f.write("    end\n")
        f.write("endtask\n\n")

        # ------------------------------------------------------------------ apply_configs
        f.write("//============================================================================\n")
        f.write("// Task: apply_configs\n")
        f.write("// Loads per-channel configuration words from stimulus SVH constants\n")
        f.write("//============================================================================\n")
        f.write("task automatic apply_configs();\n")
        f.write("    begin\n")
        for config_group in config_groups:
            assignments = [
                f"{port['name']} = {config_group.get('stimulus_prefix', 'CFG')}_{index}"
                for index, port in enumerate(config_group.get('ports', []))
            ]
            for chunk_start in range(0, len(assignments), 4):
                chunk = assignments[chunk_start:chunk_start + 4]
                if chunk:
                    f.write(f"        {'; '.join(chunk)};\n")
        f.write("    end\n")
        f.write("endtask\n\n")

        # ------------------------------------------------------------------ apply_batch
        f.write("//============================================================================\n")
        f.write("// Task: apply_batch\n")
        f.write("// Drives one stimulus batch onto all input channel groups\n")
        f.write("//============================================================================\n")
        f.write("task automatic apply_batch(input int batch_idx);\n")
        f.write("    begin\n")
        for _grp_name, channels, _width, grp_prefix in all_channel_groups:
            stim = [f"{port['name']} = {grp_prefix}_{index}[batch_idx]" for index, port in enumerate(channels)]
            for chunk_start in range(0, len(stim), 4):
                chunk = stim[chunk_start:chunk_start + 4]
                if chunk:
                    f.write(f"        {'; '.join(chunk)};\n")
            if stim:
                f.write("\n")
        for grouped_input in grouped_input_groups:
            group_prefix = grouped_input.get('stimulus_prefix') or grouped_input.get('name', 'INPUT').upper()
            grouped_stim = [
                f"{port['name']} = {group_prefix}_{index}[batch_idx]"
                for index, port in enumerate(grouped_input.get('ports', []))
            ]
            if grouped_stim:
                f.write(f"        {'; '.join(grouped_stim)};\n")
        f.write("    end\n")
        f.write("endtask\n\n")

        # ------------------------------------------------------------------ clocked logging
        out_ports = self._output_ports()
        tier2_probes = self._tier2_probes()

        f.write("//============================================================================\n")
        f.write("// Clocked logging — Tier 1 always, Tier 2 when PROBE_LOG=1\n")
        f.write("//============================================================================\n")
        f.write("always @(posedge ap_clk) begin\n")
        f.write("    if (!ap_rst) begin\n")
        f.write("        cycle_count <= cycle_count + 1;\n")
        if out_ports:
            # Build format string and argument list for $fwrite
            # Column order must match algo_top_xsim_checker: cycle,bx0,unconstr...,constr...,nn...
            fmt_parts = ["%0d", "%0b"]   # cycle_count, bx_timing_bx0_global
            arg_parts = ["cycle_count", "bx_timing_bx0_global"]
            for p in out_ports:
                name  = p['name']
                width = p.get('width', 1)
                fmt_parts.append("%0h" if width > 1 else "%0b")
                arg_parts.append(name)
            fmt_str  = ",".join(fmt_parts) + "\\n"
            args_str = ", ".join(arg_parts)
            f.write(f"        $fwrite(out_csv_fd, \"{fmt_str}\", {args_str});\n")
        f.write("\n")
        f.write("        // Tier 2 probe capture (only when compiled with +define+PROBE_LOG=1)\n")
        f.write("        if (PROBE_LOG) begin\n")
        if tier2_probes:
            probe_fmt_parts = ["%0d"]
            probe_arg_parts = ["cycle_count"]
            for probe in tier2_probes:
                width = int(probe.get('width', 1))
                probe_fmt_parts.append("%0h" if width > 1 else "%0b")
                probe_arg_parts.append(str(probe['net']))
            probe_fmt_str = ",".join(probe_fmt_parts) + "\\n"
            probe_args_str = ", ".join(probe_arg_parts)
            f.write(f"            $fwrite(probe_csv_fd, \"{probe_fmt_str}\", {probe_args_str});\n")
        else:
            f.write("            $fwrite(probe_csv_fd, \"%0d\\n\", cycle_count);\n")
            f.write("            // No Tier 2 probes declared in probe_map.yaml\n")
        f.write("        end\n")
        f.write("    end\n")
        f.write("end\n\n")

        # ------------------------------------------------------------------ main
        f.write("//============================================================================\n")
        f.write("// Main simulation flow\n")
        f.write("//============================================================================\n")
        f.write("initial begin\n")
        f.write("    cycle_count = 0;\n")
        f.write("    ap_rst      = 1'b1;\n")
        f.write("    bx_timing_bx0_global = 1'b0;\n")
        f.write("    drive_all_idle();\n\n")

        # Open output CSV
        f.write("    // Open Tier 1 output CSV\n")
        f.write("    out_csv_fd = $fopen(\"algo_top_outputs.csv\", \"w\");\n")
        f.write("    if (out_csv_fd == 0) $fatal(1, \"Failed to open algo_top_outputs.csv\");\n")
        # CSV header — cycle, bx0, then output ports in group-sorted order
        header_cols = ["cycle", "bx0"] + [p['name'] for p in out_ports]
        f.write(f"    $fwrite(out_csv_fd, \"{','.join(header_cols)}\\n\");\n\n")

        # Probe CSV
        f.write("    // Open Tier 2 probe CSV (only when PROBE_LOG compiled in)\n")
        f.write("    if (PROBE_LOG) begin\n")
        f.write("        probe_csv_fd = $fopen(\"algo_top_probe.csv\", \"w\");\n")
        f.write("        if (probe_csv_fd == 0) $fatal(1, \"Failed to open algo_top_probe.csv\");\n")
        probe_header_cols = ["cycle"] + [probe["name"] for probe in tier2_probes]
        f.write(f"        $fwrite(probe_csv_fd, \"{','.join(probe_header_cols)}\\n\");\n")
        f.write("        $display(\"[PROBE] Tier 2 probe capture ENABLED -> algo_top_probe.csv\");\n")
        f.write("    end\n\n")

        # Reset sequence
        f.write("    // Reset sequence\n")
        f.write("    repeat (RESET_CYCLES) @(posedge ap_clk);\n")
        f.write("    ap_rst = 1'b0;\n\n")

        # Config + settle
        f.write("    apply_configs();\n")
        f.write("    repeat (POST_CONFIG_SETTLE_CYCLES) @(posedge ap_clk);\n\n")

        # BX alignment
        f.write("    // Align to a BX boundary (wait until cycle_in_bx == 8 -> next is 0)\n")
        f.write("    while (bx_timing_cycle_in_bx != 4'd8) @(posedge ap_clk);\n\n")

        # Stimulus loop
        f.write("    // Drive stimulus\n")
        f.write("    for (int batch = 0; batch < NUM_BATCHES; batch++) begin\n")
        f.write("        @(negedge ap_clk);\n")
        f.write("        apply_batch(batch);\n")
        f.write("        bx_timing_bx0_global = (batch == 0) ? 1'b1 : 1'b0;\n")
        f.write("        @(posedge ap_clk);\n")
        f.write("    end\n\n")

        # Drain
        f.write("    // Return to idle and drain pipeline\n")
        f.write("    @(negedge ap_clk);\n")
        f.write("    drive_all_idle();\n")
        f.write("    bx_timing_bx0_global = 1'b0;\n")
        f.write("    repeat (POST_STIMULUS_DRAIN_CYCLES) @(posedge ap_clk);\n\n")

        # Close files
        f.write("    $fclose(out_csv_fd);\n")
        f.write("    if (PROBE_LOG) $fclose(probe_csv_fd);\n\n")

        f.write("    $display(\"\\nTB PASS: algo_top_xsim completed\\n\");\n")
        f.write("    $finish;\n")
        f.write("end\n\n")

        f.write("endmodule\n")


def generate_sv_testbench(
    rtl_path: Path,
    output_dir: Path,
    xml_stimulus_path: Optional[Path] = None,
    event_id: int = 1,
    params_path: Optional[Path] = None,
    stimulus_converter_path: Optional[Path] = None,
) -> Path:
    """
    Convenience function to generate testbench
    
    Args:
        rtl_path: Path to algo_top.v
        output_dir: Output directory for testbench
        xml_stimulus_path: Optional XML for real stimulus
        event_id: Event ID to extract from XML (default: 1)
        params_path: Optional path to design_parameters.json (auto-detected if None)
    
    Returns:
        Path to generated tb_algo_top.sv
    """
    generator = SVTestbenchGenerator(rtl_path, params_path, stimulus_converter_path)
    return generator.generate_testbench(output_dir, xml_stimulus_path, event_id)
