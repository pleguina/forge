"""Exceptions for topgen package."""

from __future__ import annotations


class TopGenError(Exception):
    """Base exception for all topgen errors."""
    pass


class ConfigurationError(TopGenError):
    """Configuration file or validation errors."""
    pass


class IPParsingError(TopGenError):
    """IP metadata parsing errors."""
    pass


class PortMatchingError(TopGenError):
    """Port matching and connection errors."""
    pass


class FileNotFoundError(TopGenError):
    """File or directory not found errors."""
    pass


class ValidationError(TopGenError):
    """Input validation errors."""
    pass
