import json

import numpy as np
import pytest
import xarray as xr

from cpdatakit import application as api
from cpdatakit.io import load_hdf5_v2


def test_schema_draft_observes_fields_but_does_not_invent_units_or_physical_roles(tmp_path):
    assert hasattr(api, "draft_schema")
    source = tmp_path / "raw.csv"
    source.write_text("load,extension\n10.0,0.1\n20.0,0.2\n")
    result = api.draft_schema(api.ImportInspectRequest(source))
    assert result.ok
    draft = result.value
    assert draft["ready"] is False
    fields = {item["name"]: item for item in draft["schema"]["fields"]}
    assert fields["load"]["dtype"] == "float"
    assert fields["load"]["unit"] is None
    assert fields["load"]["role"] is None
    assert any(item["property"] == "unit" and item["field"] == "load" for item in draft["review"])


def test_array_draft_preserves_observed_dimensions_and_declared_units(tmp_path):
    assert hasattr(api, "draft_schema")
    source = tmp_path / "raw.nc"
    xr.Dataset(
        {"temperature": (("time", "y", "x"), np.zeros((3, 2, 4)), {"units": "K"})}
    ).to_netcdf(source, engine="h5netcdf")
    result = api.draft_schema(api.ImportInspectRequest(source))
    assert result.ok
    schema = result.value["schema"]
    assert schema["schema_version"] == "2.0"
    assert schema["variables"][0]["dims"] == ["time", "y", "x"]
    assert schema["variables"][0]["unit"] == "K"
    assert schema["variables"][0]["role"] is None
    assert {d["name"]: d["length"] for d in schema["dimensions"]} == {"time": 3, "y": 2, "x": 4}


@pytest.fixture
def mapped_field(tmp_path):
    data = tmp_path / "field.nc"
    xr.Dataset(
        {"temp_C": (("t", "x", "y"), np.arange(12.0).reshape(2, 3, 2), {"units": "degC"})},
        coords={"t": ("t", [0.0, 1.0], {"units": "s"})},
    ).to_netcdf(data, engine="h5netcdf")
    schema = {
        "schema_version": "2.0",
        "profile": "thermal",
        "dimensions": [
            {"name": "time", "length": 2},
            {"name": "x", "length": 3},
            {"name": "y", "length": 2},
        ],
        "coordinates": [{"name": "time", "dtype": "float", "dims": ["time"], "unit": "s"}],
        "variables": [
            {
                "name": "temperature",
                "dtype": "float",
                "dims": ["time", "y", "x"],
                "unit": "K",
                "role": "measured_field",
            }
        ],
    }
    mapping = tmp_path / "mapping.json"
    mapping.write_text(
        json.dumps(
            {
                "mapping_version": "2.0",
                "dimensions": {"t": "time"},
                "mappings": [
                    {
                        "source": "temp_C",
                        "target": "temperature",
                        "input_unit": "degC",
                        "output_unit": "K",
                    }
                ],
            }
        )
    )
    return data, schema, mapping


def test_mapping_preview_and_conversion_share_array_renaming_units_and_transposition(
    mapped_field, tmp_path
):
    assert hasattr(api, "preview_mapping")
    data, schema, mapping = mapped_field
    preview = api.preview_mapping(api.DatasetRequest(data, schema, mapping))
    assert preview.ok, preview.to_dict()
    assert preview.value["validation"]["valid"]
    change = next(c for c in preview.value["fields"] if c["source"] == "temp_C")
    assert change["target"] == "temperature"
    assert change["source_dims"] == ["t", "x", "y"]
    assert change["target_dims"] == ["time", "y", "x"]
    assert change["source_unit"] == "degC" and change["target_unit"] == "K"
    assert change["after"][:3] == [273.15, 275.15, 277.15]
    result = api.convert_and_write(
        api.ConvertRequest(data, schema, tmp_path / "converted.h5", mapping)
    )
    assert result.ok, result.to_dict()
    loaded = load_hdf5_v2(tmp_path / "converted.h5")
    assert loaded.data.temperature.dims == ("time", "y", "x")
    np.testing.assert_allclose(
        loaded.data.temperature.values, np.arange(12.0).reshape(2, 3, 2).transpose(0, 2, 1) + 273.15
    )


def test_array_mapping_rejects_false_input_unit_declarations(mapped_field, tmp_path):
    assert hasattr(api, "preview_mapping")
    data, schema, mapping = mapped_field
    payload = json.loads(mapping.read_text(encoding="utf-8"))
    payload["mappings"][0]["input_unit"] = "MPa"
    mapping.write_text(json.dumps(payload))
    preview = api.preview_mapping(api.DatasetRequest(data, schema, mapping))
    assert not preview.ok
    result = api.convert_and_write(api.ConvertRequest(data, schema, tmp_path / "bad.h5", mapping))
    assert not result.ok
    assert not (tmp_path / "bad.h5").exists()


