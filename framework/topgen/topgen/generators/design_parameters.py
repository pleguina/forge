"""
Design Parameters Generator for Testbenches

Extracts all testbench-relevant parameters from design.yml and generates
a design_parameters.json file for consumption by testbenches (SystemVerilog,
XSIM-oriented SV testbenches, CSIM/COSIM utilities, and related tooling)

This provides a single source of truth for design constants used across
all test environments.
"""

from __future__ import annotations
import json
import datetime
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..config import DesignConfig


def parse_algo_top_ports(algo_top_v_path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """
    Parse algo_top.v to extract all port declarations with their metadata.
    
    This creates a complete port map showing:
    - Port name on algo_top
    - Direction (input/output)
    - Width in bits
    - Source module name
    - Instance number
    - Original port name on the module
    
    Args:
        algo_top_v_path: Path to generated algo_top.v file
        
    Returns:
        Dictionary with 'inputs' and 'outputs' lists, each containing port info dicts
    """
    
    if not algo_top_v_path.exists():
        return {'inputs': [], 'outputs': []}
    
    with open(algo_top_v_path, 'r') as f:
        content = f.read()
    
    ports = {'inputs': [], 'outputs': []}
    
    # Extract module declaration section
    # module algo_top (
    #   input ap_clk,
    #   input [66:0] dt_0_in_r,
    #   output [43:0] subdet_out_MB1_0,
    # );
    
    module_match = re.search(r'module\s+algo_top\s*\((.*?)\);', content, re.DOTALL)
    if not module_match:
        return ports
    
    port_section = module_match.group(1)
    
    # Parse each port line
    # Patterns:
    #   input ap_clk,
    #   input [66:0] dt_0_in_r,
    #   output [43:0] subdet_out_MB1_0,
    #   input [63:0] cfg_0_slr_cfg_reg,
    
    port_pattern = re.compile(r'(input|output)\s+(?:\[(\d+):(\d+)\]\s+)?(\w+)', re.MULTILINE)
    
    for match in port_pattern.finditer(port_section):
        direction = match.group(1)
        msb = match.group(2)
        lsb = match.group(3)
        port_name = match.group(4)
        
        # Calculate width
        if msb and lsb:
            width = int(msb) - int(lsb) + 1
        else:
            width = 1
        
        # Parse port name to extract module info
        # Port naming conventions:
        #   dt_0_in_r       → module='dt', instance=0, port='in_r'
        #   csc_15_in_r     → module='csc', instance=15, port='in_r'
        #   cfg_0_slr_cfg_reg → module='cfg', instance=0, port='slr_cfg_reg'
        #   subdet_out_MB1_0  → module='subdet', instance=0, port='out_MB1'
        #   ap_clk          → system signal
        #   new_event       → system signal
        #   bx_count_out    → system signal
        
        port_info = {
            'name': port_name,
            'direction': direction,
            'width': width,
            'msb': int(msb) if msb else 0,
            'lsb': int(lsb) if lsb else 0,
            'module': None,
            'instance': None,
            'module_port': None,
            'category': 'system'  # system, dt, csc, cfg, subdet, etc.
        }
        
        # System signals
        if port_name in ('ap_clk', 'ap_rst', 'ap_rst_n', 'new_event', 'bx_count_out'):
            port_info['category'] = 'system'
            port_info['module'] = 'system'
        # Debug ports: debug_<module>_<signal>_<instance> or debug_<module>_<signal>
        elif port_name.startswith('debug_'):
            # Pattern: debug_subdet_in_MB1_0 → module=subdet, signal=in_MB1, instance=0
            # Pattern: debug_best_sel_candidate_0 → module=best_sel, signal=candidate, instance=0
            debug_match = re.match(r'debug_(\w+)_(.+?)_(\d+)$', port_name)
            if debug_match:
                module = debug_match.group(1)
                signal = debug_match.group(2)
                instance = int(debug_match.group(3))

                port_info['category'] = 'debug'
                port_info['module'] = module
                port_info['instance'] = instance
                port_info['module_port'] = signal
                port_info['debug_source_module'] = module
                port_info['debug_source_signal'] = signal
            else:
                # Try without instance: debug_<module>_<signal>
                debug_match2 = re.match(r'debug_(\w+)_(.+)$', port_name)
                if debug_match2:
                    module = debug_match2.group(1)
                    signal = debug_match2.group(2)

                    port_info['category'] = 'debug'
                    port_info['module'] = module
                    port_info['instance'] = 0  # Default to 0
                    port_info['module_port'] = signal
                    port_info['debug_source_module'] = module
                    port_info['debug_source_signal'] = signal
                else:
                    # Couldn't parse debug port, mark as debug but unknown
                    port_info['category'] = 'debug'
        else:
            # Module-specific ports
            # Try to parse: module_instance_port or module_port_instance

            # Pattern 1: dt_0_in_r, csc_15_in_r, cfg_0_slr_cfg_reg
            match1 = re.match(r'(\w+?)_(\d+)_(.*)', port_name)
            if match1:
                module = match1.group(1)
                instance = int(match1.group(2))
                module_port = match1.group(3)

                port_info['module'] = module
                port_info['instance'] = instance
                port_info['module_port'] = module_port
                port_info['category'] = module
            else:
                # Pattern 2: subdet_out_MB1_0 → module=subdet, port=out_MB1, instance=0
                match2 = re.match(r'(\w+?)_(out_\w+)_(\d+)', port_name)
                if match2:
                    module = match2.group(1)
                    module_port = match2.group(2)
                    instance = int(match2.group(3))

                    port_info['module'] = module
                    port_info['instance'] = instance
                    port_info['module_port'] = module_port
                    port_info['category'] = module
                else:
                    # Unknown pattern - mark as other
                    port_info['category'] = 'other'
        
        # Add to appropriate list
        if direction == 'input':
            ports['inputs'].append(port_info)
        else:
            ports['outputs'].append(port_info)
    
    return ports


def _port_map_group_channels(port_map_data: Optional[Dict[str, Any]], group_name: str) -> List[Dict[str, Any]]:
    if not port_map_data:
        return []
    group = port_map_data.get('port_groups', {}).get(group_name, {})
    if isinstance(group, dict):
        if 'channels' in group:
            return group.get('channels', [])
        if 'ports' in group:
            return group.get('ports', [])
    if isinstance(group, list):
        return group
    return []


def _port_group_entries(group: Any) -> List[Dict[str, Any]]:
    if isinstance(group, dict):
        if 'channels' in group:
            return group.get('channels', [])
        if 'ports' in group:
            return group.get('ports', [])
    if isinstance(group, list):
        return group
    return []


def _port_group_width(group: Any) -> int:
    if isinstance(group, dict):
        for key in ('channel_width', 'width'):
            value = group.get(key)
            if value is not None:
                return int(value)
    widths = [int(entry.get('width', 0)) for entry in _port_group_entries(group) if entry.get('width') is not None]
    return max(widths) if widths else 0


def _summarize_output_widths(port_map_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    outputs = []
    if port_map_data:
        outputs = port_map_data.get('port_groups', {}).get('outputs', []) or []

    by_group: Dict[str, Dict[str, Any]] = {}
    by_width: Dict[str, int] = {}
    unique_widths = set()

    for port in outputs:
        width = int(port.get('width', 0))
        group_name = str(port.get('group') or 'ungrouped')
        group_info = by_group.setdefault(group_name, {'count': 0, 'widths': []})
        group_info['count'] += 1
        group_info['widths'].append(width)
        by_width[str(width)] = by_width.get(str(width), 0) + 1
        unique_widths.add(width)

    for group_info in by_group.values():
        group_info['widths'] = sorted(set(group_info['widths']))

    return {
        'unique_widths': sorted(unique_widths),
        'by_width': by_width,
        'by_group': by_group,
    }


def extract_design_parameters(
    cfg: DesignConfig,
    algo_top_v_path: Optional[Path] = None,
    hls_metrics_file: Optional[Path] = None,
    port_map_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Extract all testbench-relevant parameters from design configuration.

    Args:
        cfg: Loaded design configuration
        algo_top_v_path: Optional path to generated algo_top.v for port extraction
        hls_metrics_file: Optional path to HLS metrics JSON file

    Returns:
        Dictionary with complete design parameters
    """
    
    # ===========================================================================
    # 1. CLOCK AND TIMING
    # ===========================================================================
    clock_period_ns = float(cfg.clock_period)  # e.g., 2.77 ns
    algo_freq_mhz = round(1000.0 / clock_period_ns, 2)  # e.g., 360.8 MHz
    
    # LHC frequency is always 40 MHz (25 ns period)
    lhc_freq_mhz = 40.0
    lhc_period_ns = 25.0
    
    # Calculate batches per event (LHC BX period in algo clocks)
    # batches_per_event = ceil(LHC_period / algo_period)
    # For 25ns / 2.77ns ≈ 9.03 → 9 batches
    batches_per_event = int(round(lhc_period_ns / clock_period_ns))
    
    bx_counter_modulo = batches_per_event

    # ===========================================================================
    # 2. INTERFACE COUNTS (from modules with external_in_ports)
    # ===========================================================================
    port_map_groups = port_map_data.get('port_groups', {}) if port_map_data else {}

    interface_groups: Dict[str, Dict[str, Any]] = {}
    for group_name, group in port_map_groups.items():
        if group_name in {'clock_reset', 'bx0', 'outputs', 'unclassified'}:
            continue

        entries = _port_group_entries(group)
        group_summary = {
            'count': int(group.get('count', len(entries))) if isinstance(group, dict) else len(entries),
            'width': _port_group_width(group),
        }

        if isinstance(group, dict):
            if 'stimulus_prefix' in group:
                group_summary['stimulus_prefix'] = group.get('stimulus_prefix')
            if 'stimulus_groups' in group:
                group_summary['stimulus_groups'] = group.get('stimulus_groups', [])
            if 'groups' in group:
                group_summary['groups'] = group.get('groups', [])

        interface_groups[group_name] = group_summary

    num_total_input_channels = sum(
        g.get('count', 0) for g in interface_groups.values() if isinstance(g, dict)
    )

    # ===========================================================================
    # 3. DATA WIDTHS (derived from generated port metadata)
    # ===========================================================================
    # Build a generic per-group width dict from port_map (keyed by consumer group names)
    group_widths: dict = {}
    if port_map_groups:
        for grp_name, grp_data in port_map_groups.items():
            if isinstance(grp_data, dict):
                group_widths[grp_name] = _port_group_width(grp_data)
    output_widths = _summarize_output_widths(port_map_data)
    
    # ===========================================================================
    # 4. CONTROL SIGNAL CONFIGURATION
    # ===========================================================================
    control_signals = {
        'new_event': {
            'description': 'Pulse at start of new LHC bunch crossing',
            'period_ns': lhc_period_ns,
            'period_cycles': batches_per_event
        },
        'bx_count': {
            'description': 'Bunch crossing counter output',
            'width': 4,  # 4-bit counter (0-15)
            'modulo': bx_counter_modulo
        }
    }
    
    # ===========================================================================
    # 5. MODULE INFORMATION
    # ===========================================================================
    modules_summary = []
    for idx, module in enumerate(cfg.modules):
        mod_info = {
            'name': module.name,
            'instances': module.instances,
            'kind': module.kind,
            'top': module.top,
            'pipeline_index': idx  # Order in pipeline
        }

        # Add external ports if specified
        if module.external_in_ports:
            mod_info['external_in_ports'] = module.external_in_ports
        if module.external_out_ports:
            mod_info['external_out_ports'] = module.external_out_ports

        # Add debug flag if enabled
        if module.debug:
            mod_info['debug'] = True

        modules_summary.append(mod_info)

    # ===========================================================================
    # Identify last module in pipeline (final algorithm output)
    # ===========================================================================
    # The last module is the last HLS module with external output ports
    # Search backwards through the module list
    for i in range(len(modules_summary) - 1, -1, -1):
        mod = modules_summary[i]
        # Check if it's an HLS module with external outputs
        if mod['kind'] == 'hls' and 'external_out_ports' in mod:
            mod['is_last_module'] = True
            print(f"  ℹ️  Identified last module: {mod['name']} (pipeline output)")
            break
    
    # ===========================================================================
    # 6. PIPELINE INFORMATION (from HLS metrics)
    # ===========================================================================
    pipeline_info = {
        'description': 'Pipeline latency information (from HLS synthesis reports)',
        'total_latency_cycles': None,
        'modules': {}
    }

    # Load and process HLS metrics if provided
    if hls_metrics_file and Path(hls_metrics_file).exists():
        try:
            with open(hls_metrics_file, 'r') as f:
                hls_metrics = json.load(f)

            # Calculate cumulative latency through pipeline
            cumulative_latency = 0
            total_latency = 0

            # Process modules in the order they appear in the design
            # This gives us the correct pipeline order
            for module in cfg.modules:
                module_name = module.name

                # Find corresponding HLS metrics
                # HLS module names might not exactly match design module names
                # Try exact match first, then try common variations
                hls_module_name = None
                if module_name in hls_metrics.get('modules', {}):
                    hls_module_name = module_name
                else:
                    # Try common name variations
                    # Example: a design alias might differ from the exported HLS module name
                    for hls_name in hls_metrics.get('modules', {}).keys():
                        if module.top.lower() in hls_name.lower() or hls_name.lower() in module.top.lower():
                            hls_module_name = hls_name
                            break

                if hls_module_name and hls_module_name in hls_metrics['modules']:
                    metrics = hls_metrics['modules'][hls_module_name]

                    if metrics.get('status') == 'success':
                        latency_data = metrics.get('latency', {})
                        timing_data = metrics.get('timing', {})

                        module_latency = {
                            'latency_min': latency_data.get('best_case_latency', 0),
                            'latency_average': latency_data.get('average_case_latency', 0),
                            'latency_max': latency_data.get('worst_case_latency', 0),
                            'cumulative_latency': cumulative_latency,
                            'pipeline_type': latency_data.get('pipeline_type', 'unknown'),
                            'initiation_interval': latency_data.get('pipeline_ii', 1),
                            'pipeline_depth': latency_data.get('pipeline_depth', 0),
                            'timing_met': timing_data.get('timing_met', None),
                            'hls_module_name': hls_module_name  # Store the HLS module name for reference
                        }

                        pipeline_info['modules'][module_name] = module_latency

                        # Use worst-case latency for cumulative calculation
                        module_latency_cycles = latency_data.get('worst_case_latency', 0)
                        cumulative_latency += module_latency_cycles
                        total_latency = cumulative_latency

            # Set total latency
            pipeline_info['total_latency_cycles'] = total_latency

            print(f"  ✓ Loaded HLS metrics from: {hls_metrics_file}")
            print(f"    - Total pipeline latency: {total_latency} cycles")
            print(f"    - Processed {len(pipeline_info['modules'])} modules with HLS data")

        except Exception as e:
            print(f"  ⚠️  Warning: Failed to load HLS metrics: {e}")
            print(f"     Pipeline information will be incomplete")
    else:
        if hls_metrics_file:
            print(f"  ⚠️  Warning: HLS metrics file not found: {hls_metrics_file}")
        print(f"     Pipeline latency information will be empty")
    
    # ===========================================================================
    # 7. PORT MAPPING (from generated algo_top.v)
    # ===========================================================================
    port_map = {'inputs': [], 'outputs': []}
    if algo_top_v_path and algo_top_v_path.exists():
        port_map = parse_algo_top_ports(algo_top_v_path)

    # ===========================================================================
    # Associate debug ports with modules
    # ===========================================================================
    debug_port_counts = {}
    for port in port_map['outputs']:
        if port['category'] == 'debug' and port['module']:
            module_name = port['module']
            if module_name not in debug_port_counts:
                debug_port_counts[module_name] = []
            debug_port_counts[module_name].append(port['name'])

    # Add debug_ports list to each module that has debug ports
    for mod in modules_summary:
        if mod['name'] in debug_port_counts:
            mod['debug_ports'] = debug_port_counts[mod['name']]
            if not mod.get('debug'):
                # If module has debug ports but debug flag wasn't set, add it
                mod['debug'] = True

    if debug_port_counts:
        print(f"  ℹ️  Found debug ports for {len(debug_port_counts)} modules:")
        for module_name, ports in debug_port_counts.items():
            print(f"      - {module_name}: {len(ports)} debug ports")

    # ===========================================================================
    # 8. BUILD THE COMPLETE PARAMETERS DICTIONARY
    # ===========================================================================
    params = {
        # Metadata
        'schema_version': '1.0',
        'generator': 'topgen/design_parameters.py',
        'timestamp': datetime.datetime.now().isoformat(),
        'design_file': str(Path(cfg._source_file).resolve()) if hasattr(cfg, '_source_file') else 'unknown',
        
        # Clock and Timing
        'timing': {
            'clock_period_ns': clock_period_ns,
            'algo_freq_mhz': algo_freq_mhz,
            'lhc_freq_mhz': lhc_freq_mhz,
            'lhc_period_ns': lhc_period_ns,
            'batches_per_event': batches_per_event,
            'bx_counter_modulo': bx_counter_modulo
        },
        
        # Interface Counts
        'interfaces': {
            'total_input_channels': num_total_input_channels,
            'input_groups': interface_groups,
            'groups': interface_groups,
        },
        
        # Data Widths (keyed by consumer-defined group names + outputs)
        'data_widths': {
            **group_widths,
            'outputs': output_widths,
        },
        
        # Control Signals
        'control_signals': control_signals,
        
        # Modules Summary
        'modules': modules_summary,
        
        # Pipeline (placeholder for HLS metrics integration)
        'pipeline': pipeline_info,
        
        # Port Mapping (complete port list with module traceability)
        'port_map': port_map,
        
        # FPGA Configuration
        'fpga': {
            'part': cfg.part if hasattr(cfg, 'part') else 'unknown'
        }
    }
    
    return params


def write_design_parameters(
    cfg: DesignConfig,
    output_path: Path,
    algo_top_v_path: Optional[Path] = None,
    hls_metrics_file: Optional[Path] = None,
    port_map_data: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Generate design_parameters.json file.

    Args:
        cfg: Design configuration
        output_path: Path to write design_parameters.json
        algo_top_v_path: Optional path to algo_top.v for port extraction
        hls_metrics_file: Optional path to HLS metrics JSON file
    """
    params = extract_design_parameters(cfg, algo_top_v_path, hls_metrics_file, port_map_data)
    
    # Write JSON with nice formatting
    with open(output_path, 'w') as f:
        json.dump(params, f, indent=2, sort_keys=False)
    
    # Print summary
    num_inputs = len(params['port_map']['inputs']) if 'port_map' in params else 0
    num_outputs = len(params['port_map']['outputs']) if 'port_map' in params else 0
    
    print(f"  ✓ Design parameters: {output_path}")
    print(f"    - {params['timing']['algo_freq_mhz']} MHz algorithm clock")
    print(f"    - {params['timing']['batches_per_event']} batches per LHC BX")
    total_inputs = params['interfaces']['total_input_channels']
    group_names = ", ".join(params['interfaces'].get('groups', {}).keys())
    print(f"    - {total_inputs} total input channels across groups: {group_names or '(none)'}")
    if num_inputs > 0 or num_outputs > 0:
        print(f"    - {num_inputs} input ports, {num_outputs} output ports (with module mapping)")



def get_design_parameters(params_file: Path) -> Dict[str, Any]:
    """
    Load design parameters from JSON file.
    
    Args:
        params_file: Path to design_parameters.json
        
    Returns:
        Dictionary with design parameters
        
    Raises:
        FileNotFoundError: If parameters file doesn't exist
        json.JSONDecodeError: If file is not valid JSON
    """
    if not params_file.exists():
        raise FileNotFoundError(
            f"Design parameters file not found: {params_file}\n"
            f"Please generate it with: topgen gen-top <design.yml> --mode verilog"
        )
    
    with open(params_file, 'r') as f:
        params = json.load(f)
    
    # Validate schema version
    if params.get('schema_version') != '1.0':
        print(f"⚠️  Warning: Unexpected schema version: {params.get('schema_version')}")
    
    return params


def print_design_parameters_summary(params: Dict[str, Any]) -> None:
    """Print human-readable summary of design parameters."""
    print("\n" + "="*70)
    print("DESIGN PARAMETERS SUMMARY")
    print("="*70)
    
    print(f"\n📋 Design File: {params['design_file']}")
    print(f"   FPGA Part:   {params['fpga']['part']}")
    
    print(f"\n⏱️  Timing:")
    t = params['timing']
    print(f"   Algorithm Clock: {t['algo_freq_mhz']} MHz ({t['clock_period_ns']} ns)")
    print(f"   LHC Clock:       {t['lhc_freq_mhz']} MHz ({t['lhc_period_ns']} ns)")
    print(f"   Batches/Event:   {t['batches_per_event']}")
    print(f"   BX Modulo:       {t['bx_counter_modulo']}")
    
    print(f"\n🔌 Interfaces:")
    i = params['interfaces']
    print(f"   Total Inputs:    {i['total_input_channels']}")
    for grp_name, grp_info in i.get('groups', {}).items():
        if isinstance(grp_info, dict) and 'count' in grp_info:
            print(f"   {grp_name + ':':20s} {grp_info['count']}")
    
    print(f"\n📦 Data Widths:")
    w = params['data_widths']
    for key, val in w.items():
        if key == 'outputs':
            print(f"   Output Widths:   {val.get('unique_widths', '')}")
        elif isinstance(val, (int, float)):
            print(f"   {key + ':':20s} {val} bits")
    
    print(f"\n📊 Modules ({len(params['modules'])}):")
    for mod in params['modules']:
        print(f"   • {mod['name']:20s} × {mod['instances']:2d}  ({mod['kind']})")
    
    print("="*70 + "\n")
