from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import xarray as xr

from cpdatakit.data import ScientificDataset
from cpdatakit.exceptions import DataReadError, DataValidationError, OutputExistsError
from cpdatakit.formats import (
    NetCDFReader,
    NetCDFWriter,
    ParquetReader,
    ParquetWriter,
    ZarrReader,
    ZarrWriter,
)
from cpdatakit.model import Dataset

META = {
    "units": {"temperature": "K"},
    "provenance": {"source_description": "温度试验", "source_sha256": "a" * 64},
    "validation_summary": {"valid": False, "error_count": 1},
    "schema_sha256": "b" * 64,
    "field_mapping": {"T": "temperature"},
    "custom": [None, {"flag": True}],
}
KEY = "cpdatakit_metadata_json"


def adapters(kind):
    if kind == "parquet":
        return ParquetWriter(), ParquetReader(), ".parquet"
    if kind == "zarr":
        return ZarrWriter(), ZarrReader(), ".zarr"
    return NetCDFWriter(engine=kind), NetCDFReader(engine=kind), ".nc"


def value(kind):
    if kind == "parquet":
        return Dataset(pd.DataFrame({"temperature": [273.15, 283.15]}), deepcopy(META))
    return ScientificDataset(
        xr.Dataset(
            {"temperature": ("time", [273.15, 283.15])},
            coords={"time": [0, 1]},
            attrs={"title": "fixture"},
        ),
        deepcopy(META),
    )


@pytest.mark.parametrize("kind", ["h5netcdf", "netcdf4", "zarr", "parquet"])
def test_round_trip_preserves_metadata_and_does_not_mutate_input(tmp_path, kind):
    writer, reader, suffix = adapters(kind)
    original = value(kind)
    before = original.copy()
    target = tmp_path / ("thermal" + suffix)
    writer.write(original, target)
    loaded = reader.load(target)
    for key, expected in META.items():
        assert loaded.metadata[key] == expected
    assert original.metadata == before.metadata
    if kind == "parquet":
        pd.testing.assert_frame_equal(loaded.data, before.data)
    else:
        xr.testing.assert_identical(loaded.data, before.data)
        xr.testing.assert_identical(original.data, before.data)


@pytest.mark.parametrize("kind", ["h5netcdf", "netcdf4", "zarr", "parquet"])
@pytest.mark.parametrize(
    "payload",
    ["not json", "[]", '{"version":99,"metadata":{}}', '{"version":1,"metadata":{"x":NaN}}'],
)
def test_corrupt_metadata_is_rejected(tmp_path, kind, payload):
    _, reader, suffix = adapters(kind)
    target = tmp_path / ("corrupt" + suffix)
    if kind == "parquet":
        table = pa.table({"temperature": [1.0]}).replace_schema_metadata(
            {KEY.encode(): payload.encode()}
        )
        pq.write_table(table, target)
    else:
        data = xr.Dataset({"temperature": ("time", [1.0])}, attrs={KEY: payload})
        if kind == "zarr":
            data.to_zarr(target, zarr_format=3, consolidated=False)
        else:
            data.to_netcdf(target, engine=kind)
    with pytest.raises(DataReadError, match="metadata"):
        reader.load(target)


@pytest.mark.parametrize("kind", ["h5netcdf", "netcdf4", "zarr", "parquet"])
def test_legacy_files_without_envelope_remain_readable(tmp_path, kind):
    _, reader, suffix = adapters(kind)
    target = tmp_path / ("legacy" + suffix)
    if kind == "parquet":
        pd.DataFrame({"temperature": [1.0]}).to_parquet(target)
    else:
        data = xr.Dataset({"temperature": ("time", [1.0], {"unit": "K"})})
        if kind == "zarr":
            data.to_zarr(target, zarr_format=3, consolidated=False)
        else:
            data.to_netcdf(target, engine=kind)
    assert reader.load(target).data["temperature"].values.tolist() == [1.0]


@pytest.mark.parametrize("kind", ["h5netcdf", "zarr", "parquet"])
def test_invalid_metadata_fails_before_creating_output(tmp_path, kind):
    writer, _, suffix = adapters(kind)
    original = value(kind)
    original.metadata["invalid"] = float("nan")
    target = tmp_path / "new-directory" / ("invalid" + suffix)
    assert not writer.check(original).supported
    with pytest.raises(DataValidationError, match="metadata"):
        writer.write(original, target)
    assert not target.parent.exists()


@pytest.mark.parametrize("kind", ["h5netcdf", "zarr"])
def test_reserved_attribute_collision_does_not_discard_user_data(tmp_path, kind):
    writer, _, suffix = adapters(kind)
    original = value(kind)
    original.data.attrs[KEY] = "user attribute"
    target = tmp_path / ("collision" + suffix)
    assert not writer.check(original).supported
    with pytest.raises(DataValidationError, match="reserved"):
        writer.write(original, target)
    assert not target.exists()


@pytest.mark.parametrize("persistent_failure", [False, True])
def test_zarr_failed_promotion_keeps_old_store_recoverable(
    tmp_path, monkeypatch, persistent_failure
):
    target = tmp_path / "field.zarr"
    writer = ZarrWriter()
    old = value("zarr")
    writer.write(old, target)
    replacement = value("zarr")
    replacement.data["temperature"][:] = [999.0, 999.0]
    real_replace = os.replace
    failed = False

    def fail_promotion(source, destination):
        nonlocal failed
        if Path(destination) == target and (persistent_failure or not failed):
            failed = True
            raise OSError("simulated final rename failure")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_promotion)
    with pytest.raises(OSError):
        writer.write(replacement, target, force=True)
    candidates = [target, *tmp_path.glob(".field.zarr.backup-*/previous")]
    recovered = [p for p in candidates if p.exists()]
    assert len(recovered) == 1
    assert ZarrReader().load(recovered[0]).data["temperature"].values.tolist() == [273.15, 283.15]
    if not persistent_failure:
        assert target.exists()
        assert list(tmp_path.iterdir()) == [target]


def test_zarr_successful_overwrite_and_force_guard(tmp_path):
    target = tmp_path / "field.zarr"
    writer = ZarrWriter()
    writer.write(value("zarr"), target)
    replacement = value("zarr")
    replacement.data["temperature"][:] = [999.0, 999.0]
    with pytest.raises(OutputExistsError):
        writer.write(replacement, target)
    writer.write(replacement, target, force=True)
    assert ZarrReader().load(target).data["temperature"].values.tolist() == [999.0, 999.0]
    assert list(tmp_path.iterdir()) == [target]
