"""Exercise the shipped workbench in Chromium against an independent wheel install.

Run this script with a Playwright tools interpreter and pass the clean runtime
interpreter as --python. Screenshots, trace, console and server logs survive failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from threading import Event

from playwright.sync_api import expect, sync_playwright

_SERVER = """
import sys, threading
import uvicorn
from cpdatakit.web import create_app
server = uvicorn.Server(uvicorn.Config(create_app(sys.argv[1]), host='127.0.0.1',
                                      port=int(sys.argv[2]), log_level='info'))
thread = threading.Thread(target=server.run)
thread.start()
try:
    sys.stdin.readline()
finally:
    server.should_exit = True
    thread.join(20)
    if thread.is_alive():
        raise RuntimeError('Server did not shut down')
"""

_PREPARE = """
import importlib.util, json, pathlib, sys
import cpdatakit
from cpdatakit.application import discover_capabilities
from cpdatakit.schema import load_schema, schema_to_dict
root = pathlib.Path(sys.argv[1])
package = pathlib.Path(cpdatakit.__file__).resolve()
assert package.is_relative_to(pathlib.Path(sys.prefix).resolve()), package
assert importlib.util.find_spec('httpx') is None, 'Runtime environment contains dev-only httpx'
ui = next(i for i in discover_capabilities().value.items if i.name == 'local-ui')
assert ui.available, ui.reason
schema = schema_to_dict(load_schema('curve'))
(root / 'schema.json').write_text(json.dumps(schema), encoding='utf-8')
(root / 'curve.csv').write_text(
    'step,strain,stress\\n0,0.0,0.0\\n1,0.01,123.5\\n2,0.02,234.5\\n', encoding='utf-8')
print(json.dumps({'version': cpdatakit.__version__, 'package': str(package),
                  'runtime_without_httpx': True, 'local_ui_available': ui.available}))
