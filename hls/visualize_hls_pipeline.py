#!/usr/bin/env python3
"""
Visualize HLS pipeline metrics from design.yml and hls_metrics.json

This script generates beautiful visualizations including:
1. Pipeline latency timeline showing module connections and overlaps
2. Latency bar chart for all modules
3. Resource utilization charts (BRAM, DSP, FF, LUT)

Usage:
    ./visualize_hls_pipeline.py [--design design.yml] [--metrics hls_metrics.json] [--output-dir plots]
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import yaml

# Check for matplotlib
try:
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.patches import Rectangle, FancyBboxPatch
    import numpy as np
except ImportError:
    print("Error: matplotlib is required. Install with: pip install matplotlib")
    sys.exit(1)


# Generate distinct colors using HSV color space
def generate_distinct_colors(n):
    """Generate n visually distinct colors."""
    import colorsys
    colors = []
    for i in range(n):
        hue = i / n
        # Use high saturation and value for vibrant colors
        rgb = colorsys.hsv_to_rgb(hue, 0.8, 0.9)
        colors.append('#%02x%02x%02x' % (int(rgb[0]*255), int(rgb[1]*255), int(rgb[2]*255)))
    return colors


class HLSPipelineVisualizer:
    """Visualize HLS pipeline from design and metrics."""

    def __init__(self, design_file: Path, metrics_file: Path):
        self.design_file = design_file
        self.metrics_file = metrics_file
        self.design = None
        self.metrics = None
        self.name_mapping = {}  # design name -> metrics key
        self.hls_modules = []   # Only HLS modules from design
        self.module_colors = {}  # Unique color per module

        self.load_data()
        self.build_name_mapping()
        self.assign_colors()

    def load_data(self):
        """Load design.yml and metrics JSON."""
        # Load design
        with open(self.design_file, 'r') as f:
            self.design = yaml.safe_load(f)

        # Load metrics
        with open(self.metrics_file, 'r') as f:
            self.metrics = json.load(f)

    def build_name_mapping(self):
        """Build mapping between design module names and metrics keys."""
        # Extract HLS modules from design
        for module in self.design.get('modules', []):
            if module.get('kind') == 'hls':
                design_name = module['name']
                top_func = module['top']

                # Try to find matching module in metrics by top function name
                best_match = None
                best_score = 0

                for metrics_key, metrics_data in self.metrics.get('modules', {}).items():
                    if metrics_data.get('status') != 'success':
                        continue

                    module_info = metrics_data.get('module_info', {})
                    metrics_top = module_info.get('top_module', '')

                    # Direct match
                    if metrics_top == top_func:
                        best_match = metrics_key
                        break

                    # Partial match scoring
                    score = 0
                    if top_func.lower() in metrics_key.lower():
                        score += 2
                    if metrics_key.lower() in top_func.lower():
                        score += 2
                    if top_func.lower().replace('_', '') in metrics_key.lower().replace('_', ''):
                        score += 1

                    if score > best_score:
                        best_score = score
                        best_match = metrics_key

                if best_match:
                    self.name_mapping[design_name] = best_match
                    self.hls_modules.append({
                        'design_name': design_name,
                        'metrics_key': best_match,
                        'top_func': top_func,
                        'module': module
                    })

        print(f"Mapped {len(self.name_mapping)} HLS modules:")
        for design_name, metrics_key in self.name_mapping.items():
            print(f"  {design_name:15} -> {metrics_key}")

    def assign_colors(self):
        """Assign unique colors to each module."""
        module_names = sorted([m['design_name'] for m in self.hls_modules])
        colors = generate_distinct_colors(len(module_names))
        self.module_colors = dict(zip(module_names, colors))

    def get_module_color(self, design_name: str) -> str:
        """Get the unique color for a module."""
        return self.module_colors.get(design_name, '#95a5a6')

    def get_module_type(self, design_name: str) -> str:
        """Determine module type for coloring."""
        name_lower = design_name.lower()

        if 'interface' in name_lower or name_lower in ['dt', 'csc']:
            return 'interface'
        elif 'nn' in name_lower or 'neural' in name_lower or 'regression' in name_lower:
            return 'neural_net'
        elif 'mem' in name_lower or 'buffer' in name_lower:
            return 'memory'
        elif 'arb' in name_lower:
            return 'arbiter'
        elif 'sel' in name_lower or 'best' in name_lower:
            return 'selector'
        elif 'out' in name_lower or 'csp' in name_lower:
            return 'output'
        else:
            return 'processing'

    def analyze_pipeline_flow(self) -> List[List[str]]:
        """Analyze connections to determine pipeline stages."""
        connections = self.design.get('connections', [])

        # Build adjacency graph (only HLS modules)
        hls_names = set(self.name_mapping.keys())
        graph = {name: [] for name in hls_names}
        
        # Store register stages info: (from_mod, to_mod) -> register_stages
        self.register_stages_map = {}

        for conn in connections:
            from_mod = conn.get('from')
            to_mod = conn.get('to')
            register_stages = conn.get('register_stages', 0)

            if from_mod in hls_names and to_mod in hls_names:
                if to_mod not in graph[from_mod]:
                    graph[from_mod].append(to_mod)
                
                # Store register stages info
                if register_stages > 0:
                    self.register_stages_map[(from_mod, to_mod)] = register_stages

        # Find pipeline stages using topological ordering
        visited = set()
        stages = []

        def get_stage(node, current_stage=0):
            if node in visited:
                return current_stage
            visited.add(node)

            if current_stage >= len(stages):
                stages.append([])
            stages[current_stage].append(node)

            max_stage = current_stage
            for next_node in graph.get(node, []):
                next_stage = get_stage(next_node, current_stage + 1)
                max_stage = max(max_stage, next_stage)

            return max_stage

        # Start from modules with no incoming edges (inputs)
        incoming = {name: 0 for name in hls_names}
        for from_mod in graph:
            for to_mod in graph[from_mod]:
                incoming[to_mod] += 1

        roots = [name for name, count in incoming.items() if count == 0]
        for root in roots:
            get_stage(root)

        # Add any remaining nodes
        for name in hls_names:
            if name not in visited:
                stages.append([name])

        return stages

    def plot_pipeline_timeline(self, output_dir: Path):
        """Create a beautiful pipeline timeline visualization."""
        stages = self.analyze_pipeline_flow()

        if not stages:
            print("No pipeline stages found")
            return

        # Set up figure
        fig, ax = plt.subplots(figsize=(16, 10))

        # Calculate positions
        y_pos = 0
        y_spacing = 1.2
        cumulative_latency = 0
        max_latency = 0

        module_positions = {}

        for stage_idx, stage_modules in enumerate(stages):
            stage_max_latency = 0

            for module_idx, design_name in enumerate(sorted(stage_modules)):
                metrics_key = self.name_mapping.get(design_name)
                if not metrics_key:
                    continue

                module_data = self.metrics['modules'][metrics_key]
                if module_data.get('status') != 'success':
                    continue

                latency = module_data['latency']['worst_case_latency']
                ii = module_data['latency']['pipeline_ii']
                stage_max_latency = max(stage_max_latency, latency)

                # Get unique color for this module
                color = self.get_module_color(design_name)

                # Draw latency bar
                bar = FancyBboxPatch(
                    (cumulative_latency, y_pos),
                    latency,
                    0.8,
                    boxstyle="round,pad=0.05",
                    facecolor=color,
                    edgecolor='black',
                    linewidth=1.5,
                    alpha=0.8
                )
                ax.add_patch(bar)

                # Add module label
                label_x = cumulative_latency + latency / 2
                label_text = f"{design_name}\n{latency} cycles"
                if ii > 1:
                    label_text += f"\nII={ii}"

                ax.text(label_x, y_pos + 0.4, label_text,
                       ha='center', va='center',
                       fontsize=8, fontweight='bold',
                       color='white')

                module_positions[design_name] = (cumulative_latency, y_pos, latency)

                y_pos += y_spacing

            # Move to next stage
            cumulative_latency += stage_max_latency
            max_latency = max(max_latency, cumulative_latency)
            y_pos += 0.5  # Extra spacing between stages

        # Draw register stages between modules
        if hasattr(self, 'register_stages_map'):
            for (from_mod, to_mod), reg_stages in self.register_stages_map.items():
                if from_mod in module_positions and to_mod in module_positions:
                    from_x, from_y, from_lat = module_positions[from_mod]
                    to_x, to_y, to_lat = module_positions[to_mod]
                    
                    # Draw register stage indicator
                    reg_x = from_x + from_lat
                    reg_y = (from_y + to_y) / 2
                    
                    # Draw a small box for register stages
                    reg_box = FancyBboxPatch(
                        (reg_x, reg_y - 0.3),
                        reg_stages * 2,  # Width proportional to stages
                        0.6,
                        boxstyle="round,pad=0.03",
                        facecolor='#FFD700',  # Gold color
                        edgecolor='#FF6347',  # Tomato red border
                        linewidth=2,
                        alpha=0.9,
                        linestyle='--'
                    )
                    ax.add_patch(reg_box)
                    
                    # Add label
                    ax.text(reg_x + reg_stages, reg_y, f"REG\n{reg_stages}",
                           ha='center', va='center',
                           fontsize=7, fontweight='bold',
                           color='#8B0000')  # Dark red
                    
                    # Draw connection arrows
                    ax.arrow(from_x + from_lat, from_y + 0.4, 
                            reg_x - (from_x + from_lat) - 1, 
                            reg_y - (from_y + 0.4),
                            head_width=0.2, head_length=0.5, 
                            fc='gray', ec='gray', alpha=0.5, linestyle='--')
                    
                    ax.arrow(reg_x + reg_stages * 2, reg_y,
                            to_x - (reg_x + reg_stages * 2) - 1,
                            to_y + 0.4 - reg_y,
                            head_width=0.2, head_length=0.5,
                            fc='gray', ec='gray', alpha=0.5, linestyle='--')

        # Set axis properties
        ax.set_xlim(-5, max_latency + 5)
        ax.set_ylim(-1, y_pos + 1)
        ax.set_xlabel('Latency (clock cycles)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Pipeline Modules', fontsize=12, fontweight='bold')
        ax.set_title('HLS Pipeline Latency Timeline (with Register Stages)', fontsize=16, fontweight='bold', pad=20)

        # Remove y-ticks
        ax.set_yticks([])

        # Add grid
        ax.grid(True, axis='x', alpha=0.3, linestyle='--')

        # Add legend for register stages if any exist
        if hasattr(self, 'register_stages_map') and self.register_stages_map:
            legend_elements = [
                mpatches.Patch(facecolor='#FFD700', edgecolor='#FF6347', 
                              label='Register Stages', linestyle='--', linewidth=2)
            ]
            ax.legend(handles=legend_elements, loc='upper right', fontsize=10)

        # Add total latency annotation
        ax.text(max_latency / 2, -0.5, f'Total Pipeline Latency: {max_latency} cycles',
               ha='center', va='top', fontsize=12, fontweight='bold',
               bbox=dict(boxstyle='round,pad=0.5', facecolor='yellow', alpha=0.3))

        plt.tight_layout()
        output_file = output_dir / 'pipeline_timeline.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()

        print(f"✅ Pipeline timeline saved to: {output_file}")

    def plot_latency_comparison(self, output_dir: Path):
        """Create bar chart comparing module latencies."""
        fig, ax = plt.subplots(figsize=(14, 8))

        modules = []
        latencies = []
        colors = []
        ii_values = []

        for hls_mod in sorted(self.hls_modules,
                            key=lambda x: self.metrics['modules'][x['metrics_key']]['latency']['worst_case_latency'],
                            reverse=True):
            design_name = hls_mod['design_name']
            metrics_key = hls_mod['metrics_key']
            module_data = self.metrics['modules'][metrics_key]

            if module_data.get('status') != 'success':
                continue

            latency = module_data['latency']['worst_case_latency']
            ii = module_data['latency']['pipeline_ii']

            modules.append(design_name)
            latencies.append(latency)
            ii_values.append(ii)
            colors.append(self.get_module_color(design_name))

        # Create bars
        bars = ax.barh(modules, latencies, color=colors, edgecolor='black', linewidth=1.5)

        # Add value labels
        for i, (bar, lat, ii) in enumerate(zip(bars, latencies, ii_values)):
            width = bar.get_width()
            label = f'{lat} cycles'
            if ii > 1:
                label += f' (II={ii})'
            ax.text(width + max(latencies) * 0.01, bar.get_y() + bar.get_height()/2,
                   label, ha='left', va='center', fontsize=9, fontweight='bold')

        ax.set_xlabel('Latency (clock cycles)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Module', fontsize=12, fontweight='bold')
        ax.set_title('HLS Module Latency Comparison', fontsize=16, fontweight='bold', pad=20)
        ax.grid(True, axis='x', alpha=0.3, linestyle='--')

        plt.tight_layout()
        output_file = output_dir / 'latency_comparison.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()

        print(f"✅ Latency comparison saved to: {output_file}")

    def plot_resource_usage(self, output_dir: Path):
        """Create resource utilization charts."""
        resources = ['BRAM_18K', 'DSP', 'FF', 'LUT']

        fig, axes = plt.subplots(2, 2, figsize=(16, 12))
        axes = axes.flatten()

        for idx, resource in enumerate(resources):
            ax = axes[idx]

            modules = []
            values = []
            colors = []
            percentages = []

            # Get total available resources from first module
            total_available = None
            for mod in self.hls_modules:
                metrics_key = mod['metrics_key']
                module_data = self.metrics['modules'][metrics_key]
                if module_data.get('status') == 'success':
                    total_available = module_data['resources']['available'][resource]
                    break

            for hls_mod in sorted(self.hls_modules,
                                key=lambda x: self.metrics['modules'][x['metrics_key']]['resources']['used'][resource],
                                reverse=True):
                design_name = hls_mod['design_name']
                metrics_key = hls_mod['metrics_key']
                module_data = self.metrics['modules'][metrics_key]

                if module_data.get('status') != 'success':
                    continue

                value = module_data['resources']['used'][resource]
                if value == 0 and resource in ['BRAM_18K', 'DSP']:
                    continue  # Skip modules with 0 for these resources

                modules.append(design_name)
                values.append(value)
                colors.append(self.get_module_color(design_name))

                # Calculate percentage of total board resources
                if total_available and total_available > 0:
                    pct = 100.0 * value / total_available
                    percentages.append(pct)
                else:
                    percentages.append(0.0)

            # Create bars
            if values:
                bars = ax.barh(modules, values, color=colors, edgecolor='black', linewidth=1)

                # Add value labels with percentage
                for bar, val, pct in zip(bars, values, percentages):
                    width = bar.get_width()
                    label = f'{val:,} ({pct:.1f}%)'
                    ax.text(width + max(values) * 0.01, bar.get_y() + bar.get_height()/2,
                           label, ha='left', va='center', fontsize=8, fontweight='bold')

                ax.set_xlabel(f'{resource} Count (% of total)', fontsize=11, fontweight='bold')
                ax.set_ylabel('Module', fontsize=11, fontweight='bold')
                title = f'{resource} Utilization'
                if total_available:
                    title += f'\nTotal Available: {total_available:,}'
                ax.set_title(title, fontsize=13, fontweight='bold')
                ax.grid(True, axis='x', alpha=0.3, linestyle='--')
            else:
                ax.text(0.5, 0.5, 'No data', ha='center', va='center',
                       transform=ax.transAxes, fontsize=12)
                ax.set_title(f'{resource} Utilization', fontsize=13, fontweight='bold')

        plt.suptitle('HLS Resource Utilization by Module', fontsize=16, fontweight='bold', y=0.995)
        plt.tight_layout()
        output_file = output_dir / 'resource_usage.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()

        print(f"✅ Resource usage saved to: {output_file}")

    def plot_frequency_comparison(self, output_dir: Path):
        """Create frequency comparison chart."""
        fig, ax = plt.subplots(figsize=(14, 8))

        modules = []
        frequencies = []
        colors = []
        timing_met = []

        target_freq = None

        for hls_mod in sorted(self.hls_modules,
                            key=lambda x: self.metrics['modules'][x['metrics_key']]['timing']['estimated_frequency_mhz'],
                            reverse=False):
            design_name = hls_mod['design_name']
            metrics_key = hls_mod['metrics_key']
            module_data = self.metrics['modules'][metrics_key]

            if module_data.get('status') != 'success':
                continue

            freq = module_data['timing']['estimated_frequency_mhz']
            met = module_data['timing']['timing_met']

            if target_freq is None:
                target_freq = module_data['timing']['target_frequency_mhz']

            modules.append(design_name)
            frequencies.append(freq)
            timing_met.append(met)

            # Color based on timing
            if not met:
                colors.append('#e74c3c')  # Red for timing violations
            else:
                colors.append(self.get_module_color(design_name))

        # Create bars
        bars = ax.barh(modules, frequencies, color=colors, edgecolor='black', linewidth=1.5)

        # Add target frequency line
        if target_freq:
            ax.axvline(x=target_freq, color='red', linestyle='--', linewidth=2,
                      label=f'Target: {target_freq:.1f} MHz')

        # Add value labels
        for bar, freq, met in zip(bars, frequencies, timing_met):
            width = bar.get_width()
            label = f'{freq:.1f} MHz'
            if not met:
                label += ' ⚠'
            ax.text(width + 10, bar.get_y() + bar.get_height()/2,
                   label, ha='left', va='center', fontsize=9, fontweight='bold')

        ax.set_xlabel('Frequency (MHz)', fontsize=12, fontweight='bold')
        ax.set_ylabel('Module', fontsize=12, fontweight='bold')
        ax.set_title('HLS Module Estimated Frequency', fontsize=16, fontweight='bold', pad=20)
        ax.grid(True, axis='x', alpha=0.3, linestyle='--')
        ax.legend(loc='lower right', fontsize=10)

        plt.tight_layout()
        output_file = output_dir / 'frequency_comparison.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()

        print(f"✅ Frequency comparison saved to: {output_file}")

    def plot_latency_gaps(self, output_dir: Path):
        """Create a visualization showing latency gaps and synchronization issues."""
        # Build connection maps
        connections = self.design.get('connections', [])
        hls_names = set(self.name_mapping.keys())

        # Forward map: from_module -> [to_modules]
        forward_map = {}
        # Reverse map: to_module -> [from_modules]
        reverse_map = {}

        for conn in connections:
            from_mod = conn.get('from')
            to_mod = conn.get('to')
            if from_mod in hls_names and to_mod in hls_names:
                if from_mod not in forward_map:
                    forward_map[from_mod] = []
                forward_map[from_mod].append(to_mod)

                if to_mod not in reverse_map:
                    reverse_map[to_mod] = []
                reverse_map[to_mod].append(from_mod)

        # Get module latencies
        module_latencies = {}
        for hls_mod in self.hls_modules:
            design_name = hls_mod['design_name']
            metrics_key = hls_mod['metrics_key']
            module_data = self.metrics['modules'][metrics_key]
            if module_data.get('status') == 'success':
                module_latencies[design_name] = module_data['latency']['worst_case_latency']

        # Set up figure
        fig, ax = plt.subplots(figsize=(18, 10))

        # Analyze stages and calculate positions
        stages = self.analyze_pipeline_flow()

        y_spacing = 1.2
        y_offset = 0
        max_x = 0

        stage_positions = {}  # module_name -> (x_start, y_pos, latency)
        gap_info = []  # List of (x_start, x_end, y_pos, gap_cycles, affected_module)

        for stage_idx, stage_modules in enumerate(stages):
            # Calculate when this stage can start
            stage_start_time = 0

            # For each module in this stage, check all its inputs
            for design_name in stage_modules:
                if design_name in reverse_map:
                    # This module has inputs - find when the slowest input finishes
                    max_input_finish = 0
                    input_finish_times = {}

                    for input_mod in reverse_map[design_name]:
                        if input_mod in stage_positions:
                            x_start, _, input_lat = stage_positions[input_mod]
                            finish_time = x_start + input_lat
                            
                            # Add register stages latency if present
                            if hasattr(self, 'register_stages_map'):
                                reg_stages = self.register_stages_map.get((input_mod, design_name), 0)
                                if reg_stages > 0:
                                    finish_time += reg_stages
                            
                            input_finish_times[input_mod] = finish_time
                            max_input_finish = max(max_input_finish, finish_time)

                    # Check for synchronization gaps
                    if len(input_finish_times) > 1:
                        # Multiple inputs - there might be gaps
                        for input_mod, finish_time in input_finish_times.items():
                            gap = max_input_finish - finish_time
                            if gap > 0:
                                # This input finishes early and must wait
                                inp_x, inp_y, inp_lat = stage_positions[input_mod]
                                gap_info.append({
                                    'x_start': inp_x + inp_lat,
                                    'x_end': inp_x + inp_lat + gap,
                                    'y_pos': inp_y,
                                    'gap_cycles': gap,
                                    'waiting_for': design_name,
                                    'module': input_mod
                                })

                    stage_start_time = max(stage_start_time, max_input_finish)

            # Position modules in this stage
            for design_name in sorted(stage_modules):
                if design_name not in module_latencies:
                    continue

                latency = module_latencies[design_name]
                color = self.get_module_color(design_name)

                # Draw module bar
                bar = FancyBboxPatch(
                    (stage_start_time, y_offset),
                    latency,
                    0.9,
                    boxstyle="round,pad=0.05",
                    facecolor=color,
                    edgecolor='black',
                    linewidth=2,
                    alpha=0.9
                )
                ax.add_patch(bar)

                # Label
                label_text = f"{design_name}\n{latency} cycles"
                ax.text(stage_start_time + latency / 2, y_offset + 0.45,
                       label_text,
                       ha='center', va='center',
                       fontsize=9, fontweight='bold',
                       color='white')

                stage_positions[design_name] = (stage_start_time, y_offset, latency)
                max_x = max(max_x, stage_start_time + latency)

                y_offset += y_spacing

            y_offset += 0.5  # Extra spacing between stages

        # Draw register stages between modules
        register_stage_info = []
        if hasattr(self, 'register_stages_map'):
            for (from_mod, to_mod), reg_stages in self.register_stages_map.items():
                if from_mod in stage_positions and to_mod in stage_positions:
                    from_x, from_y, from_lat = stage_positions[from_mod]
                    to_x, to_y, to_lat = stage_positions[to_mod]
                    
                    # Register stages start right after the source module
                    reg_x = from_x + from_lat
                    reg_y = from_y
                    
                    # Draw register stage box
                    reg_box = FancyBboxPatch(
                        (reg_x, reg_y),
                        reg_stages,
                        0.9,
                        boxstyle="round,pad=0.03",
                        facecolor='#FFD700',  # Gold
                        edgecolor='#FF6347',  # Tomato
                        linewidth=2,
                        alpha=0.9,
                        linestyle='--'
                    )
                    ax.add_patch(reg_box)
                    
                    # Label
                    ax.text(reg_x + reg_stages / 2, reg_y + 0.45,
                           f"REG\n{reg_stages}c",
                           ha='center', va='center',
                           fontsize=8, fontweight='bold',
                           color='#8B0000')
                    
                    register_stage_info.append({
                        'from': from_mod,
                        'to': to_mod,
                        'stages': reg_stages
                    })

        # Draw synchronization gaps in RED
        for gap in gap_info:
            # Red bar showing the gap
            gap_bar = Rectangle(
                (gap['x_start'], gap['y_pos']),
                gap['gap_cycles'],
                0.9,
                facecolor='red',
                edgecolor='darkred',
                linewidth=2,
                alpha=0.7,
                hatch='//'
            )
            ax.add_patch(gap_bar)

            # Label the gap
            gap_label = f"GAP: {gap['gap_cycles']}c\nwaiting for\n{gap['waiting_for']}"
            ax.text(gap['x_start'] + gap['gap_cycles'] / 2, gap['y_pos'] + 0.45,
                   gap_label,
                   ha='center', va='center',
                   fontsize=7, fontweight='bold',
                   color='white',
                   bbox=dict(boxstyle='round,pad=0.2', facecolor='darkred', alpha=0.8))

        # Axis settings
        ax.set_xlim(-5, max_x + 5)
        ax.set_ylim(-1, y_offset + 1)
        ax.set_xlabel('Clock Cycles (Latency)', fontsize=13, fontweight='bold')
        ax.set_ylabel('Pipeline Modules', fontsize=13, fontweight='bold')
        ax.set_title('HLS Pipeline Latency Gaps Analysis\n(Red bars = synchronization gaps where modules wait for slower inputs)',
                    fontsize=15, fontweight='bold', pad=20)

        ax.set_yticks([])
        ax.grid(True, axis='x', alpha=0.3, linestyle='--', linewidth=0.8)

        # Add legend
        from matplotlib.patches import Patch
        legend_elements = [
            Patch(facecolor='red', edgecolor='darkred', alpha=0.7, hatch='//',
                  label='Synchronization Gap (wasted cycles)'),
            Patch(facecolor='lightblue', edgecolor='black',
                  label='Module Execution')
        ]
        
        # Add register stages to legend if any exist
        if register_stage_info:
            legend_elements.append(
                Patch(facecolor='#FFD700', edgecolor='#FF6347', 
                      label=f'Register Stages ({len(register_stage_info)} connections)',
                      linestyle='--', linewidth=2)
            )
        
        ax.legend(handles=legend_elements, loc='upper right',
                 fontsize=10, framealpha=0.9)

        # Add summary statistics
        total_gap_cycles = sum(g['gap_cycles'] for g in gap_info)
        if gap_info:
            summary_text = f"Total Gaps: {len(gap_info)} | Total Wasted Cycles: {total_gap_cycles}"
            ax.text(max_x / 2, -0.5, summary_text,
                   ha='center', va='top', fontsize=11, fontweight='bold',
                   bbox=dict(boxstyle='round,pad=0.5', facecolor='yellow', alpha=0.5))

        plt.tight_layout()
        output_file = output_dir / 'latency_gaps.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()

        print(f"✅ Latency gaps visualization saved to: {output_file}")
        if gap_info:
            print(f"   Found {len(gap_info)} synchronization gaps totaling {total_gap_cycles} cycles")

    def plot_block_diagram(self, output_dir: Path):
        """Create a block diagram showing module connections with bit widths and register stages."""
        try:
            import networkx as nx
        except ImportError:
            print("⚠️  Block diagram requires networkx. Install with: pip install networkx")
            print("   Skipping block diagram generation...")
            return

        # Get all connections
        connections = self.design.get('connections', [])
        hls_names = set(self.name_mapping.keys())
        
        # Get ALL module names (HLS + RTL)
        all_module_names = set()
        for module in self.design.get('modules', []):
            all_module_names.add(module.get('name'))
        
        # Build graph for topology analysis - include ALL modules
        G = nx.DiGraph()
        
        # Store connection metadata
        connection_data = []
        
        # Track fanout for better positioning
        module_fanout = {}
        module_fanin = {}
        
        # Track external ports per module
        module_ext_inputs = {}   # module -> list of input port names
        module_ext_outputs = {}  # module -> list of output port names
        
        for module in self.design.get('modules', []):
            mod_name = module.get('name')
            ext_in = module.get('external_in_ports', [])
            ext_out = module.get('external_out_ports', [])
            if ext_in:
                module_ext_inputs[mod_name] = ext_in
            if ext_out:
                module_ext_outputs[mod_name] = ext_out
        
        for conn in connections:
            from_mod = conn.get('from')
            to_mod = conn.get('to')
            
            # Track fanout/fanin
            if from_mod not in module_fanout:
                module_fanout[from_mod] = []
            if to_mod not in module_fanin:
                module_fanin[to_mod] = []
            
            if to_mod not in module_fanout[from_mod]:
                module_fanout[from_mod].append(to_mod)
            if from_mod not in module_fanin[to_mod]:
                module_fanin[to_mod].append(from_mod)
            
            # Only calculate details for connections we'll show
            if from_mod in all_module_names and to_mod in all_module_names:
                # Calculate connection info
                port_map = conn.get('port_map', [])
                port_ranges = conn.get('port_map_ranges', [])
                
                num_connections = len(port_map)
                
                # Handle port_map_ranges with multi-dimensional templates
                for prange in port_ranges:
                    if 'dims' in prange:
                        dims = prange['dims']
                        count = 1
                        for dim in dims:
                            count *= dim
                        num_connections += count
                    else:
                        num_connections += prange.get('count', 0)
                
                total_bits = num_connections * 64 if num_connections > 0 else 64
                register_stages = conn.get('register_stages', 0)
                
                G.add_edge(from_mod, to_mod)
                
                connection_data.append({
                    'from': from_mod,
                    'to': to_mod,
                    'bits': total_bits,
                    'reg_stages': register_stages,
                    'num_conn': num_connections
                })
        
        # Add all modules as nodes
        for mod_name in all_module_names:
            G.add_node(mod_name)
        
        # Compute hierarchical layout
        try:
            generations = list(nx.topological_generations(G))
        except:
            generations = [[node] for node in G.nodes()]
        
        # Calculate positions with room for external port columns
        pos = {}
        column_width = 5.0
        row_height = 2.5
        
        # Reserve column 0 for external inputs, start modules at column 1
        for col_idx, generation in enumerate(generations):
            # Sort by fanout to spread vertically
            sorted_gen = sorted(generation, key=lambda m: len(module_fanout.get(m, [])), reverse=True)
            num_in_col = len(sorted_gen)
            
            for row_idx, mod_name in enumerate(sorted_gen):
                x = (col_idx + 1) * column_width  # +1 to leave room for external inputs
                y = (row_idx - num_in_col / 2) * row_height
                pos[mod_name] = (x, y)
        
        # Adjust vertical positions for modules with multiple inputs to spread arrows
        for mod_name in all_module_names:
            if mod_name in module_fanin and len(module_fanin[mod_name]) > 1:
                # This module has multiple inputs - adjust source positions
                sources = module_fanin[mod_name]
                if len(sources) == 2:
                    # Two inputs - offset them vertically
                    for idx, source in enumerate(sources):
                        if source in pos:
                            x, y = pos[source]
                            offset = (idx - 0.5) * 1.0  # Spread by 1 unit
                            pos[source] = (x, y + offset)
        
        # Position external input ports in column 0
        ext_input_pos = {}
        input_modules = sorted(module_ext_inputs.keys())
        num_inputs = len(input_modules)
        
        for idx, mod_name in enumerate(input_modules):
            if mod_name in pos:
                # Place input port to the left of its module, but in column 0
                _, mod_y = pos[mod_name]
                ext_input_pos[mod_name] = (0, mod_y)
        
        # Position external output ports in final column
        ext_output_pos = {}
        output_modules = sorted(module_ext_outputs.keys())
        max_col_x = max(p[0] for p in pos.values()) if pos else 0
        output_col_x = max_col_x + column_width
        
        # For modules with multiple outputs, spread them vertically
        for idx, mod_name in enumerate(output_modules):
            if mod_name in pos:
                _, mod_y = pos[mod_name]
                ports = module_ext_outputs[mod_name]
                num_ports = len(ports)
                
                if num_ports == 1:
                    # Single output - same vertical position
                    ext_output_pos[mod_name] = [(output_col_x, mod_y, ports[0])]
                else:
                    # Multiple outputs - spread vertically
                    port_positions = []
                    for port_idx, port_name in enumerate(ports):
                        offset = (port_idx - (num_ports - 1) / 2) * 0.6
                        port_positions.append((output_col_x, mod_y + offset, port_name))
                    ext_output_pos[mod_name] = port_positions
        
        # Set up figure
        num_columns = len(generations) + 2  # +2 for external ports
        max_rows = max(len(gen) for gen in generations) if generations else 1
        fig_width = max(20, num_columns * 4)
        fig_height = max(14, max_rows * 2.5)
        
        fig, ax = plt.subplots(figsize=(fig_width, fig_height))
        
        # Draw external input ports
        for mod_name, (x, y) in ext_input_pos.items():
            port_box = FancyBboxPatch(
                (x - 0.5, y - 0.4),
                1.0, 0.8,
                boxstyle="round,pad=0.05",
                facecolor='#3498db',
                edgecolor='black',
                linewidth=2,
                alpha=0.9
            )
            ax.add_patch(port_box)
            
            ports = module_ext_inputs[mod_name]
            label = 'INPUT'
            if len(ports) == 1:
                label = f'IN\n{ports[0]}'
            
            ax.text(x, y, label,
                   ha='center', va='center',
                   fontsize=8, fontweight='bold',
                   color='white')
            
            # Arrow to module
            if mod_name in pos:
                mod_x, mod_y = pos[mod_name]
                ax.arrow(x + 0.5, y, mod_x - x - 2.4, mod_y - y,
                        head_width=0.2, head_length=0.4,
                        fc='#3498db', ec='#3498db', alpha=0.6, linewidth=2)
        
        # Draw external output ports
        for mod_name, port_list in ext_output_pos.items():
            for (x, y, port_name) in port_list:
                port_box = FancyBboxPatch(
                    (x - 0.6, y - 0.35),
                    1.2, 0.7,
                    boxstyle="round,pad=0.05",
                    facecolor='#e74c3c',
                    edgecolor='black',
                    linewidth=2,
                    alpha=0.9
                )
                ax.add_patch(port_box)
                
                # Show port name
                label = f'OUT\n{port_name}'
                ax.text(x, y, label,
                       ha='center', va='center',
                       fontsize=7, fontweight='bold',
                       color='white')
                
                # Arrow from module
                if mod_name in pos:
                    mod_x, mod_y = pos[mod_name]
                    ax.arrow(mod_x + 0.9, mod_y, x - mod_x - 1.5, y - mod_y,
                            head_width=0.15, head_length=0.3,
                            fc='#e74c3c', ec='#e74c3c', alpha=0.6, linewidth=1.5)
        
        # Draw module boxes
        box_width = 1.8
        box_height = 1.0
        
        for node in G.nodes():
            if node not in pos:
                continue
                
            x, y = pos[node]
            
            # Determine if HLS or RTL
            is_hls = node in hls_names
            
            # Get color - use gray for RTL modules
            if is_hls:
                color = self.get_module_color(node)
            else:
                color = '#95a5a6'  # Gray for RTL
            
            # Get latency info (only for HLS)
            latency_text = ""
            if is_hls and node in self.name_mapping:
                metrics_key = self.name_mapping[node]
                module_data = self.metrics['modules'].get(metrics_key, {})
                if module_data.get('status') == 'success':
                    latency = module_data['latency']['worst_case_latency']
                    latency_text = f"\n{latency} cyc"
            
            # Draw module box
            box = FancyBboxPatch(
                (x - box_width/2, y - box_height/2),
                box_width, box_height,
                boxstyle="round,pad=0.08",
                facecolor=color,
                edgecolor='black',
                linewidth=2.5,
                alpha=0.95
            )
            ax.add_patch(box)
            
            # Add module label
            label = f"{node}{latency_text}"
            if not is_hls:
                label = f"{node}\n(RTL)"
            
            ax.text(x, y, label,
                   ha='center', va='center',
                   fontsize=9, fontweight='bold',
                   color='white')
        
        # Draw edges with improved routing to avoid crossing blocks
        for edge_data in connection_data:
            from_mod = edge_data['from']
            to_mod = edge_data['to']
            
            if from_mod not in pos or to_mod not in pos:
                continue
            
            x1, y1 = pos[from_mod]
            x2, y2 = pos[to_mod]
            
            dx = x2 - x1
            dy = y2 - y1
            
            # Start/end points on box edges
            start_x = x1 + box_width/2
            start_y = y1
            end_x = x2 - box_width/2
            end_y = y2
            
            # Line thickness based on signal count
            line_width = 1.5 + min(edge_data['num_conn'] / 200, 3.5)
            
            # Improved routing: use larger curve for vertical offsets to go around blocks
            # If there's a significant vertical distance, route around
            if abs(dy) > 0.5:
                # Large curve to go around intermediate blocks
                curve_rad = 0.6 if abs(dy) > 2 else 0.4
            else:
                # Small curve for same-row or close connections
                curve_rad = 0.15
            
            # Draw arrow with curved path
            arrow_props = dict(
                arrowstyle='-|>',
                connectionstyle=f'arc3,rad={curve_rad}',
                linewidth=line_width,
                color='#34495e',
                alpha=0.7
            )
            
            ax.annotate('', xy=(end_x, end_y), xytext=(start_x, start_y),
                       arrowprops=arrow_props)
            
            # Label position
            mid_x = (start_x + end_x) / 2
            mid_y = (start_y + end_y) / 2
            
            # Format label
            if edge_data['num_conn'] > 100:
                conn_label = f"{edge_data['num_conn']}"
            elif edge_data['num_conn'] > 1:
                conn_label = f"{edge_data['num_conn']}"
            else:
                conn_label = f"{edge_data['bits']}b"
            
            # Position label
            label_offset = 0.25
            ax.text(mid_x, mid_y + label_offset, conn_label,
                   ha='center', va='bottom',
                   fontsize=7, style='italic',
                   color='#2c3e50',
                   bbox=dict(boxstyle='round,pad=0.25', facecolor='white', 
                           edgecolor='#95a5a6', alpha=0.9, linewidth=0.8))
            
            # Register stage indicator
            if edge_data['reg_stages'] > 0:
                reg_label = f"REG {edge_data['reg_stages']}"
                ax.text(mid_x, mid_y - 0.35, reg_label,
                       ha='center', va='top',
                       fontsize=7, fontweight='bold',
                       color='#c0392b',
                       bbox=dict(boxstyle='round,pad=0.2', facecolor='#f39c12',
                               edgecolor='#e74c3c', linewidth=1.5, alpha=0.95))
        
        # Set axis properties
        ax.set_aspect('equal')
        ax.axis('off')
        
        # Set limits with external ports
        if pos:
            all_x = [p[0] for p in pos.values()]
            all_y = [p[1] for p in pos.values()]
            
            # Include external ports in bounds
            if ext_input_pos:
                all_x.extend([p[0] for p in ext_input_pos.values()])
                all_y.extend([p[1] for p in ext_input_pos.values()])
            if ext_output_pos:
                for port_list in ext_output_pos.values():
                    for (x, y, _) in port_list:
                        all_x.append(x)
                        all_y.append(y)
            
            margin = 2
            ax.set_xlim(min(all_x) - margin, max(all_x) + margin)
            ax.set_ylim(min(all_y) - margin, max(all_y) + margin)
        
        # Title
        total_connections = len(connection_data)
        total_modules = len(all_module_names)
        hls_count = len(hls_names)
        rtl_count = total_modules - hls_count
        num_reg_stages = sum(1 for ed in connection_data if ed['reg_stages'] > 0)
        total_signal_count = sum(ed['num_conn'] for ed in connection_data)
        
        title = f'Algorithm Block Diagram\n'
        title += f'{hls_count} HLS + {rtl_count} RTL Modules • {total_connections} Links • {total_signal_count} Signals'
        if num_reg_stages > 0:
            title += f' • {num_reg_stages} Register Stages'
        ax.set_title(title, fontsize=16, fontweight='bold', pad=20)
        
        # Legend
        legend_elements = [
            mpatches.Patch(facecolor='lightblue', edgecolor='black', 
                          label='HLS Module', linewidth=2),
            mpatches.Patch(facecolor='#95a5a6', edgecolor='black',
                          label='RTL Module', linewidth=2),
            mpatches.Patch(facecolor='#3498db', edgecolor='black',
                          label='External Input', linewidth=2),
            mpatches.Patch(facecolor='#e74c3c', edgecolor='black',
                          label='External Output', linewidth=2),
        ]
        if num_reg_stages > 0:
            legend_elements.append(
                mpatches.Patch(facecolor='#f39c12', edgecolor='#e74c3c',
                              label='Pipeline Register', linewidth=1.5)
            )
        
        ax.legend(handles=legend_elements, loc='upper left', fontsize=10, framealpha=0.95)
        
        # Column labels
        ax.text(0, max(all_y) + 1.5 if all_y else 0, 'External\nInputs',
               ha='center', va='bottom',
               fontsize=9, fontweight='bold', style='italic', color='#3498db')
        
        for col_idx, generation in enumerate(generations):
            if generation:
                x = (col_idx + 1) * column_width
                y_top = max(pos[mod][1] for mod in generation if mod in pos) + box_height/2 + 0.8
                ax.text(x, y_top, f'Stage {col_idx}',
                       ha='center', va='bottom',
                       fontsize=9, style='italic', color='#7f8c8d',
                       bbox=dict(boxstyle='round,pad=0.3', facecolor='lightyellow',
                               edgecolor='gray', alpha=0.7))
        
        ax.text(output_col_x, max(all_y) + 1.5 if all_y else 0, 'External\nOutputs',
               ha='center', va='bottom',
               fontsize=9, fontweight='bold', style='italic', color='#e74c3c')
        
        plt.tight_layout()
        output_file = output_dir / 'block_diagram.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight', facecolor='white')
        plt.close()
        
        print(f"✅ Block diagram saved to: {output_file}")
        print(f"   {total_modules} modules ({hls_count} HLS, {rtl_count} RTL) in {len(generations)} stages")
        print(f"   {total_connections} connections, {total_signal_count} total signals")

    def generate_all_plots(self, output_dir: Path):
        """Generate all visualization plots."""
        output_dir.mkdir(parents=True, exist_ok=True)

        print()
        print("=" * 60)
        print("Generating HLS Pipeline Visualizations")
        print("=" * 60)
        print()

        self.plot_pipeline_timeline(output_dir)
        self.plot_latency_comparison(output_dir)
        self.plot_frequency_comparison(output_dir)
        self.plot_resource_usage(output_dir)
        self.plot_latency_gaps(output_dir)
        self.plot_block_diagram(output_dir)

        print()
        print(f"✅ All plots saved to: {output_dir}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description='Visualize HLS pipeline metrics',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        '--design',
        type=Path,
        default=Path('design.yml'),
        help='Path to design.yml (default: design.yml)'
    )

    parser.add_argument(
        '--metrics',
        type=Path,
        default=Path('hls_metrics.json'),
        help='Path to hls_metrics.json (default: hls_metrics.json)'
    )

    parser.add_argument(
        '--output-dir', '-o',
        type=Path,
        default=Path('hls_plots'),
        help='Output directory for plots (default: hls_plots)'
    )

    args = parser.parse_args()

    # Check input files
    if not args.design.exists():
        print(f"Error: Design file not found: {args.design}")
        sys.exit(1)

    if not args.metrics.exists():
        print(f"Error: Metrics file not found: {args.metrics}")
        sys.exit(1)

    # Create visualizer and generate plots
    visualizer = HLSPipelineVisualizer(args.design, args.metrics)
    visualizer.generate_all_plots(args.output_dir)


if __name__ == '__main__':
    main()
