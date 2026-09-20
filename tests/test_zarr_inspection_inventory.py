"""Observe directory visits and actual chunk reads during structural inspection."""

from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from zarr.storage import LocalStore

from cpdatakit.application.data_access import ReadLimitError, inspect_input
from cpdatakit.exceptions import DataReadError
from cpdatakit.formats import ReadLimits, ZarrReader


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "field.zarr"
    xr.Dataset({"v": ("record", np.arange(20))}).to_zarr(
        path, consolidated=False, zarr_format=3, encoding={"v": {"chunks": (2,)}}
    )
    return path


def test_inspection_inventories_once_without_reading_numeric_chunks(store, monkeypatch):
    visits = []
    reads = []
    original_walk = Path.rglob
    original_get = LocalStore.get

    def walk(path, *args, **kwargs):
        if path == store:
            visits.append([])
        for item in original_walk(path, *args, **kwargs):
            if path == store:
                visits[-1].append(item.relative_to(store).as_posix())
            yield item

    async def get(storage, key, *args, **kwargs):
        result = await original_get(storage, key, *args, **kwargs)
        if "/c/" in key and result is not None:
            reads.append(key)
        return result

    monkeypatch.setattr(Path, "rglob", walk)
    monkeypatch.setattr(LocalStore, "get", get)
    result = inspect_input(store, None, ReadLimits())
    assert result["record_count"] == 20
    assert reads == []
    assert len(visits) == 1, [len(visit) for visit in visits]
    assert len(visits[0]) == len(set(visits[0])) == 14


@pytest.mark.parametrize("direct", [False, True])
def test_inventory_stops_at_byte_limit_before_opening_store(store, monkeypatch, direct):
    visited = []
    original_walk = Path.rglob

    def walk(path, *args, **kwargs):
        for item in original_walk(path, *args, **kwargs):
            visited.append(item)
            yield item

    monkeypatch.setattr(Path, "rglob", walk)
    limits = ReadLimits(max_bytes=1)
    with pytest.raises(DataReadError if direct else ReadLimitError, match="byte limit"):
        if direct:
            ZarrReader().inspect(store, limits=limits)
        else:
            inspect_input(store, None, limits)
    assert len(visited) < 14


@pytest.mark.parametrize("direct", [False, True])
def test_inspection_rejects_link_entries(store, monkeypatch, direct):
    # Model a link on platforms where creating symlinks needs elevated privileges.
    # The directory and all metadata/chunks remain real; only its link flag is injected.
    linked = store / "v" / "zarr.json"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked or original(path))
    with pytest.raises(DataReadError, match="symbolic link"):
        if direct:
            ZarrReader().inspect(store, limits=ReadLimits())
        else:
            inspect_input(store, None, ReadLimits())


def test_direct_inventory_preserves_file_count(store):
    result = ZarrReader().inspect(store, limits=ReadLimits())
    assert result["store_entries"] == 12


def test_non_zarr_byte_limit_precedes_format_detection(tmp_path, monkeypatch):
    from cpdatakit.application import data_access

    path = tmp_path / "oversized.nc"
    path.write_bytes(b"CDF\0")

    def detect(path):
        raise AssertionError("Format detection read an already oversized input")

    monkeypatch.setattr(data_access, "reader_for", detect)
    with pytest.raises(ReadLimitError, match="byte limit"):
        inspect_input(path, None, ReadLimits(max_bytes=1))