"""

_READ_BACK = """
import json, sys
from pathlib import Path
from cpdatakit import load_hdf5
from cpdatakit.application import DatasetRequest, validate_and_summarize
root = Path(sys.argv[1])
data = load_hdf5(root / 'converted.h5')
assert data.data['step'].tolist() == [0, 1, 2]
assert data.data['strain'].tolist() == [0.0, 0.01, 0.02]
assert data.data['stress'].tolist() == [0.0, 123.5, 234.5]
result = validate_and_summarize(DatasetRequest(data=root / 'converted.h5', schema='curve'))
assert result.ok and result.value.validation.valid, result.to_dict()
report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
assert report['validation']['valid'] is True
assert report['record_count'] == 3
print(json.dumps({'rows': 3, 'stress': data.data['stress'].tolist(), 'report_valid': True}))
"""


def run_child(python: Path, code: str, root: Path, environment: dict) -> dict:
    completed = subprocess.run(
        [str(python), "-X", "utf8", "-c", code, str(root)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if completed.returncode:
        raise RuntimeError(completed.stdout + completed.stderr)
    return json.loads(completed.stdout)


def wait_for_server(server: subprocess.Popen, base: str) -> None:
    deadline = time.monotonic() + 30
    while server.poll() is None:
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=1) as response:
                assert json.load(response) == {"status": "ok"}
                return
        except (urllib.error.URLError, TimeoutError):
            if time.monotonic() >= deadline:
                break
            Event().wait(0.05)
    raise RuntimeError("Installed Uvicorn server did not become healthy; inspect server.log")


def browser_workflow(base: str, root: Path, evidence: dict) -> None:
    console = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(accept_downloads=True)
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        page.set_default_timeout(30_000)
        page.on("console", lambda message: console.append([message.type, message.text]))
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            page.goto(base)
            page.get_by_label("项目名称", exact=True).fill("Installed wheel browser acceptance")
            page.get_by_role("button", name="创建项目", exact=True).click()
            page.wait_for_url("**/projects/*")
            # Hidden pagination must remain hidden after author CSS is applied.
            expect(page.locator('[data-load-more="datasets"]')).to_be_hidden()
            page.get_by_text("添加自定义规则", exact=True).click()
            page.get_by_label("规则 JSON 文件", exact=True).set_input_files(root / "schema.json")
            page.get_by_role("button", name="添加数据规则", exact=True).click()
            expect(page.locator("#result-title")).to_have_text("已添加数据规则")
            expect(page.locator("#schema")).to_have_value(re.compile(r"schema:\d+"))
            page.get_by_label("上传数据文件", exact=True).set_input_files(root / "curve.csv")
            page.get_by_role("button", name="上传并检查", exact=True).click()
            expect(page.locator("#result-title")).to_have_text("上传完成")
            expect(page.locator("#result-content")).to_contain_text("记录 3")
            expect(page.locator('[data-load-more="datasets"]')).to_be_hidden()
            page.get_by_role("button", name="校验数据", exact=True).click()
            expect(page.locator("#result-title")).to_have_text("校验结果")
            expect(page.locator("#result-content .is-success")).to_have_text("校验通过")

            def submit_job(operation: str, button: str) -> str:
                with page.expect_response(
                    lambda response: (
                        response.request.method == "POST" and response.url.endswith(f"/{operation}")
                    )
                ) as submitted:
                    page.get_by_role("button", name=button, exact=True).click()
                response = submitted.value
                assert response.status == 202, response.text()
                job_id = response.json()["job_id"]
                expect(page.locator(f'[data-job-id="{job_id}"]')).to_have_attribute(
                    "data-job-status", "succeeded"
                )
                return job_id

            convert_id = submit_job("convert", "转换并保存")
            converted_row = page.locator("#artifacts .artifact-row").filter(has_text="converted.h5")
            with page.expect_download() as converted:
                converted_row.get_by_role("link", name="下载", exact=True).click()
            converted.value.save_as(root / "converted.h5")
            page.get_by_label("报告格式", exact=True).select_option("json")
            report_id = submit_job("report", "生成报告")
            report_row = page.locator("#artifacts .artifact-row").filter(has_text="report.json")
            with page.expect_download() as report:
                report_row.get_by_role("link", name="下载", exact=True).click()
            report.value.save_as(root / "report.json")

            # A path outside this synthetic project must surface a visible error.
            page.get_by_label("数据保存路径", exact=True).fill("../outside.h5")
            page.get_by_role("button", name="转换并保存", exact=True).click()
            expect(page.locator("#result-title")).to_have_text("操作失败")
            expect(page.locator("#result-content .error")).to_be_visible()
            assert not (root / "workspace" / "projects" / "outside.h5").exists()
            assert errors == [], errors
            evidence.update(
                project_url=page.url,
                jobs={"convert": convert_id, "report": report_id},
                visible_error=page.locator("#result-content .error").inner_text(),
                browser_version=browser.version,
                browser_steps=["schema", "upload", "validate", "convert", "report", "download"],
            )
        finally:
            page.screenshot(path=str(root / "browser.png"), full_page=True)
            (root / "page.html").write_text(page.content(), encoding="utf-8")
            (root / "console.json").write_text(
                json.dumps({"console": console, "page_errors": errors}, indent=2), encoding="utf-8"
            )
            context.tracing.stop(path=str(root / "trace.zip"))
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", type=Path, required=True, help="Clean wheel runtime interpreter"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    # POSIX venv launchers are symlinks; resolving them selects the base Python.
    python = args.python.absolute()
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    evidence = run_child(python, _PREPARE, root, environment)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with (root / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [str(python), "-X", "utf8", "-c", _SERVER, str(root / "workspace"), str(port)],
            cwd=root,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            wait_for_server(server, base)
            browser_workflow(base, root, evidence)
            evidence["read_back"] = run_child(python, _READ_BACK, root, environment)
        finally:
            try:
                server.communicate("stop\n", timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
                raise RuntimeError("Installed server did not stop") from None
    assert server.returncode == 0, f"Server exit status: {server.returncode}"
    evidence["server_exit_code"] = server.returncode
    (root / "evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
