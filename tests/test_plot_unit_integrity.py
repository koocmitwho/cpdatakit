"""Units describe the plotted values; mappings must agree with source declarations."""

import numpy as np
import pandas as pd
import pytest
from matplotlib import pyplot as plt

from cpdatakit.application import PlotRequest, plot_declared_fields
from cpdatakit.exceptions import CPDataKitError, NormalizationError
from cpdatakit.io import write_hdf5
from cpdatakit.model import Dataset
from cpdatakit.normalization import FieldMapping, normalize_dataset
from cpdatakit.plotting import plot_field2d, plot_histogram, plot_stress_strain, plot_xy
from cpdatakit.schema import load_schema, make_field_schema, make_profile_schema
from cpdatakit.validation import validate_dataset


def pascal_curve():
    return Dataset(
        pd.DataFrame({"step": [0, 1], "strain": [0.0, 0.01], "stress": [0.0, 100_000_000.0]}),
        {"units": {"step": "dimensionless", "strain": "dimensionless", "stress": "Pa"}},
    )


def test_stress_plot_labels_the_actual_values_and_uses_a_neutral_legend():
    value = pascal_curve()
    figure, axes = plot_stress_strain(value, "curve")
    try:
        assert axes.get_ylabel() == "Stress [Pa]"
        assert axes.lines[0].get_ydata().tolist() == [0.0, 100_000_000.0]
        assert axes.get_legend().get_texts()[0].get_text() == "stress"
        assert value.metadata["units"]["stress"] == "Pa"
    finally:
        plt.close(figure)


def test_histogram_labels_stored_units():
    figure, axes = plot_histogram(pascal_curve(), "curve", "stress")
    try:
        assert axes.get_xlabel() == "stress [Pa]"
    finally:
        plt.close(figure)


@pytest.mark.parametrize(
    ("stored", "declared", "values"),
    [("Pa", "MPa", [0.0, 1e8]), ("mm", "m", [0.0, 1000.0]), ("degC", "K", [0.0, 100.0])],
)
def test_xy_plot_keeps_actual_scale_and_offset_without_implicit_conversion(
    stored, declared, values
):
    schema = make_profile_schema(
        "units",
        [
            make_field_schema("time", "float", required=True, unit="s"),
            make_field_schema("reading", "float", required=True, unit=declared),
        ],
    )
    value = Dataset(
        pd.DataFrame({"time": [0.0, 60.0], "reading": values}),
        {"units": {"time": "min", "reading": stored}},
    )
    figure, axes = plot_xy(value, schema, "time", "reading")
    try:
        assert axes.get_xlabel() == "time [min]"
        assert axes.get_ylabel() == f"reading [{stored}]"
        assert axes.lines[0].get_ydata().tolist() == values
        assert value.data.reading.tolist() == values
    finally:
        plt.close(figure)


def test_field_plot_uses_stored_axis_and_color_units():
    schema = make_profile_schema(
        "field",
        [
            make_field_schema("x", "float", unit="m"),
            make_field_schema("y", "float", unit="m"),
            make_field_schema("value", "float", unit="MPa"),
        ],
    )
    value = Dataset(
        pd.DataFrame({"x": [0.0, 1000.0], "y": [0.0, 2000.0], "value": [0.0, 1e8]}),
        {"units": {"x": "mm", "y": "mm", "value": "Pa"}},
    )
    figure, axes = plot_field2d(value, schema)
    try:
        assert axes.get_xlabel() == "x [mm]"
        assert axes.get_ylabel() == "y [mm]"
        assert figure.axes[1].get_ylabel() == "Value [Pa]"
        np.testing.assert_array_equal(axes.collections[0].get_array(), value.data.value)
    finally:
        plt.close(figure)


@pytest.mark.parametrize("shape", [(), (2,)])
def test_mapping_rejects_source_unit_conflict_before_converting(shape):
    raw = [[100.0, 200.0]] if shape else [100.0]
    schema = make_profile_schema(
        "units", [make_field_schema("stress", "float", shape=shape, unit="MPa")]
    )
    source = Dataset(pd.DataFrame({"stress": raw}), {"units": {"stress": "MPa"}})
    with pytest.raises(NormalizationError, match=r"unit.*conflict|conflict.*unit"):
        normalize_dataset(source, schema, [FieldMapping("stress", "stress", "Pa", "MPa")])
    assert source.data.stress.tolist() == raw
    assert source.metadata["units"] == {"stress": "MPa"}


@pytest.mark.parametrize("stored", ["pascal", "newton / meter ** 2"])
def test_mapping_accepts_unit_aliases_and_performs_explicit_conversion(stored):
    source = pascal_curve()
    source.metadata["units"]["stress"] = stored
    result = normalize_dataset(source, "curve", [FieldMapping("stress", "stress", "Pa", "MPa")])
    assert result.data.stress.tolist() == [0.0, 100.0]
    assert result.metadata["units"]["stress"] == "MPa"
    assert source.data.stress.tolist() == [0.0, 100_000_000.0]


def test_hdf5_plot_service_exports_stored_unit_labels(tmp_path):
    source = tmp_path / "curve.h5"
    value = pascal_curve()
    write_hdf5(value, source, load_schema("curve"), validate_dataset(value, "curve"))
    output = tmp_path / "curve.svg"
    result = plot_declared_fields(PlotRequest(source, "curve", output, "stress-strain"))
    assert result.ok, result.to_dict()
    rendered = output.read_text(encoding="utf-8")
    assert "Stress [Pa]" in rendered
    assert "Synthetic curve" not in rendered


def test_mapping_rejects_an_offset_conflict_even_with_compatible_dimensions():
    schema = make_profile_schema(
        "temperature", [make_field_schema("temperature", "float", unit="K")]
    )
    source = Dataset(
        pd.DataFrame({"temperature": [0.0, 100.0]}), {"units": {"temperature": "degC"}}
    )
    with pytest.raises(NormalizationError, match="conflicts"):
        normalize_dataset(source, schema, [FieldMapping("temperature", "temperature", "K", "K")])
    assert source.data.temperature.tolist() == [0.0, 100.0]


@pytest.mark.parametrize("kind", ["curve", "histogram", "xy", "field"])
def test_invalid_plot_units_do_not_leave_an_open_figure(kind):
    value = pascal_curve()
    value.metadata["units"]["stress"] = None
    before = plt.get_fignums()
    try:
        with pytest.raises(CPDataKitError, match="unit"):
            if kind == "curve":
                plot_stress_strain(value, "curve")
            elif kind == "histogram":
                plot_histogram(value, "curve", "stress")
            elif kind == "xy":
                plot_xy(value, "curve", "strain", "stress")
            else:
                field = Dataset(
                    pd.DataFrame({"x": [0.0], "y": [0.0], "value": [1.0]}),
                    {"units": {"value": None}},
                )
                plot_field2d(field, "field2d")
        assert plt.get_fignums() == before
    finally:
        plt.close("all")
