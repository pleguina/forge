"""Verilog linter using Verilator."""

from __future__ import annotations
import subprocess
import sys
from pathlib import Path
from typing import Optional, List, Tuple


def check_verilator_installed() -> bool:
    """Check if Verilator is installed and available."""
    try:
        result = subprocess.run(
            ["verilator", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def lint_verilog_file(
    verilog_file: Path,
    *,
    top_module: Optional[str] = None,
    include_dirs: Optional[List[Path]] = None,
    show_warnings: bool = True,
    strict: bool = False
) -> bool:
    """
    Lint a Verilog file using Verilator.
    
    Args:
        verilog_file: Path to the Verilog file to lint
        top_module: Optional top module name (auto-detected if not provided)
        include_dirs: Optional list of include directories
        show_warnings: Whether to show warnings (default: True)
        strict: Enable strict mode with more checks (default: False)
    
    Returns:
        True if linting passed, False if errors were found
    """
    if not verilog_file.exists():
        print(f"❌ File not found: {verilog_file}")
        return False
    
    if not check_verilator_installed():
        print("❌ Verilator not found!")
        print("   Please install Verilator:")
        print("   - Ubuntu/Debian: sudo apt-get install verilator")
        print("   - Fedora/RHEL: sudo dnf install verilator")
        print("   - macOS: brew install verilator")
        print("   - Or build from source: https://verilator.org/guide/latest/install.html")
        return False
    
    print(f"🔍 Linting Verilog file: {verilog_file}")
    
    # Build verilator command
    cmd = ["verilator", "--lint-only"]
    
    # Add warning flags
    if show_warnings:
        cmd.extend([
            "-Wall",  # Enable all warnings
        ])
    else:
        cmd.append("-Wno-lint")
    
    # Add strict mode flags
    if strict:
        cmd.extend([
            "-Wpedantic",
            "--error-limit", "100",
        ])
    
    # Add include directories
    if include_dirs:
        for inc_dir in include_dirs:
            if inc_dir.exists():
                cmd.extend(["-I", str(inc_dir)])
    
    # Add top module if specified
    if top_module:
        cmd.extend(["--top-module", top_module])
    
    # Add the file to lint
    cmd.append(str(verilog_file))
    
    # Run verilator
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # Print output
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            # Parse and format Verilator output
            errors = []
            warnings = []
            for line in result.stderr.split('\n'):
                if line.strip():
                    if '%Error' in line:
                        errors.append(line)
                    elif '%Warning' in line:
                        warnings.append(line)
                    else:
                        print(line)
            
            if errors:
                print(f"\n❌ ERRORS FOUND ({len(errors)}):")
                for error in errors:
                    print(f"  {error}")
            
            if warnings and show_warnings:
                print(f"\n⚠️  WARNINGS ({len(warnings)}):")
                for warning in warnings:
                    print(f"  {warning}")
        
        if result.returncode == 0:
            print(f"✅ Linting passed: {verilog_file.name}")
            return True
        else:
            print(f"❌ Linting failed: {verilog_file.name}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"❌ Verilator timed out while linting {verilog_file}")
        return False
    except Exception as e:
        print(f"❌ Error running Verilator: {e}")
        return False


def lint_verilog_files(
    verilog_files: List[Path],
    **kwargs
) -> Tuple[int, int]:
    """
    Lint multiple Verilog files.
    
    Returns:
        Tuple of (passed_count, failed_count)
    """
    passed = 0
    failed = 0
    
    for vfile in verilog_files:
        print(f"\n{'='*70}")
        if lint_verilog_file(vfile, **kwargs):
            passed += 1
        else:
            failed += 1
    
    print(f"\n{'='*70}")
    print(f"📊 SUMMARY: {passed} passed, {failed} failed")
    print(f"{'='*70}\n")
    
    return passed, failed


def get_verilator_version() -> Optional[str]:
    """Get the installed Verilator version."""
    try:
        result = subprocess.run(
            ["verilator", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            # Parse version from output like "Verilator 5.008 2023-06-18"
            lines = result.stdout.strip().split('\n')
            if lines:
                return lines[0].strip()
        return None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


if __name__ == "__main__":
    # Simple test
    if len(sys.argv) > 1:
        test_file = Path(sys.argv[1])
        success = lint_verilog_file(test_file)
        sys.exit(0 if success else 1)
    else:
        print("Usage: python verilog_linter.py <verilog_file>")
        sys.exit(1)
