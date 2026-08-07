"""Utility functions for hls-auto tool."""

from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Dict


def sanitize_identifier(name: str) -> str:
    """Sanitize a string to be a valid VHDL/Verilog identifier."""
    if not name:
        return "unnamed"
    
    # Replace non-alphanumeric characters with underscores
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    
    # Collapse multiple underscores
    name = re.sub(r'_+', '_', name)
    
    # Strip leading/trailing underscores
    name = name.strip('_')
    
    # Ensure starts with letter
    if not name or not name[0].isalpha():
        name = f"id_{name}"
    
    return name


def ensure_path_exists(path: Path) -> Path:
    """Ensure a path exists, creating parent directories as needed."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_yaml_safe(path: Path) -> Dict[str, Any]:
    """Load YAML file with error handling."""
    import yaml
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        from ..exceptions import ConfigurationError
        raise ConfigurationError(f"Invalid YAML in {path}: {e}")
    except Exception as e:
        from ..exceptions import FileNotFoundError
        raise FileNotFoundError(f"Cannot read {path}: {e}")


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"


def format_memory(bytes_val: int) -> str:
    """Format memory size in human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024:
            return f"{bytes_val:.1f}{unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f}TB"
