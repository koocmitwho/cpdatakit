import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from cpdatakit import application as api
from cpdatakit.io import load_hdf5_v2

REFERENCE = Path(__file__).parents[1] / "examples/cpfe-tensile/reference"


def test_real_tensile_bundle_becomes_validated_experiment_and_training_data(tmp_path):
    assert hasattr(api, "integrate_tensile_bundle")
    output = tmp_path / "integrated"
    result = api.integrate_tensile_bundle(REFERENCE, output)
    assert result.ok, result.to_dict()
    experimental = load_hdf5_v2(output / "experiments.h5")
    training = load_hdf5_v2(output / "training.h5")
    assert experimental.data.sizes == {"record": 483}
    assert training.data.sizes == {"record": 729}
    with np.load(REFERENCE / "surrogate-data.npz", allow_pickle=False) as original:
        np.testing.assert_array_equal(
            training.data.nominal_axial_stress.values, original["targets"]
        )
        np.testing.assert_array_equal(
            training.data.engineering_strain.values, original["features"][:, 0]
        )
        np.testing.assert_array_equal(training.data.split.values, original["splits"])
    assert training.metadata["provenance"]["source_doi"] == "10.5281/zenodo.10820299"
    assert training.metadata["provenance"]["source_kind"] == "simulated"
    assert experimental.metadata["provenance"]["source_kind"] == "measured-derived"
    assert training.data.nominal_axial_stress.attrs["unit"] == "MPa"
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["validation"]["training"]["valid"]
    assert (
        manifest["outputs"]["training.h5"]
        == hashlib.sha256((output / "training.h5").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("mutation", ["unit", "source", "hash", "split", "shape"])
def test_integration_rejects_ambiguous_or_changed_scientific_inputs_without_outputs(
    tmp_path, mutation
):
    assert hasattr(api, "integrate_tensile_bundle")
    bundle = tmp_path / "bundle"
    shutil.copytree(REFERENCE, bundle)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if mutation == "unit":
        manifest["units"]["strain"] = "%"
    elif mutation == "source":
        manifest["raw_sources"] = {}
    elif mutation == "hash":
        (bundle / "experimental-curves.json").write_text("{}")
    else:
        with np.load(bundle / "surrogate-data.npz", allow_pickle=False) as data:
            arrays = {name: data[name].copy() for name in data.files}
        if mutation == "split":
            arrays["splits"][0] = "test"
        else:
            arrays["targets"] = arrays["targets"][:-1]
        np.savez_compressed(bundle / "surrogate-data.npz", **arrays)
        manifest["files"]["surrogate-data.npz"] = hashlib.sha256(
            (bundle / "surrogate-data.npz").read_bytes()
        ).hexdigest()
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "rejected"
    result = api.integrate_tensile_bundle(bundle, output)
    assert not result.ok
    assert not output.exists()


def test_integration_requires_new_output_directory(tmp_path):
    assert hasattr(api, "integrate_tensile_bundle")
    output = tmp_path / "existing"
    output.mkdir()
    (output / "keep.txt").write_text("keep")
    assert not api.integrate_tensile_bundle(REFERENCE, output).ok
    assert (output / "keep.txt").read_text(encoding="utf-8") == "keep"
