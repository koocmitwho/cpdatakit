import json

from test_web_workflows import _csrf, _request, _seed_curve, _wait_for_job

from cpdatakit.cli import main
from cpdatakit.web import create_app


def test_cli_generates_editable_draft_and_runs_mapping_preview(tmp_path, capsys):
    source = tmp_path / "curve.csv"
    source.write_text("step,strain,stress\n0,0.0,0.0\n")
    assert main(["schema", "draft", str(source)]) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["schema"]["fields"][1]["name"] == "strain"
    mapping = tmp_path / "map.json"
    mapping.write_text('{"mappings": []}')
    assert (
        main(["mapping", "preview", str(source), "--schema", "curve", "--mapping", str(mapping)])
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["validation"]["valid"]


def test_workbench_draft_and_mapping_preview_use_existing_project_boundaries(tmp_path):
    app = create_app(tmp_path / "workspace")
    try:
        home, project = _seed_curve(app, tmp_path)
        dataset = app.state.catalog.list_datasets(project)[0].id
        headers = {"X-CSRF-Token": _csrf(home)}
        draft = _request(app, "GET", f"/api/projects/{project}/datasets/{dataset}/schema-draft")
        assert draft.status_code == 200
        assert draft.json()["value"]["ready"] is False
        response = _request(
            app,
            "POST",
            f"/api/projects/{project}/mapping-preview",
            cookies=home.cookies,
            headers=headers,
            data={"dataset_id": dataset, "schema": "curve", "mapping_json": '{"mappings": []}'},
        )
        assert response.status_code == 200
        assert response.json()["value"]["validation"]["valid"]
        queued = _request(
            app,
            "POST",
            f"/api/projects/{project}/convert",
            cookies=home.cookies,
            headers=headers,
            data={
                "dataset_id": dataset,
                "schema": "curve",
                "output": "mapped.h5",
                "mapping_json": json.dumps(
                    {
                        "mappings": [
                            {
                                "source": "stress",
                                "target": "stress",
                                "input_unit": "MPa",
                                "output_unit": "MPa",
                            }
                        ]
                    }
                ),
            },
        )
        assert _wait_for_job(app, queued.json()["job_id"])["status"] == "succeeded"
    finally:
        app.state.jobs.shutdown()
