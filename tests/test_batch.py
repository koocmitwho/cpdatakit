import json

import pytest

from cpdatakit import application as api
from cpdatakit.cli import main


def config_files(tmp_path):
    (tmp_path / "good.csv").write_text("step,strain,stress\n0,0.0,0.0\n1,0.01,20.0\n")
    (tmp_path / "bad.csv").write_text("step,strain\n0,0.0\n")
    path = tmp_path / "batch.json"
    path.write_text(
        json.dumps(
            {
                "batch_version": 1,
                "defaults": {"schema": "curve"},
                "items": [
                    {"input": "good.csv", "output": "results/good.h5"},
                    {"input": "bad.csv", "output": "results/bad.h5"},
                ],
            }
        )
    )
    return path


def test_batch_records_partial_failure_then_retries_only_failed_inputs(tmp_path):
    assert hasattr(api, "run_batch")
    config = config_files(tmp_path)
    manifest = tmp_path / "run.json"
    result = api.run_batch(config, manifest)
    assert not result.ok
    report = json.loads(manifest.read_text(encoding="utf-8"))
    assert report["counts"] == {"succeeded": 1, "failed": 1, "skipped": 0}
    good = tmp_path / "results/good.h5"
    unchanged = good.stat().st_mtime_ns
    assert report["items"][0]["output_sha256"]
    assert report["items"][1]["result"]["value"]["validation"]["errors"]
    (tmp_path / "bad.csv").write_text("step,strain,stress\n0,0.0,0.0\n")
    retry = api.run_batch(config, manifest, retry=True)
    assert retry.ok, retry.to_dict()
    report = json.loads(manifest.read_text(encoding="utf-8"))
    assert report["counts"] == {"succeeded": 1, "failed": 0, "skipped": 1}
    assert good.stat().st_mtime_ns == unchanged
    assert all(item["parameters"]["schema_sha256"] for item in report["items"])


@pytest.mark.parametrize(
    "target", ["results/good.h5", "good.csv", "batch.json", "run.json", "results"]
)
def test_batch_preflights_duplicate_outputs_and_protects_every_input(tmp_path, target):
    assert hasattr(api, "run_batch")
    config = config_files(tmp_path)
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["items"][1]["output"] = target
    config.write_text(json.dumps(payload))
    original = (tmp_path / "good.csv").read_bytes()
    result = api.run_batch(config, tmp_path / "run.json")
    assert not result.ok
    assert not (tmp_path / "results/good.h5").exists()
    assert (tmp_path / "good.csv").read_bytes() == original


def test_retry_detects_modified_successful_outputs(tmp_path):
    assert hasattr(api, "run_batch")
    config = config_files(tmp_path)
    manifest = tmp_path / "run.json"
    api.run_batch(config, manifest)
    output = tmp_path / "results/good.h5"
    output.write_bytes(b"user modified result")
    result = api.run_batch(config, manifest, retry=True)
    assert not result.ok
    assert output.read_bytes() == b"user modified result"
    report = json.loads(manifest.read_text(encoding="utf-8"))
    assert report["items"][0]["status"] == "failed"
    assert report["items"][0]["result"]["error"]["code"] == "output_changed"


def test_batch_cli_returns_partial_failure_and_requires_explicit_retry(tmp_path, capsys):
    config = config_files(tmp_path)
    manifest = tmp_path / "run.json"
    assert main(["batch", str(config), "--manifest", str(manifest)]) == 1
    assert json.loads(capsys.readouterr().out)["value"]["counts"]["failed"] == 1
    before = manifest.read_bytes()
    assert main(["batch", str(config), "--manifest", str(manifest)]) == 2
    assert manifest.read_bytes() == before


def test_interrupted_retry_keeps_prior_success_records_for_unprocessed_files(tmp_path, monkeypatch):
    import cpdatakit.application.batch as batch

    config = config_files(tmp_path)
    (tmp_path / "bad.csv").write_text("step,strain,stress\n0,0.0,0.0\n")
    manifest = tmp_path / "run.json"
    assert api.run_batch(config, manifest).ok
    before = json.loads(manifest.read_text(encoding="utf-8"))
    original = batch._parameters

    def interrupted(request):
        raise KeyboardInterrupt

    monkeypatch.setattr(batch, "_parameters", interrupted)
    with pytest.raises(KeyboardInterrupt):
        api.run_batch(config, manifest, retry=True)
    interrupted_report = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(interrupted_report["items"]) == 2
    assert interrupted_report["items"][1]["output_sha256"] == before["items"][1]["output_sha256"]
    monkeypatch.setattr(batch, "_parameters", original)
    assert api.run_batch(config, manifest, retry=True).ok


def test_retry_remembers_success_while_an_input_is_temporarily_unavailable(tmp_path):
    config = config_files(tmp_path)
    (tmp_path / "bad.csv").write_text("step,strain,stress\n0,0.0,0.0\n")
    manifest = tmp_path / "run.json"
    assert api.run_batch(config, manifest).ok
    data = (tmp_path / "good.csv").read_bytes()
    (tmp_path / "good.csv").unlink()
    assert not api.run_batch(config, manifest, retry=True).ok
    (tmp_path / "good.csv").write_bytes(data)
    restored = api.run_batch(config, manifest, retry=True)
    assert restored.ok, restored.to_dict()
    assert restored.value["counts"]["skipped"] == 2
