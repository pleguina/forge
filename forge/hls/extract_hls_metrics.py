#!/usr/bin/env python3
"""
Extract HLS synthesis metrics from Vitis HLS csynth.xml reports and export to JSON.

This script parses the csynth.xml files for all modules in build_hls/ and extracts:
- Clock period (target and estimated) and frequency
- Initiation Interval (II)
- Latency (best/average/worst case)
- Resource utilization (BRAM, DSP, FF, LUT, URAM)

Usage:
    ./extract_hls_metrics.py [--output metrics.json] [--build-dir build_hls]
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional


class HLSMetricsExtractor:
    """Extract metrics from HLS csynth.xml reports."""

    def __init__(self, xml_file: Path):
        self.xml_file = xml_file
        self.tree = None
        self.root = None

        if xml_file.exists():
            try:
                self.tree = ET.parse(xml_file)
                self.root = self.tree.getroot()
            except ET.ParseError as e:
                print(f"Error parsing {xml_file}: {e}")

    def get_text(self, xpath: str, default="N/A") -> str:
        """Get text content from an XML element."""
        if self.root is None:
            return default
        elem = self.root.find(xpath)
        return elem.text if elem is not None and elem.text else default

    def get_float(self, xpath: str, default=0.0) -> float:
        """Get float value from an XML element."""
        text = self.get_text(xpath, None)
        if text is None or text == "N/A":
            return default
        try:
            return float(text)
        except (ValueError, TypeError):
            return default

    def get_int(self, xpath: str, default=0) -> int:
        """Get integer value from an XML element."""
        text = self.get_text(xpath, None)
        if text is None or text == "N/A":
            return default
        try:
            return int(text)
        except (ValueError, TypeError):
            return default

    def extract_timing(self) -> Dict:
        """Extract timing information."""
        target_clock_period = self.get_float("UserAssignments/TargetClockPeriod")
        estimated_clock_period = self.get_float(
            "PerformanceEstimates/SummaryOfTimingAnalysis/EstimatedClockPeriod"
        )

        timing = {
            "target_clock_period_ns": target_clock_period,
            "target_frequency_mhz": round(1000.0 / target_clock_period, 2) if target_clock_period > 0 else 0,
            "estimated_clock_period_ns": estimated_clock_period,
            "estimated_frequency_mhz": round(1000.0 / estimated_clock_period, 2) if estimated_clock_period > 0 else 0,
            "timing_met": estimated_clock_period <= target_clock_period if target_clock_period > 0 and estimated_clock_period > 0 else None,
            "slack_ns": round(target_clock_period - estimated_clock_period, 3) if target_clock_period > 0 and estimated_clock_period > 0 else 0
        }

        return timing

    def extract_latency(self) -> Dict:
        """Extract latency and II information."""
        base_path = "PerformanceEstimates/SummaryOfOverallLatency"

        latency = {
            "best_case_latency": self.get_int(f"{base_path}/Best-caseLatency"),
            "average_case_latency": self.get_int(f"{base_path}/Average-caseLatency"),
            "worst_case_latency": self.get_int(f"{base_path}/Worst-caseLatency"),
            "pipeline_ii": self.get_int(f"{base_path}/PipelineInitiationInterval"),
            "interval_min": self.get_int(f"{base_path}/Interval-min"),
            "interval_max": self.get_int(f"{base_path}/Interval-max"),
            "pipeline_depth": self.get_int(f"{base_path}/PipelineDepth"),
            "pipeline_type": self.get_text("PerformanceEstimates/PipelineType", "no")
        }

        return latency

    def extract_resources(self) -> Dict:
        """Extract resource utilization."""
        base_path = "AreaEstimates/Resources"
        avail_path = "AreaEstimates/AvailableResources"

        resources = {
            "BRAM_18K": self.get_int(f"{base_path}/BRAM_18K"),
            "DSP": self.get_int(f"{base_path}/DSP"),
            "FF": self.get_int(f"{base_path}/FF"),
            "LUT": self.get_int(f"{base_path}/LUT"),
            "URAM": self.get_int(f"{base_path}/URAM")
        }

        # Add available resources
        available = {
            "BRAM_18K": self.get_int(f"{avail_path}/BRAM_18K"),
            "DSP": self.get_int(f"{avail_path}/DSP"),
            "FF": self.get_int(f"{avail_path}/FF"),
            "LUT": self.get_int(f"{avail_path}/LUT"),
            "URAM": self.get_int(f"{avail_path}/URAM")
        }

        # Calculate utilization percentages
        utilization = {}
        for key in resources.keys():
            if available[key] > 0:
                utilization[key] = round(100.0 * resources[key] / available[key], 2)
            else:
                utilization[key] = 0.0

        return {
            "used": resources,
            "available": available,
            "utilization_percent": utilization
        }

    def extract_module_info(self) -> Dict:
        """Extract basic module information."""
        return {
            "top_module": self.get_text("UserAssignments/TopModelName"),
            "part": self.get_text("UserAssignments/Part"),
            "product_family": self.get_text("UserAssignments/ProductFamily"),
            "flow_target": self.get_text("UserAssignments/FlowTarget", "vivado"),
            "hls_version": self.get_text("ReportVersion/Version")
        }

    def extract_all(self) -> Dict:
        """Extract all metrics from the XML."""
        if self.root is None:
            return {
                "status": "error",
                "error": f"Failed to parse XML file: {self.xml_file}"
            }

        metrics = {
            "status": "success",
            "xml_file": str(self.xml_file),
            "module_info": self.extract_module_info(),
            "timing": self.extract_timing(),
            "latency": self.extract_latency(),
            "resources": self.extract_resources()
        }

        return metrics


def find_all_modules(build_dir: Path) -> List[Path]:
    """Find all HLS modules in the build directory."""
    if not build_dir.exists():
        print(f"Error: Build directory not found: {build_dir}")
        return []

    modules = []
    for module_dir in build_dir.iterdir():
        if module_dir.is_dir():
            # Check if csynth.xml exists
            xml_file = module_dir / "solution1" / "syn" / "report" / "csynth.xml"
            if xml_file.exists():
                modules.append(module_dir)

    return sorted(modules)


def extract_all_metrics(build_dir: Path) -> Dict[str, Dict]:
    """Extract metrics for all modules in the build directory."""
    modules = find_all_modules(build_dir)

    if not modules:
        print(f"No HLS modules with csynth.xml found in {build_dir}")
        return {}

    all_metrics = {}

    print(f"Found {len(modules)} modules with synthesis reports")
    print()

    for module_dir in modules:
        module_name = module_dir.name
        xml_file = module_dir / "solution1" / "syn" / "report" / "csynth.xml"

        print(f"Processing {module_name}...", end=' ')

        extractor = HLSMetricsExtractor(xml_file)
        metrics = extractor.extract_all()

        if metrics['status'] == 'success':
            print("✅")
        else:
            print(f"❌ {metrics.get('error', 'Unknown error')}")

        all_metrics[module_name] = metrics

    return all_metrics


def print_summary_table(metrics: Dict[str, Dict]):
    """Print a summary table of all modules."""
    print()
    print("=" * 120)
    print("HLS SYNTHESIS METRICS SUMMARY")
    print("=" * 120)
    print()

    # Header
    header = f"{'Module':<25} {'Freq (MHz)':<12} {'II':<5} {'Latency':<10} {'BRAM':<10} {'DSP':<10} {'FF':<12} {'LUT':<12}"
    print(header)
    print("-" * 120)

    for module_name, data in sorted(metrics.items()):
        if data['status'] != 'success':
            print(f"{module_name:<25} {'ERROR':<12}")
            continue

        timing = data.get('timing', {})
        latency = data.get('latency', {})
        resources = data.get('resources', {}).get('used', {})

        freq = timing.get('estimated_frequency_mhz', 0)
        ii = latency.get('pipeline_ii', 0)
        lat = latency.get('worst_case_latency', 0)
        bram = resources.get('BRAM_18K', 0)
        dsp = resources.get('DSP', 0)
        ff = resources.get('FF', 0)
        lut = resources.get('LUT', 0)

        # Format with color for timing violations
        freq_str = f"{freq:.1f}"
        if not timing.get('timing_met', True):
            freq_str = f"⚠ {freq_str}"

        row = f"{module_name:<25} {freq_str:<12} {ii:<5} {lat:<10} {bram:<10} {dsp:<10} {ff:<12} {lut:<12}"
        print(row)

    print("-" * 120)
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Extract HLS synthesis metrics from csynth.xml and export to JSON',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Extract metrics for all modules and save to JSON
  %(prog)s --output hls_metrics.json

  # Use a different build directory
  %(prog)s --build-dir my_build_hls --output metrics.json

  # Print summary table only (no JSON output)
  %(prog)s --summary-only

  # Pretty print JSON
  %(prog)s --output metrics.json --pretty
        """
    )

    parser.add_argument(
        '--build-dir',
        type=Path,
        default=Path('build_hls'),
        help='Path to HLS build directory (default: build_hls)'
    )

    parser.add_argument(
        '--output', '-o',
        type=Path,
        help='Output JSON file path'
    )

    parser.add_argument(
        '--pretty',
        action='store_true',
        help='Pretty print JSON output'
    )

    parser.add_argument(
        '--summary-only',
        action='store_true',
        help='Only print summary table, do not save JSON'
    )

    args = parser.parse_args()

    print()
    print("=" * 60)
    print("HLS Metrics Extraction Tool")
    print("=" * 60)
    print(f"Build directory: {args.build_dir}")
    print()

    # Extract metrics
    metrics = extract_all_metrics(args.build_dir)

    if not metrics:
        sys.exit(1)

    # Print summary table
    print_summary_table(metrics)

    # Prepare output with summary statistics
    total_resources = {
        'BRAM_18K': 0,
        'DSP': 0,
        'FF': 0,
        'LUT': 0,
        'URAM': 0
    }

    successful_count = 0
    for data in metrics.values():
        if data['status'] == 'success':
            successful_count += 1
            resources = data.get('resources', {}).get('used', {})
            for key in total_resources.keys():
                total_resources[key] += resources.get(key, 0)

    output = {
        'build_dir': str(args.build_dir),
        'modules': metrics,
        'summary': {
            'total_modules': len(metrics),
            'successful': successful_count,
            'failed': len(metrics) - successful_count,
            'total_resources': total_resources
        }
    }

    # Output results
    if not args.summary_only:
        json_str = json.dumps(output, indent=2 if args.pretty else None)

        if args.output:
            with open(args.output, 'w') as f:
                f.write(json_str)
            print(f"✅ Metrics saved to: {args.output}")
            print(f"   Total modules:  {output['summary']['total_modules']}")
            print(f"   Successful:     {output['summary']['successful']}")
            print(f"   Failed:         {output['summary']['failed']}")
            print()
        else:
            print("JSON Output:")
            print(json_str)
            print()


if __name__ == '__main__':
    main()
