"""Audited integration of the published KupferDigital/experiment-to-CPFE case."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
import xarray as xr

from ..data import ScientificDataset
from ..exceptions import DataValidationError, OutputExistsError
from ..io import write_hdf5_v2
from ..schemas import resolve_schema_v2
from .data_access import path_sha256, validate_value
from .services import ServiceResult, _failure

_FEATURES = ["engineering_strain", "E", "yield_stress", "plastic_modulus"]
_ROLES = {"H_08": "calibration", "H_16": "model_check", "H_18": "final_holdout"}


def _require(condition, message):
    if not condition:
        raise DataValidationError(message)


def _scientific(columns, units, roles, provenance):
    variables = {
        name: (("record",), np.asarray(values), {"unit": units[name], "role": roles[name]})
        for name, values in columns.items()
    }
    value = ScientificDataset(xr.Dataset(variables), {"provenance": provenance})
    schema = resolve_schema_v2(
        {
            "profile": "kupfer-tensile-" + provenance["source_kind"],
            "schema_version": "2.0",
            "dimensions": [{"name": "record", "length": value.data.sizes["record"]}],
            "variables": [
                {
                    "name": name,
                    "dims": ["record"],
                    "dtype": "string" if value.data[name].dtype.kind in "US" else "float",
                    "unit": units[name],
                    "role": roles[name],
                }
                for name in columns
            ],
        }
    )
    return value, schema


def _load_bundle(bundle):
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    _require(manifest.get("bundle_version") == 1, "Unsupported tensile bundle version")
    _require(
        manifest.get("source_doi") == "10.5281/zenodo.10820299"
        and manifest.get("license") == "CC-BY-4.0",
        "Tensile bundle must retain the source DOI and CC-BY-4.0 license",
    )
    _require(
        manifest.get("units") == {"strain": "1", "stress": "MPa"},
        "Tensile units must be declared as 1 and MPa",
    )
    _require(
        manifest.get("strain_window") == [0, 0.008],
        "This integration covers 0-0.8% engineering strain",
    )
    _require(
        manifest.get("strain_definition") == "engineering strain from preload origin"
        and manifest.get("stress_definition") == "nominal stress increment from preload origin",
        "Explicit strain and stress definitions are required",
    )
    _require(
        manifest.get("experiment_roles") == _ROLES,
        "Experiment calibration/check/holdout roles must be retained",
    )
    _require(
        set(manifest.get("raw_sources", {})) == set(_ROLES), "Raw source identities are missing"
    )
    for source in manifest["raw_sources"].values():
        _require(
            bool(source.get("member"))
            and bool(re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", ""))),
            "Raw source hash or member is missing",
        )
    for name in (
        "experimental-curves.json",
        "surrogate-data.npz",
        "training-config.json",
        "upstream-summary.json",
    ):
        path = bundle / name
        _require(
            not path.is_symlink() and path_sha256(path) == manifest.get("files", {}).get(name),
            f"Bundle input hash mismatch: {name}",
        )
    config = json.loads((bundle / "training-config.json").read_text(encoding="utf-8"))
    _require(
        config.get("feature_names") == _FEATURES
        and config.get("feature_units") == ["1", "MPa", "MPa", "MPa"]
        and config.get("target_name") == "nominal_axial_stress"
        and config.get("target_unit") == "MPa",
        "Training fields and units do not match this integration contract",
    )
    shared = {
        name: manifest[name]
        for name in (
            "source_doi",
            "license",
            "source_authors",
            "upstream_repository",
            "upstream_release",
            "upstream_commit",
            "strain_definition",
            "stress_definition",
            "strain_window",
            "processing",
            "raw_sources",
            "physical_scope",
        )
    }
    shared["bundle_manifest_sha256"] = path_sha256(bundle / "manifest.json")
    shared["input_hashes"] = manifest["files"]
    shared["integration_kind"] = "owned-repository integration"
    experiments = json.loads((bundle / "experimental-curves.json").read_text(encoding="utf-8"))
    _require(set(experiments) == set(_ROLES), "Expected H_08, H_16 and H_18 experimental curves")
    columns = {
        name: [] for name in ("engineering_strain", "nominal_axial_stress", "specimen", "role")
    }
    for name, curve in experiments.items():
        strain, stress = np.asarray(curve["strain"]), np.asarray(curve["stress"])
        _require(
            strain.ndim == stress.ndim == 1 and strain.size == stress.size and strain.size > 1,
            "Experimental strain/stress dimensions differ",
        )
        _require(
            np.all(np.diff(strain) > 0) and strain[0] >= 0 and strain[-1] <= 0.008,
            "Experimental strain must increase within the stated window",
        )
        for field, values in (
            ("engineering_strain", strain),
            ("nominal_axial_stress", stress),
            ("specimen", [name] * len(strain)),
            ("role", [_ROLES[name]] * len(strain)),
        ):
            columns[field].extend(values)
    experimental = _scientific(
        columns,
        {"engineering_strain": "1", "nominal_axial_stress": "MPa", "specimen": None, "role": None},
        {
            "engineering_strain": "measured_derived",
            "nominal_axial_stress": "measured_derived",
            "specimen": "identifier",
            "role": "experiment_partition",
        },
        {**shared, "source_kind": "measured-derived"},
    )
    with np.load(bundle / "surrogate-data.npz", allow_pickle=False) as data:
        _require(
            set(data.files) == {"features", "targets", "groups", "splits"},
            "Unexpected training array fields",
        )
        features, targets, groups, splits = (
            data[name] for name in ("features", "targets", "groups", "splits")
        )
    _require(
        features.ndim == 2
        and features.shape[1] == 4
        and targets.shape == groups.shape == splits.shape == (features.shape[0],),
        "Training feature/target/group/split dimensions do not match",
    )
    _require(
        features.shape[0] > 0 and np.isfinite(features).all() and np.isfinite(targets).all(),
        "Training arrays must be finite and nonempty",
    )
    evidence = {
        item["case"]: item
        for item in manifest.get("evidence", [])
        if item["split"] in {"train", "validation", "test"}
    }
    _require(set(groups) == set(evidence), "Training cases lack upstream source evidence")
    for name in set(groups):
        rows = groups == name
        entry = evidence[name]
        _require(
            set(splits[rows]) == {entry["split"]},
            f"Case {name} has mixed or changed training splits",
        )
        _require(
            entry.get("completed_stages") == 7
            and all(
                re.fullmatch(r"[0-9a-f]{64}", entry.get("artifacts", {}).get(key, ""))
                for key in ("dataset/sample.h5", "solver/analysis/gauge.odb")
            ),
            "Completed solver source evidence is missing",
        )
        _require(
            np.all(np.diff(features[rows, 0]) > 0)
            and np.all((features[rows, 0] >= 0) & (features[rows, 0] <= 0.008)),
            "Training strain grid is outside the declared window",
        )
    training_columns = {name: features[:, i] for i, name in enumerate(_FEATURES)}
    training_columns.update(nominal_axial_stress=targets, case_id=groups, split=splits)
    training_units = dict(zip(_FEATURES, config["feature_units"], strict=True))
    training_units.update(nominal_axial_stress="MPa", case_id=None, split=None)
    training_roles = dict.fromkeys(_FEATURES, "input_feature")
    training_roles.update(
        nominal_axial_stress="simulated_target", case_id="group_identifier", split="partition"
    )
    training = _scientific(
        training_columns,
        training_units,
        training_roles,
        {**shared, "source_kind": "simulated", "upstream_evidence": list(evidence.values())},
    )
    return {"experiments": experimental, "training": training}, manifest


def integrate_tensile_bundle(bundle: Path, output: Path):
    """Validate the supplied case data and atomically create two HDF5 2.0 datasets.

    Bundled values and hashes are checked here. Recorded upstream solver evidence
    is retained as provenance; this function does not rerun Abaqus or a model.
    """
    bundle, output = Path(bundle), Path(output)
    staging = None
    provenance = {"operation": "integrate_tensile_bundle", "input_filename": bundle.name}
    try:
        if output.exists():
            raise OutputExistsError("Use a new integration output directory")
        if output.resolve().is_relative_to(bundle.resolve()):
            raise DataValidationError("Integration output must be outside the source bundle")
        values, source = _load_bundle(bundle)
        validations = {
            name: validate_value(value, schema) for name, (value, schema) in values.items()
        }
        _require(
            all(v.valid for v in validations.values()), "Tensile values failed schema validation"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".tensile-", dir=output.parent))
        for name, (value, schema) in values.items():
            write_hdf5_v2(value, staging / f"{name}.h5", schema)
            (staging / f"{name}.schema.json").write_text(
                json.dumps(schema.schema.to_dict(), indent=2), encoding="utf-8"
            )
        report = {
            "source_doi": source["source_doi"],
            "license": source["license"],
            "validation": {name: v.to_dict() for name, v in validations.items()},
            "outputs": {f"{name}.h5": path_sha256(staging / f"{name}.h5") for name in values},
            "input_hashes": source["files"],
            "evidence_scope": source["evidence_scope"],
        }
        (staging / "manifest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        os.rename(staging, output)
        return ServiceResult(
            "integrate_tensile_bundle",
            "succeeded",
            report,
            artifact=output.name,
            provenance=provenance,
        )
    except Exception as exc:
        return _failure("integrate_tensile_bundle", exc, provenance=provenance)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
