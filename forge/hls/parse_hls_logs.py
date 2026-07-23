#!/usr/bin/env python3
"""
Parse HLS log files to extract key information and summarize results.
"""

import sys
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional


class HLSLogParser:
    """Parse Vitis HLS log files and extract key information."""

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.results = {}

    def parse_csim_log(self) -> Dict:
        """Parse C simulation log."""
        log_file = self.log_dir / "csim.log"
        if not log_file.exists():
            return {"status": "not_run", "error": "Log file not found"}

        result = {
            "status": "unknown",
            "errors": [],
            "warnings": [],
            "info": []
        }

        with open(log_file, 'r') as f:
            content = f.read()

            # Check for success/failure
            if "✅ C-SIMULATION PASS" in content:
                result["status"] = "passed"
            elif "❌ C-SIMULATION FAILED" in content:
                result["status"] = "failed"
                # Extract error message
                error_match = re.search(r"Error: (.+)", content)
                if error_match:
                    result["errors"].append(error_match.group(1))

            # Extract warnings
            warnings = re.findall(r"WARNING: (.+)", content)
            result["warnings"].extend(warnings)

            # Look for testbench output
            if "Test PASS" in content:
                result["info"].append("Testbench reported PASS")
            elif "Test FAILED" in content:
                result["info"].append("Testbench reported FAILED")
                result["status"] = "failed"

        return result

    def parse_synth_log(self) -> Dict:
        """Parse synthesis log."""
        log_file = self.log_dir / "synth.log"
        if not log_file.exists():
            return {"status": "not_run", "error": "Log file not found"}

        result = {
            "status": "unknown",
            "errors": [],
            "warnings": [],
            "timing": {},
            "resources": {}
        }

        with open(log_file, 'r') as f:
            content = f.read()

            # Check for success/failure
            if "✅ SYNTHESIS PASS" in content:
                result["status"] = "passed"
            elif "❌ SYNTHESIS FAILED" in content:
                result["status"] = "failed"
                error_match = re.search(r"Error: (.+)", content)
                if error_match:
                    result["errors"].append(error_match.group(1))

            # Extract timing info
            latency_match = re.search(r"Latency.*?min\s+(\d+).*?max\s+(\d+)", content, re.DOTALL)
            if latency_match:
                result["timing"]["latency_min"] = int(latency_match.group(1))
                result["timing"]["latency_max"] = int(latency_match.group(2))

            interval_match = re.search(r"Interval.*?min\s+(\d+).*?max\s+(\d+)", content, re.DOTALL)
            if interval_match:
                result["timing"]["interval_min"] = int(interval_match.group(1))
                result["timing"]["interval_max"] = int(interval_match.group(2))

            # Extract resource usage
            resources_patterns = [
                (r"BRAM_18K\s+\|\s+(\d+)", "BRAM"),
                (r"DSP\s+\|\s+(\d+)", "DSP"),
                (r"FF\s+\|\s+(\d+)", "FF"),
                (r"LUT\s+\|\s+(\d+)", "LUT"),
            ]

            for pattern, name in resources_patterns:
                match = re.search(pattern, content)
                if match:
                    result["resources"][name] = int(match.group(1))

            # Extract warnings
            warnings = re.findall(r"WARNING: (.+)", content)
            result["warnings"].extend(warnings)

        return result

    def parse_cosim_log(self) -> Dict:
        """Parse co-simulation log."""
        log_file = self.log_dir / "cosim.log"
        if not log_file.exists():
            return {"status": "not_run", "error": "Log file not found"}

        result = {
            "status": "unknown",
            "errors": [],
            "warnings": [],
            "latency": {},
            "info": []
        }

        with open(log_file, 'r') as f:
            content = f.read()

            # Check for success/failure
            if "✅ CO-SIMULATION PASS" in content:
                result["status"] = "passed"
            elif "❌ CO-SIMULATION FAILED" in content:
                result["status"] = "failed"
                error_match = re.search(r"Error: (.+)", content)
                if error_match:
                    result["errors"].append(error_match.group(1))

            # Check if RTL matched C simulation
            if "PASS" in content and "RTL" in content:
                result["info"].append("RTL matches C simulation")
            elif "FAIL" in content and "RTL" in content:
                result["status"] = "failed"
                result["errors"].append("RTL does not match C simulation")

            # Extract warnings
            warnings = re.findall(r"WARNING: (.+)", content)
            result["warnings"].extend(warnings)

        return result

    def parse_all(self) -> Dict:
        """Parse all available logs."""
        return {
            "csim": self.parse_csim_log(),
            "synth": self.parse_synth_log(),
            "cosim": self.parse_cosim_log()
        }

    def print_summary(self, results: Dict):
        """Print a human-readable summary of the results."""
        print("=" * 60)
        print(f"HLS LOG SUMMARY: {self.log_dir.name}")
        print("=" * 60)
        print()

        # CSIM
        csim = results.get("csim", {})
        print("C SIMULATION:")
        self._print_phase_summary(csim)

        # Synthesis
        synth = results.get("synth", {})
        print("\nSYNTHESIS:")
        self._print_phase_summary(synth)
        if synth.get("status") == "passed":
            if synth.get("timing"):
                print("  Timing:")
                for key, val in synth["timing"].items():
                    print(f"    {key}: {val}")
            if synth.get("resources"):
                print("  Resources:")
                for key, val in synth["resources"].items():
                    print(f"    {key}: {val}")

        # COSIM
        cosim = results.get("cosim", {})
        print("\nCO-SIMULATION:")
        self._print_phase_summary(cosim)

        print()
        print("=" * 60)

    def _print_phase_summary(self, phase_result: Dict):
        """Print summary for one phase (csim/synth/cosim)."""
        status = phase_result.get("status", "unknown")

        if status == "not_run":
            print(f"  Status: ⏭️  NOT RUN")
            return
        elif status == "passed":
            print(f"  Status: ✅ PASS")
        elif status == "failed":
            print(f"  Status: ❌ FAILED")
        else:
            print(f"  Status: ❓ {status.upper()}")

        # Print errors
        errors = phase_result.get("errors", [])
        if errors:
            print("  Errors:")
            for err in errors:
                print(f"    - {err}")

        # Print warnings (only first 3)
        warnings = phase_result.get("warnings", [])
        if warnings:
            print(f"  Warnings: {len(warnings)} total")
            for warn in warnings[:3]:
                print(f"    - {warn}")
            if len(warnings) > 3:
                print(f"    ... and {len(warnings) - 3} more")

        # Print info
        info = phase_result.get("info", [])
        if info:
            print("  Info:")
            for i in info:
                print(f"    - {i}")


def main():
    if len(sys.argv) < 2:
        print("Usage: parse_hls_logs.py <log_directory>")
        print("\nExample:")
        print("  parse_hls_logs.py logs/hls/dt_interface")
        sys.exit(1)

    log_dir = Path(sys.argv[1])

    if not log_dir.exists():
        print(f"Error: Directory not found: {log_dir}")
        sys.exit(1)

    parser = HLSLogParser(log_dir)
    results = parser.parse_all()
    parser.print_summary(results)


if __name__ == "__main__":
    main()
