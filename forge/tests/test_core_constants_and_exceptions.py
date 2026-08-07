"""Coverage for forge/core/constants.py and forge/core/exceptions.py
(previously 0% — both are pure module-level definitions with no prior
importer in the test suite)."""

from __future__ import annotations

import pytest

from forge.core.constants import ALL_HLS_STAGES, HDL_EXTENSIONS, SOURCE_EXTENSIONS
from forge.core.exceptions import (
    ConfigurationError,
    FileNotFoundError as ForgeFileNotFoundError,
    IPParsingError,
    PortMatchingError,
    TopGenError,
    ValidationError,
)


class TestConstants:
    def test_hdl_extensions_cover_verilog_and_vhdl(self) -> None:
        assert HDL_EXTENSIONS == {".v", ".sv", ".vhd", ".vhdl"}

    def test_source_extensions_cover_c_family(self) -> None:
        assert SOURCE_EXTENSIONS == {".cpp", ".c", ".h", ".hpp", ".cc", ".cxx"}

    def test_all_hls_stages_in_pipeline_order(self) -> None:
        assert ALL_HLS_STAGES == ("csim", "synth", "cosim", "export")


class TestExceptionHierarchy:
    @pytest.mark.parametrize(
        "exc_cls",
        [
            ConfigurationError,
            IPParsingError,
            PortMatchingError,
            ForgeFileNotFoundError,
            ValidationError,
        ],
    )
    def test_all_derive_from_topgen_error(self, exc_cls: type) -> None:
        assert issubclass(exc_cls, TopGenError)
        assert issubclass(exc_cls, Exception)

    def test_message_is_preserved(self) -> None:
        try:
            raise ConfigurationError("bad config")
        except TopGenError as exc:
            assert str(exc) == "bad config"

    def test_forge_file_not_found_error_is_not_builtin(self) -> None:
        # This class deliberately shadows the builtin FileNotFoundError
        # within forge.core.exceptions — confirm it's the forge type, not
        # accidentally caught as/conflated with the builtin OSError subclass.
        assert not issubclass(ForgeFileNotFoundError, OSError)
