from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import pytest

import cpdatakit.plotting as plotting
from cpdatakit.exceptions import CPDataKitError, OutputExistsError
from cpdatakit.model import Dataset
from cpdatakit.plotting import (
    plot_counts,
    plot_field2d,
    plot_histogram,
    plot_stress_strain,
    save_figure,
)
from cpdatakit.schema import make_field_schema, make_profile_schema


@pytest.fixture(autouse=True)
def close_test_figures():
    yield
    plt.close("all")


@pytest.mark.parametrize("extension", [".png", ".svg"])
def test_stress_strain_export(curve: Dataset, tmp_path: Path, extension: str) -> None:
    fig, ax = plot_stress_strain(curve, "curve")
    assert "MPa" in ax.get_ylabel()
    output = tmp_path / f"curve{extension}"
    save_figure(fig, output)
    assert output.stat().st_size > 100
    with pytest.raises(OutputExistsError):
        save_figure(fig, output)


def test_histogram_counts_and_field2d(curve: Dataset) -> None:
    assert plot_histogram(curve, "curve", "stress")[1].get_legend() is not None
    points = Dataset(
        pd.DataFrame({"point_id": [0, 1], "grain_id": [0, 1]}),
        {"units": {"point_id": "1", "grain_id": "1"}},
    )
    assert plot_counts(points, "point", "grain_id")[1].get_legend() is not None
    field = Dataset(
        pd.DataFrame({"x": [0.0, 1.0], "y": [0.0, 1.0], "value": [1.0, 2.0]}),
        {"units": {"x": "um", "y": "um", "value": "1"}},
    )
    assert plot_field2d(field, "field2d")[1].get_title()


def test_histogram_rejects_shaped_numeric_fields(tmp_path: Path) -> None:
    schema = tmp_path / "vector.json"
    schema.write_text(
        '{"profile":"point","schema_version":"1.0","fields":['
        '{"name":"vector","dtype":"float","required":true,"shape":[2],"unit":"1"}]}',
        encoding="utf-8",
    )
    dataset = Dataset(pd.DataFrame({"vector": [[1.0, 2.0], [3.0, 4.0]]}))
    with pytest.raises(CPDataKitError, match="scalar numeric"):
        plot_histogram(dataset, schema, "vector")


def test_xy_plot_uses_declared_fields_and_units() -> None:
    schema = make_profile_schema(
        "thermal-cycle",
        [
            make_field_schema("time", "float", required=True, unit="s"),
            make_field_schema("temperature", "float", required=True, unit="K"),
        ],
    )
    dataset = Dataset(pd.DataFrame({"time": [0.0, 60.0], "temperature": [298.15, 373.15]}))

    _, ax = plotting.plot_xy(dataset, schema, "time", "temperature")

    assert ax.get_xlabel() == "time [s]"
    assert ax.get_ylabel() == "temperature [K]"
    assert ax.get_title() == "temperature vs time"


@pytest.mark.parametrize(
    ("field", "dtype", "shape", "message"),
    [
        ("undeclared", "float", (), "absent or undeclared"),
        ("vector", "float", (2,), "scalar numeric"),
        ("label", "string", (), "scalar numeric"),
    ],
)
def test_xy_plot_rejects_fields_outside_scalar_numeric_contract(
    field: str, dtype: str, shape: tuple[int, ...], message: str
) -> None:
    fields = [make_field_schema("time", "float", required=True, unit="s")]
    if field != "undeclared":
        fields.append(
            make_field_schema(
                field,
                dtype,  # type: ignore[arg-type]
                required=True,
                shape=shape,
                unit="K" if dtype == "float" else None,
            )
        )
    schema = make_profile_schema("thermal-cycle", fields)
    values = [[1.0, 2.0]] if shape else ["hold"]
    dataset = Dataset(pd.DataFrame({"time": [0.0], field: values}))

    with pytest.raises(CPDataKitError, match=message):
        plotting.plot_xy(dataset, schema, "time", field)


@pytest.mark.parametrize("extension", [".png", ".svg"])
def test_plot_exports_are_reproducible(curve: Dataset, tmp_path: Path, extension: str) -> None:
    outputs = []
    for name in ("first", "second"):
        fig, _ = plot_stress_strain(curve, "curve")
        output = tmp_path / f"{name}{extension}"
        save_figure(fig, output)
        plt.close(fig)
        outputs.append(output.read_bytes())
    assert outputs[0] == outputs[1]
