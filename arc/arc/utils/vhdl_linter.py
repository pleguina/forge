"""VHDL linting utilities using GHDL."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional, List


def check_ghdl_installed() -> bool:
    """Check if GHDL is installed and available."""
    try:
        result = subprocess.run(
            ["ghdl", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def get_ghdl_version() -> Optional[str]:
    """Get the installed GHDL version."""
    try:
        result = subprocess.run(
            ["ghdl", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            if lines:
                return lines[0].strip()
        return None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def lint_vhdl_file(
    file_path,
    std="08",
    ieee="synopsys",
    work_dir=None,
    fix=False
):
    """Lint a VHDL file using GHDL syntax checker."""
    file_path = Path(file_path)
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return False
    
    if not check_ghdl_installed():
        print("❌ GHDL not found!")
        print("   Please install GHDL:")
        print("   - Ubuntu/Debian: sudo apt-get install ghdl")
        print("   - Fedora/RHEL: sudo dnf install ghdl")
        print("   - macOS: brew install ghdl")
        return False
    
    if fix:
        print("ℹ️  Note: GHDL performs syntax checking only (no auto-fix available)")
    
    print(f"🔍 Linting VHDL file: {file_path}")
    
    cmd = [
        "ghdl",
        "-s",
        f"--std={std}",
        f"--ieee={ieee}",
        "--warn-error",
        str(file_path)
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        has_errors = False
        has_warnings = False
        
        output = result.stdout + result.stderr
        if output.strip():
            for line in output.split('\n'):
                if line.strip():
                    if 'error:' in line.lower():
                        has_errors = True
                        print(f"  ❌ {line}")
                    elif 'warning:' in line.lower():
                        has_warnings = True
                        print(f"  ⚠️  {line}")
                    else:
                        print(f"     {line}")
        
        if result.returncode == 0 and not has_errors:
            if has_warnings:
                print(f"✅ Syntax check passed (with warnings): {file_path.name}")
            else:
                print(f"✅ Syntax check passed: {file_path.name}")
            return True
        else:
            print(f"❌ Syntax check failed: {file_path.name}")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"❌ GHDL timed out while checking {file_path}")
        return False
    except Exception as e:
        print(f"❌ Error running GHDL: {e}")
        return False


def lint_vhdl_files(vhdl_files, **kwargs):
    """Lint multiple VHDL files."""
    passed = 0
    failed = 0
    
    for vfile in vhdl_files:
        print(f"\n{'='*70}")
        if lint_vhdl_file(vfile, **kwargs):
            passed += 1
        else:
            failed += 1
    
    print(f"\n{'='*70}")
    print(f"📊 SUMMARY: {passed} passed, {failed} failed")
    print(f"{'='*70}\n")
    
    return passed, failed