def test_tabular_mapping_preview_uses_existing_normalization(tmp_path):
    assert hasattr(api, "preview_mapping")
    source = tmp_path / "raw.csv"
    source.write_text("temp_C\n25\n30\n")
    schema = {
        "profile": "thermal",
        "schema_version": "1.0",
        "fields": [{"name": "temperature", "dtype": "float", "required": True, "unit": "K"}],
    }
    mapping = tmp_path / "mapping.json"
    mapping.write_text(
        json.dumps(
            {
                "mappings": [
                    {
                        "source": "temp_C",
                        "target": "temperature",
                        "input_unit": "degC",
                        "output_unit": "K",
                    }
                ]
            }
        )
    )
    result = api.preview_mapping(api.DatasetRequest(source, schema, mapping))
    assert result.ok
    assert result.value["validation"]["valid"]
    assert result.value["fields"][0]["after"] == [298.15, 303.15]


def test_parquet_draft_observes_arrow_dtypes_without_loading_rows(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = tmp_path / "table.parquet"
    pq.write_table(pa.table({"temperature": [1.0, 2.0], "label": ["a", "b"]}), path)

    def forbidden(*args, **kwargs):
        pytest.fail("draft loaded Parquet rows")

    monkeypatch.setattr(pq.ParquetFile, "read", forbidden)
    result = api.draft_schema(api.ImportInspectRequest(path))
    assert result.ok
    fields = {item["name"]: item for item in result.value["schema"]["fields"]}
    assert fields["temperature"]["dtype"] == "float"
    assert fields["label"]["dtype"] == "string"


def test_mapping_unit_conversion_does_not_reuse_lossy_integer_storage_encoding(
    mapped_field, tmp_path
):
    from cpdatakit.formats import NetCDFReader

    data, schema, mapping = mapped_field
    with xr.open_dataset(data, engine="h5netcdf") as opened:
        changed = opened.load()
    changed["temp_C"] = changed.temp_C.astype("int32")
    changed.to_netcdf(data, engine="h5netcdf", mode="w")
    output = tmp_path / "converted.nc"
    result = api.convert_and_write(
        api.ConvertRequest(data, schema, output, mapping, output_format="netcdf")
    )
    assert result.ok, result.to_dict()
    values = NetCDFReader().load(output).data.temperature.values
    np.testing.assert_allclose(values, np.arange(12.0).reshape(2, 3, 2).transpose(0, 2, 1) + 273.15)


def test_mapping_rejects_conflicting_unit_attributes(mapped_field):
    data, schema, mapping = mapped_field
    with xr.open_dataset(data, engine="h5netcdf") as opened:
        changed = opened.load()
    changed.temp_C.attrs["unit"] = "degC"
    changed.temp_C.attrs["units"] = "K"
    changed.to_netcdf(data, engine="h5netcdf", mode="w")
    result = api.preview_mapping(api.DatasetRequest(data, schema, mapping))
    assert not result.ok
    assert "conflict" in result.error.message.lower()


def test_mapping_preview_limits_decoded_array_bytes_before_loading(tmp_path, monkeypatch):
    from cpdatakit.formats import ReadLimits

    source = tmp_path / "compressed.nc"
    xr.Dataset({"temperature": (("time", "y", "x"), np.zeros((1, 256, 256)))}).to_netcdf(
        source, engine="h5netcdf", encoding={"temperature": {"zlib": True, "complevel": 9}}
    )

    def forbidden(*args, **kwargs):
        pytest.fail("decoded preview exceeded its byte budget")

    monkeypatch.setattr(xr.Dataset, "load", forbidden)
    result = api.preview_mapping(
        api.DatasetRequest(source, "curve"), read_limits=ReadLimits(max_bytes=65536)
    )
    assert not result.ok
    assert result.error.code == "read_limit_exceeded"


def test_full_validation_reports_conflicting_unit_attributes(tmp_path):
    source = tmp_path / "conflict.nc"
    xr.Dataset({"temperature": ("x", [1.0, 2.0], {"unit": "K", "units": "degC"})}).to_netcdf(
        source, engine="h5netcdf"
    )
    schema = {
        "profile": "thermal",
        "schema_version": "2.0",
        "dimensions": [{"name": "x", "length": 2}],
        "variables": [
            {
                "name": "temperature",
                "dims": ["x"],
                "dtype": "float",
                "unit": "K",
                "role": "measured_field",
            }
        ],
    }
    result = api.validate_and_summarize(api.DatasetRequest(source, schema))
    assert result.ok
    assert not result.value.validation.valid
    assert any(issue.code == "unit_conflict" for issue in result.value.validation.errors)
