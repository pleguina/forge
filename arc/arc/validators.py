"""Validation utilities for hls-auto tool."""

from __future__ import annotations
import re
from pathlib import Path
from typing import List, Optional, Set

from ..core.constants import HDL_EXTENSIONS, SOURCE_EXTENSIONS, ALL_HLS_STAGES
from ..core.exceptions import ValidationError, ConfigurationError


def validate_identifier(name: str, context: str = "identifier") -> None:
    """Validate that a string is a valid identifier."""
    if not name:
        raise ValidationError(f"Empty {context}")
    
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', name):
        raise ValidationError(f"Invalid {context} '{name}': must start with letter and contain only alphanumeric characters and underscores")


def validate_file_exists(path: Path, context: str = "file") -> None:
    """Validate that a file exists."""
    if not path.exists():
        raise ValidationError(f"{context.title()} not found: {path}")
    
    if not path.is_file():
        raise ValidationError(f"{context.title()} is not a file: {path}")


def validate_directory_exists(path: Path, context: str = "directory") -> None:
    """Validate that a directory exists."""
    if not path.exists():
        raise ValidationError(f"{context.title()} not found: {path}")
    
    if not path.is_dir():
        raise ValidationError(f"{context.title()} is not a directory: {path}")


def validate_stages(stages: List[str]) -> None:
    """Validate HLS stages."""
    invalid_stages = set(stages) - set(ALL_HLS_STAGES)
    if invalid_stages:
        raise ValidationError(f"Invalid stages: {invalid_stages}. Valid stages: {ALL_HLS_STAGES}")


def validate_part_name(part: str) -> None:
    """Validate Xilinx part name format."""
    # Basic validation for Xilinx part names (e.g., xcvu13p-fsga2577-1-e)
    if not re.match(r'^x[a-z0-9]+-[a-z0-9]+-[0-9]+-[a-z]$', part):
        raise ValidationError(f"Invalid part name format: {part}")


def validate_clock_period(period: float) -> None:
    """Validate clock period."""
    if period <= 0:
        raise ValidationError(f"Clock period must be positive, got: {period}")
    
    if period < 1.0:  # Less than 1ns seems unrealistic for HLS
        raise ValidationError(f"Clock period {period}ns seems too fast for HLS")


def validate_source_files(files: List[Path], context: str = "source files") -> None:
    """Validate source files exist and have appropriate extensions."""
    for file_path in files:
        validate_file_exists(file_path, f"{context} file")
        
        if file_path.suffix.lower() not in SOURCE_EXTENSIONS | HDL_EXTENSIONS:
            raise ValidationError(f"Unsupported file extension: {file_path}")


def validate_positive_integer(value: int, name: str, minimum: int = 1) -> None:
    """Validate positive integer."""
    if not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{name} must be >= {minimum}, got: {value}")


def validate_connection_syntax(from_spec: str, to_spec: str) -> None:
    """Validate connection specification syntax."""
    # Basic validation for connection specs like "module[0]" or "module.port"
    pattern = r'^[a-zA-Z][a-zA-Z0-9_]*(\[[0-9]+\])?(\.[a-zA-Z][a-zA-Z0-9_]*)?$'
    
    if not re.match(pattern, from_spec):
        raise ValidationError(f"Invalid connection source format: {from_spec}")
    
    if not re.match(pattern, to_spec):
        raise ValidationError(f"Invalid connection destination format: {to_spec}")


class ConfigValidator:
    """Comprehensive configuration validator."""
    
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def add_error(self, message: str) -> None:
        """Add an error message."""
        self.errors.append(message)
    
    def add_warning(self, message: str) -> None:
        """Add a warning message."""
        self.warnings.append(message)
    
    def validate_config(self, config) -> None:
        """Validate complete configuration."""
        try:
            self._validate_basic_fields(config)
            self._validate_modules(config)
            self._validate_connections(config)
            self._validate_sweeps(config)
        except Exception as e:
            self.add_error(f"Validation error: {e}")
        
        if self.errors:
            error_msg = "\n".join(f"  - {err}" for err in self.errors)
            raise ConfigurationError(f"Configuration validation failed:\n{error_msg}")
    
    def _validate_basic_fields(self, config) -> None:
        """Validate basic configuration fields."""
        try:
            validate_part_name(config.part)
        except ValidationError as e:
            self.add_error(str(e))
        
        try:
            validate_clock_period(config.clock_period)
        except ValidationError as e:
            self.add_error(str(e))
        
        try:
            validate_positive_integer(config.max_parallel_jobs, "max_parallel_jobs")
        except ValidationError as e:
            self.add_error(str(e))
    
    def _validate_modules(self, config) -> None:
        """Validate module definitions."""
        if not config.modules:
            self.add_error("No modules defined")
            return
        
        module_names = set()
        for module in config.modules:
            # Check for duplicate names
            if module.name in module_names:
                self.add_error(f"Duplicate module name: {module.name}")
            module_names.add(module.name)
            
            # Validate module fields
            try:
                validate_identifier(module.name, "module name")
                validate_identifier(module.top, "module top function")
                validate_stages(module.stages)
                validate_positive_integer(module.instances, f"instances for module {module.name}")
            except ValidationError as e:
                self.add_error(f"Module {module.name}: {e}")
    
    def _validate_connections(self, config) -> None:
        """Validate connection definitions."""
        module_names = {m.name for m in config.modules}
        
        for i, conn in enumerate(config.connections):
            try:
                validate_connection_syntax(conn.from_, conn.to)
                
                # Extract module names from connection specs
                from_module = conn.from_.split('[')[0].split('.')[0]
                to_module = conn.to.split('[')[0].split('.')[0]
                
                if from_module not in module_names:
                    self.add_error(f"Connection {i}: unknown source module '{from_module}'")
                
                if to_module not in module_names:
                    self.add_error(f"Connection {i}: unknown destination module '{to_module}'")
                    
            except ValidationError as e:
                self.add_error(f"Connection {i}: {e}")
    
    def _validate_sweeps(self, config) -> None:
        """Validate sweep configurations."""
        module_names = {m.name for m in config.modules}
        
        for sweep_name, sweep_spec in config.sweeps.items():
            if sweep_name not in module_names:
                self.add_error(f"Sweep '{sweep_name}': unknown module")
            
            if sweep_spec.n_iters is not None:
                try:
                    validate_positive_integer(sweep_spec.n_iters, f"n_iters for sweep {sweep_name}")
                except ValidationError as e:
                    self.add_error(str(e))
