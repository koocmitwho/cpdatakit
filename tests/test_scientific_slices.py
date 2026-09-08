import json

import numpy as np
import pytest
import xarray as xr
from PIL import Image

from cpdatakit import application as api
from cpdatakit.data import ScientificDataset
from cpdatakit.formats import NetCDFWriter, ZarrWriter


@pytest.fixture(params=["netcdf", "zarr", "hdf5"])
def thermal_source(tmp_path, request):
    data = xr.Dataset(
        {"temperature": (("time", "y", "x"), np.arange(24, dtype=float).reshape(3, 2, 4) + 273.15)},
        coords={"time": [0.0, 10.0, 20.0], "y": [0.0, 2.0], "x": [0.0, 1.0, 2.0, 3.0]},
    )
    for name, unit in (("temperature", "K"), ("time", "s"), ("y", "mm"), ("x", "mm")):
        data[name].attrs["units"] = unit
    value = ScientificDataset(data, {"provenance": {"source": "synthetic UI fixture"}})
    path = (
        tmp_path
        / {"netcdf": "thermal.nc", "zarr": "thermal.zarr", "hdf5": "thermal.h5"}[request.param]
    )
    if request.param == "hdf5":
        from cpdatakit.io import write_hdf5_v2

        schema = {
            "schema_version": "2.0",
            "profile": "thermal",
            "dimensions": [{"name": n, "length": k} for n, k in data.sizes.items()],
            "coordinates": [
                {"name": n, "dims": [n], "dtype": "float", "unit": data[n].attrs["units"]}
                for n in data.coords
            ],
            "variables": [
                {
                    "name": "temperature",
                    "dims": ["time", "y", "x"],
                    "dtype": "float",
                    "unit": "K",
                    "role": "synthetic_field",
                }
            ],
        }
        write_hdf5_v2(value, path, schema)
    else:
        (NetCDFWriter() if request.param == "netcdf" else ZarrWriter()).write(value, path)
    return path


def test_inspection_supplies_variable_dtype_units_and_dimensions_without_loading(
    thermal_source, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("structural inspection materialized array data")

    monkeypatch.setattr(xr.Dataset, "load", forbidden)
    result = api.import_and_inspect(api.ImportInspectRequest(thermal_source))
    assert result.ok
    field = next(f for f in result.value["fields"] if f["name"] == "temperature")
    assert field["dims"] == ["time", "y", "x"]
    assert field["dtype"] == "float64"
    assert field["unit"] == "K"
    assert field["kind"] == "variable"


def test_slice_export_reads_one_time_step_and_records_position_and_units(
    thermal_source, tmp_path, monkeypatch
):
    assert hasattr(api, "plot_scientific_slice"), "a multidimensional slice service is required"
    original = xr.Dataset.load

    def bounded(data, **kwargs):
        assert data.sizes["time"] == 1
        return original(data, **kwargs)

    monkeypatch.setattr(xr.Dataset, "load", bounded)
    output = tmp_path / "slice.png"
    result = api.plot_scientific_slice(
        api.SliceRequest(
            thermal_source,
            "temperature",
            "x",
            "y",
            {"time": 1},
            output,
            workspace=tmp_path,
            vmin=273,
            vmax=310,
            cmap="magma",
        )
    )
    assert result.ok, result.to_dict()
    assert result.artifact == "slice.png"
    assert result.value["shape"] == [2, 4]
    assert result.value["unit"] == "K"
    assert result.value["slice"]["time"] == {"index": 1, "value": 10.0, "unit": "s"}
    assert result.value["data_range"] == [281.15, 288.15]
    with Image.open(output) as image:
        assert image.width > 500
        metadata = json.loads(image.info["Description"])
    assert metadata["variable"] == "temperature"
    assert metadata["color_limits"] == [273, 310]
    assert metadata["slice"] == result.value["slice"]


@pytest.mark.parametrize(
    "changes",
    [
        {"x": "time"},
        {"indices": {}},
        {"indices": {"time": 3}},
        {"x": "y"},
        {"variable": "unknown"},
        {"vmin": 400, "vmax": 300},
        {"vmin": float("nan")},
        {"max_pixels": 4},
    ],
)
def test_invalid_slice_never_creates_an_output(thermal_source, tmp_path, changes):
    assert hasattr(api, "SliceRequest")
    args = dict(
        data=thermal_source,
        variable="temperature",
        x="x",
        y="y",
        indices={"time": 1},
        output=tmp_path / "rejected.png",
    )
    args.update(changes)
    result = api.plot_scientific_slice(api.SliceRequest(**args))
    assert not result.ok
    assert not args["output"].exists()


def test_slice_export_does_not_overwrite_an_existing_image(thermal_source, tmp_path):
    assert hasattr(api, "SliceRequest")
    output = tmp_path / "existing.png"
    output.write_bytes(b"original")
    result = api.plot_scientific_slice(
        api.SliceRequest(thermal_source, "temperature", "x", "y", {"time": 0}, output)
    )
    assert not result.ok
    assert output.read_bytes() == b"original"


def test_datetime_slice_label_uses_iso_time_and_keeps_cf_time_encoding(tmp_path):
    source = tmp_path / "dated.nc"
    xr.Dataset(
        {"temperature": (("time", "y", "x"), np.ones((2, 2, 2)), {"units": "K"})},
        coords={
            "time": np.array(["2026-09-07T12:00:00", "2026-09-08T12:00:00"], dtype="datetime64[ns]")
        },
    ).to_netcdf(source, engine="h5netcdf")
    result = api.plot_scientific_slice(
        api.SliceRequest(source, "temperature", "x", "y", {"time": 1}, tmp_path / "dated.png")
    )
    assert result.ok, result.to_dict()
    position = result.value["slice"]["time"]
    assert position["value"].startswith("2026-09-08T12:00:00")
    assert "since" in position["encoding_unit"]


def test_nonfinite_pixels_are_masked_and_range_uses_only_finite_values(tmp_path):
    source = tmp_path / "missing.nc"
    xr.Dataset(
        {"temperature": (("y", "x"), [[1.0, float("inf")], [2.0, 3.0]], {"units": "K"})}
    ).to_netcdf(source, engine="h5netcdf")
    result = api.plot_scientific_slice(
        api.SliceRequest(source, "temperature", "x", "y", {}, tmp_path / "missing.png")
    )
    assert result.ok, result.to_dict()
    assert result.value["data_range"] == [1.0, 3.0]
    assert result.value["masked_pixels"] == 1
