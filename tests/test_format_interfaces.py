from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from cpdatakit.model import Dataset


def _load_formats():
    # Exercise the installed package in wheel acceptance environments.
    return importlib.import_module("cpdatakit.formats.base")


def test_format_metadata_is_immutable_and_limits_are_positive() -> None:
    formats = _load_formats()
    info = formats.ReaderInfo("thermal-netcdf", "NetCDF", frozenset({"scientific"}), (".nc",))

    assert info.name == "thermal-netcdf"
    with pytest.raises(AttributeError):
        info.name = "other"  # type: ignore[misc]
    assert formats.ReadLimits(max_records=10, max_bytes=100).max_records == 10
    with pytest.raises(ValueError, match="max_records"):
        formats.ReadLimits(max_records=0)
    with pytest.raises(ValueError, match="max_bytes"):
        formats.ReadLimits(max_bytes=-1)


def test_selection_and_results_carry_explicit_boundary_messages() -> None:
    formats = _load_formats()
    selection = formats.Selection(fields=("temperature",), start=1, stop=3)
    assert selection.fields == ("temperature",)
    assert selection.start == 1 and selection.stop == 3
    with pytest.raises(ValueError, match="start"):
        formats.Selection(start=4, stop=3)
    assert formats.DetectionResult(matched=False, reason="extension mismatch").reason == (
        "extension mismatch"
    )
    assert formats.CapabilityResult(supported=False, messages=("object dtype",)).messages == (
        "object dtype",
    )


def test_reader_and_writer_protocols_accept_concrete_boundary_implementations() -> None:
    formats = _load_formats()

    class Reader:
        info = formats.ReaderInfo("fixture", "Fixture", frozenset({"tabular"}), (".fixture",))

        def detect(self, path: Path) -> formats.DetectionResult:
            return formats.DetectionResult(path.suffix == ".fixture")

        def inspect(self, path: Path, *, limits: formats.ReadLimits) -> dict[str, object]:
            return {"path": path.name, "max_records": limits.max_records}

        def load(self, path: Path, *, selection: formats.Selection | None = None) -> Dataset:
            return Dataset.__new__(Dataset)

    class Writer:
        info = formats.WriterInfo("fixture", "Fixture", frozenset({"tabular"}), (".fixture",))

        def check(self, data: object) -> formats.CapabilityResult:
            return formats.CapabilityResult(True)

        def write(self, data: object, output: Path, *, force: bool = False) -> Path:
            return output

    assert isinstance(Reader(), formats.DatasetReader)
    assert isinstance(Writer(), formats.DatasetWriter)
