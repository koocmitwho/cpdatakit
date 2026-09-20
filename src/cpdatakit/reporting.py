"""Build validation reports for terminal and offline use."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from html import escape
from pathlib import Path
from typing import Any

from .adapters import DEFAULT_ADAPTER_REGISTRY, DamaskDADF5Adapter
from .exceptions import CPDataKitError, OutputExistsError
from .inspection import inspect_dataset, sanitize_for_output
from .io import load_dataset
from .schema import ProfileSchema, load_schema, schema_to_dict
from .statistics import summarize_dataset
from .validation import validate_dataset

SCOPE_NOTE = (
    "Validation reports declared format constraints; physical or scientific interpretation "
    "remains part of the domain workflow."
)


def _has_non_finite(value: object) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return isinstance(value, float) and not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(_has_non_finite(item) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_has_non_finite(item) for item in value)
    return False


def _safe_json_value(value: object) -> object:
    return sanitize_for_output(value)


def render_report_json(report: Mapping[str, Any]) -> str:
    """Render a report mapping as canonical JSON."""

    if _has_non_finite(report):
        raise ValueError("Report contains non-finite numeric values")
    return json.dumps(_safe_json_value(report), indent=2, sort_keys=True, allow_nan=False) + "\n"


def _cell(value: object) -> str:
    safe = _safe_json_value(value)
    if isinstance(safe, (dict, list)):
        text = json.dumps(safe, ensure_ascii=False, sort_keys=True, allow_nan=False)
    elif safe is None:
        text = "not available"
    else:
        text = str(safe)
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _shape(value: object) -> str:
    safe = _safe_json_value(value)
    return json.dumps(safe, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _issue_lines(issues: object) -> list[str]:
    if not isinstance(issues, list) or not issues:
        return ["(none)"]
    lines = []
    for issue in issues:
        if isinstance(issue, Mapping):
            lines.append(
                "| "
                + " | ".join(
                    _cell(issue.get(key))
                    for key in ("code", "field", "message", "affected_records", "suggestion")
                )
                + " |"
            )
    return lines or ["(none)"]


def _mapping_block(value: object) -> str:
    safe = _safe_json_value(value)
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def render_report_markdown(report: Mapping[str, Any]) -> str:
    """Render a report mapping as stable Markdown."""

    file_info = report.get("file", {})
    schema = report.get("schema", {})
    validation = report.get("validation", {})
    fields = report.get("fields", [])
    file_values = file_info if isinstance(file_info, Mapping) else {}
    validation_value = (
        _cell(validation.get("valid")) if isinstance(validation, Mapping) else "not available"
    )
    lines = [
        "# CPDataKit Validation Report",
        "",
        "## File and Format",
        "",
        f"- Filename: {_cell(file_values.get('filename'))}",
        f"- File type: {_cell(file_values.get('file_type'))}",
        f"- Format: {_cell(file_values.get('format'))}",
        f"- Format version: {_cell(file_values.get('format_version'))}",
        f"- Records: {_cell(report.get('record_count'))}",
        "",
        "## Schema",
        "",
    ]
    if isinstance(schema, Mapping):
        lines.extend(
            [
                f"- Profile: {_cell(schema.get('profile'))}",
                f"- Schema version: {_cell(schema.get('schema_version'))}",
                "",
            ]
        )
    else:
        lines.extend(["- not available", ""])
    lines.extend(
        [
            "## Fields",
            "",
            "| Field | Dtype | Shape | Unit | Missing | Description |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    if isinstance(fields, list) and fields:
        for field in fields:
            if not isinstance(field, Mapping):
                continue
            lines.append(
                "| "
                + " | ".join(
                    (
                        _cell(field.get("name")),
                        _cell(field.get("dtype")),
                        _shape(field.get("shape", [])),
                        _cell(field.get("unit")),
                        _cell(field.get("missing_count")),
                        _cell(field.get("description")),
                    )
                )
                + " |"
            )
    else:
        lines.append("| (none) | | | | | |")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- Valid: {validation_value}",
            "",
            "### Errors",
            "",
            "| Code | Field | Message | Affected records | Suggestion |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    lines.extend(_issue_lines(validation.get("errors") if isinstance(validation, Mapping) else []))
    lines.extend(
        [
            "",
            "### Warnings",
            "",
            "| Code | Field | Message | Affected records | Suggestion |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    lines.extend(
        _issue_lines(validation.get("warnings") if isinstance(validation, Mapping) else [])
    )
    lines.extend(
        [
            "",
            "## Descriptive Statistics",
            "",
            "~~~json",
            _mapping_block(report.get("statistics", {})),
            "~~~",
            "",
            "## Provenance",
            "",
            "~~~json",
            _mapping_block(report.get("provenance", {})),
            "~~~",
            "",
            "## Adapter",
            "",
            "~~~json",
            _mapping_block(report.get("adapter", {})),
            "~~~",
            "",
            "## HDF5 Storage",
            "",
            "~~~json",
            _mapping_block(report.get("hdf5", {})),
            "~~~",
            "",
            "## Scope",
            "",
            _cell(report.get("scope_note", SCOPE_NOTE)),
            "",
        ]
    )
    return "\n".join(lines)


def _html_value(value: object) -> str:
    safe = _safe_json_value(value)
    if isinstance(safe, (dict, list)):
        text = json.dumps(safe, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    elif safe is None:
        text = "未提供"
    elif isinstance(safe, str):
        text = {
            "not available": "未提供",
            "not provided": "未提供",
            "not applicable": "不适用",
        }.get(safe, safe)
    else:
        text = str(safe)
    return escape(text, quote=True)


def _html_definition_list(values: Mapping[str, Any]) -> str:
    parts = ["<dl>"]
    for key, value in values.items():
        parts.append(f"<div><dt>{_html_value(key)}</dt><dd>{_html_value(value)}</dd></div>")
    parts.append("</dl>")
    return "\n".join(parts)


def _html_table(caption: str, headers: list[str], rows: list[list[object]]) -> str:
    if not rows:
        return '<p class="muted">未提供可展示的条目。</p>'
    parts = [
        '<div class="table-scroll"><table>',
        f"<caption>{_html_value(caption)}</caption>",
        "<thead><tr>"
        + "".join(f'<th scope="col">{_html_value(h)}</th>' for h in headers)
        + "</tr></thead>",
        "<tbody>",
    ]
    parts.extend(
        "<tr>" + "".join(f"<td>{_html_value(value)}</td>" for value in row) + "</tr>"
        for row in rows
    )
    parts.append("</tbody></table></div>")
    return "\n".join(parts)


def _html_issue_table(issues: object, caption: str) -> str:
    if not isinstance(issues, list):
        return '<p class="muted">未提供问题明细。</p>'
    if not issues:
        return '<p class="muted">未发现此类问题。</p>'
    return _html_table(
        caption,
        ["代码", "字段", "说明", "受影响记录", "处理建议"],
        [
            [
                issue.get(key)
                for key in ("code", "field", "message", "affected_records", "suggestion")
            ]
            for issue in issues
            if isinstance(issue, Mapping)
        ],
    )


def _report_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _html_statistics(report: Mapping[str, Any]) -> str:
    """Arrange stored statistics without recalculating or narrowing their values."""
    statistics = _report_mapping(report.get("statistics"))
    observed_fields = (
        {
            item["name"]: item
            for item in report.get("fields", [])
            if isinstance(item, Mapping) and isinstance(item.get("name"), str)
        }
        if isinstance(report.get("fields"), list)
        else {}
    )
    numeric = _report_mapping(statistics.get("numeric_fields"))
    arrays = _report_mapping(statistics.get("fields"))
    metrics = ("min", "max", "mean", "std")
    if arrays:
        entries = [
            (name, info)
            for name, info in arrays.items()
            if isinstance(info, Mapping) and any(key in info for key in metrics)
        ]
    else:
        missing = _report_mapping(statistics.get("missing_values"))
        entries = [
            (
                name,
                {
                    **_report_mapping(info),
                    "unit": observed_fields.get(name, {}).get("unit"),
                    "count": statistics.get("record_count", report.get("record_count")),
                    "missing_count": missing.get(name),
                },
            )
            for name, info in numeric.items()
        ]
    with_std = any("std" in info for _, info in entries)
    columns = ["min", "max", "mean"] + (["std"] if with_std else [])
    rows = [
        [
            name,
            info.get("unit") if info.get("unit") is not None else "未声明",
            info.get("count"),
            info.get("missing_count"),
            *(info.get(key) for key in columns),
        ]
        for name, info in entries
    ]
    return _html_table(
        "数值统计",
        ["字段", "单位", "数量", "缺失", "最小值", "最大值", "均值"]
        + (["标准差"] if with_std else []),
        rows,
    )


def _html_provenance(value: object) -> str:
    provenance = _report_mapping(value)
    labels = {
        "source_doi": "来源 DOI",
        "license": "许可",
        "source_authors": "作者",
        "source_description": "来源说明",
        "input_filename": "来源文件",
        "input_sha256": "来源 SHA-256",
        "converted_at_utc": "转换时间（UTC）",  # noqa: RUF001 -- Chinese display text.
        "cpdatakit_version": "CPDataKit 版本",
        "python_version": "Python 版本",
        "upstream_repository": "上游项目",
        "upstream_release": "上游版本",
        "upstream_commit": "上游提交",
        "strain_definition": "应变定义",
        "stress_definition": "应力定义",
        "strain_window": "应变范围",
        "physical_scope": "适用范围",
    }
    summary = {label: provenance[key] for key, label in labels.items() if key in provenance}
    if isinstance(summary.get("作者"), list):
        summary["作者"] = "、".join(str(author) for author in summary["作者"])
    parts = (
        [_html_definition_list(summary)]
        if summary
        else ['<p class="muted">未提供可摘要的来源信息；其他已有内容见完整报告数据。</p>']  # noqa: RUF001
    )
    processing = _report_mapping(provenance.get("processing"))
    if processing:
        parts.append(
            _html_table(
                "处理说明",
                ["步骤", "已有处理记录"],
                [[name, description] for name, description in processing.items()],
            )
        )
    hashes = _report_mapping(provenance.get("input_hashes"))
    if hashes:
        parts.append(
            _html_table(
                "输入文件校验信息",
                ["文件", "SHA-256"],
                [[name, digest] for name, digest in hashes.items()],
            )
        )
    sources = _report_mapping(provenance.get("raw_sources"))
    if sources:
        parts.append(
            _html_table(
                "原始来源记录",
                ["标识", "文件", "SHA-256"],
                [
                    [name, source.get("member"), source.get("sha256")]
                    for name, source in sources.items()
                    if isinstance(source, Mapping)
                ],
            )
        )
    return "\n".join(parts)


def render_report_html(report: Mapping[str, Any]) -> str:
    """Render an offline HTML overview while retaining the complete report evidence."""
    file_values = _report_mapping(report.get("file"))
    schema = _report_mapping(report.get("schema"))
    validation = _report_mapping(report.get("validation"))
    statistics = _report_mapping(report.get("statistics"))
    fields = report.get("fields")
    field_rows = (
        [
            [
                item.get("name"),
                item.get("dtype"),
                item.get("shape"),
                item.get("unit") if item.get("unit") is not None else "未声明",
                item.get("missing_count"),
                item.get("description"),
            ]
            for item in fields
            if isinstance(item, Mapping)
        ]
        if isinstance(fields, list)
        else []
    )
    valid = validation.get("valid")
    state = "通过" if valid is True else "未通过" if valid is False else "未提供"
    tone = "passed" if valid is True else "failed" if valid is False else "unknown"
    errors, warnings = validation.get("errors"), validation.get("warnings")
    summary = {
        "验证状态": state,
        "记录数": report.get("record_count", statistics.get("record_count")),
        "字段数": len(field_rows) if isinstance(fields, list) else statistics.get("field_count"),
        "错误": len(errors) if isinstance(errors, list) else None,
        "警告": len(warnings) if isinstance(warnings, list) else None,
    }
    html = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>CPDataKit 数据检查报告</title>",
        "<style>",
        "*{box-sizing:border-box}body{margin:0;background:#f3f6f8;color:#20313c;"
        "font:15px/1.65 system-ui,-apple-system,'Segoe UI',sans-serif}",
        "main{max-width:1100px;margin:2.5rem auto;padding:0 1.25rem}"
        "header{margin-bottom:1.6rem}h1{font-size:2rem;margin:.3rem 0}"
        "h2{font-size:1.2rem;margin:0 0 1rem}h3{font-size:1rem}",
        "section{background:#fff;border:1px solid #dce3e8;border-radius:12px;"
        "padding:1.4rem;margin:1rem 0}p{margin:.6rem 0}.muted{color:#586975}"
        ".eyebrow{font-weight:700;letter-spacing:.08em;color:#245c64}",
        ".overview{border-top:4px solid #687780}.overview.passed{border-top-color:#287a56}"
        ".overview.failed{border-top-color:#aa3440}.overview dl{display:grid;"
        "grid-template-columns:repeat(5,minmax(0,1fr));gap:1rem}"
        ".overview dl>div{display:block}.overview dd{font-size:1.6rem;font-weight:700}",
        "dl{margin:0}dl>div{display:grid;grid-template-columns:minmax(8rem,12rem) minmax(0,1fr);"
        "gap:.5rem;margin:.6rem 0}dt{font-weight:600}dd{margin:0;overflow-wrap:anywhere}",
        ".table-scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;"
        "font-size:.9rem;margin:.5rem 0 1rem}caption{text-align:left;font-weight:650;"
        "padding:.5rem 0}th,td{padding:.7rem;text-align:left;vertical-align:top;"
        "border-bottom:1px solid #dce3e8;overflow-wrap:anywhere;font-variant-numeric:tabular-nums}"
        "th{background:#f0f5f6;white-space:nowrap}td{min-width:5rem}",
        "summary{cursor:pointer;color:#245c64;font-weight:600}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f8;"
        "padding:1rem;font:12px/1.6 ui-monospace,monospace}"
        ".scope{border-left:4px solid #527b88;padding-left:1rem}"
        ":focus-visible{outline:3px solid #5799a4;outline-offset:3px}",
        "@media(max-width:640px){main{margin:1rem auto;padding:0 .75rem}"
        "section{padding:1rem}.overview dl{grid-template-columns:repeat(2,minmax(0,1fr))}"
        "dl>div{grid-template-columns:1fr;gap:.1rem}h1{font-size:1.6rem}}",
        "@media print{body{background:#fff;font-size:10pt}main{max-width:none;margin:0;padding:0}"
        "section{border:0;border-top:1px solid #bbb;border-radius:0;padding:.8rem 0}"
        "h1,h2,h3,caption{break-after:avoid}.table-scroll{overflow:visible}"
        "tr{break-inside:avoid}thead{display:table-header-group}table{font-size:8pt}"
        "th,td{padding:.35rem}td{min-width:0}.overview dd{font-size:1.2rem}}",
        "</style>",
        "</head>",
        "<body><main>",
        '<header><p class="eyebrow">CPDataKit · 本地数据检查</p><h1>数据检查报告</h1>',
        f"<p>检查对象：<strong>{_html_value(file_values.get('filename'))}</strong></p>",  # noqa: RUF001
        '<p class="muted">先查看检查结论，再核对字段、数值统计和来源记录。</p></header>',  # noqa: RUF001
        f'<section class="overview {tone}" aria-label="检查概览">',
        _html_definition_list(summary),
        '<p class="muted">通过表示符合已声明的数据规则；科学与物理解释需结合具体研究。</p>',  # noqa: RUF001
        "</section><section><h2>数据与规则</h2>",
        _html_definition_list(
            {
                "文件": file_values.get("filename"),
                "格式": file_values.get("format"),
                "格式版本": file_values.get("format_version"),
                "规则名称": schema.get("profile"),
                "规则版本": schema.get("schema_version"),
            }
        ),
        '<p class="muted">数据规则（schema）声明字段、类型、单位和形状。'  # noqa: RUF001
        "记录数沿用文件检查结果，多维数据还应结合字段形状阅读。</p>",  # noqa: RUF001
        _html_table("字段信息", ["字段", "类型", "形状", "单位", "缺失", "说明"], field_rows),
        '<p class="muted">“未声明”表示报告没有提供可确定的单位，不等于无量纲；'  # noqa: RUF001
        "无量纲单位保留原声明，如 1 或 dimensionless。</p>",  # noqa: RUF001
        "</section><section><h2>检查发现</h2><h3>错误</h3>",
        _html_issue_table(errors, "错误明细"),
        "<h3>警告</h3>",
        _html_issue_table(warnings, "警告明细"),
        "</section><section><h2>数值统计</h2>",
        '<p class="muted">数量按表格记录或数组元素计，包含缺失项，不代表独立样品数。'  # noqa: RUF001
        "极值、均值和已有标准差沿用有限数值的统计结果；未提供的统计不补零。</p>",  # noqa: RUF001
        _html_statistics(report),
        "</section><section><h2>数据来源与处理</h2>",
        _html_provenance(report.get("provenance")),
        '<p class="muted">来源与哈希来自已有记录；本报告没有重新执行上游实验或求解器。</p>',  # noqa: RUF001
        "</section><section><h2>适用范围</h2>",
        '<p class="scope">本报告检查所选规则中的结构、字段、单位和数据质量要求。'
        "验证通过不代表物理结论或模型预测已经得到验证。</p>",
        f'<p class="muted">{_html_value(report.get("scope_note", SCOPE_NOTE))}</p>',
        "</section><section><details><summary>完整报告数据（JSON）</summary>",  # noqa: RUF001
        '<p class="muted">保留已有规则、统计、来源、适配器和存储信息，便于进一步核对。</p>',  # noqa: RUF001
        f"<pre>{_html_value(report)}</pre>",
        "</details></section></main></body>",
        "</html>",
        "",
    ]
    return "\n".join(html)


def _load_for_report(path: Path, inspection: Mapping[str, Any]):
    adapter = inspection.get("adapter", {})
    if isinstance(adapter, Mapping) and adapter.get("registry_name") == "damask-dadf5":
        implementation = DEFAULT_ADAPTER_REGISTRY.get("damask-dadf5")
        return implementation().load(path)
    if inspection.get("file", {}).get("format") == "DAMASK DADF5":
        return DamaskDADF5Adapter().load(path)
    return load_dataset(path)


def build_report(
    path: str | Path,
    schema: str | Path | ProfileSchema | Mapping[str, Any],
) -> dict[str, Any]:
    """Build a complete validation report for one input file."""

    input_path = Path(path)
    contract = load_schema(schema)
    inspection = inspect_dataset(input_path, schema=contract)
    dataset = _load_for_report(input_path, inspection)
    validation = validate_dataset(dataset, contract)
    statistics = summarize_dataset(dataset, contract, validation=validation)
    report = {
        "file": inspection.get("file", {}),
        "schema": schema_to_dict(contract),
        "record_count": inspection.get("record_count", len(dataset.data)),
        "fields": inspection.get("fields", []),
        "validation": validation.to_dict(),
        "statistics": statistics,
        "provenance": inspection.get("provenance", {}),
        "adapter": inspection.get("adapter", {}),
        "hdf5": inspection.get("hdf5", {}),
        "scope_note": SCOPE_NOTE,
    }
    return dict(_safe_json_value(report))


def write_report(
    report: Mapping[str, Any],
    output: str | Path,
    *,
    format: str = "html",
    force: bool = False,
) -> Path:
    """Write a report artifact and preserve existing files by default."""

    renderers = {
        "html": render_report_html,
        "markdown": render_report_markdown,
        "json": render_report_json,
    }
    try:
        renderer = renderers[format]
    except KeyError as exc:
        raise CPDataKitError(f"Unsupported report format: {format!r}") from exc
    rendered = renderer(report)
    target = Path(output)
    if target.exists() and not force:
        raise OutputExistsError(f"Output already exists: {target}; pass --force to replace it")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise CPDataKitError(f"Cannot write report output {target}: {exc}") from exc
    return target
