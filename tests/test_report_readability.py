"""Readable HTML must preserve the evidence carried by existing reports."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import pytest

from cpdatakit.reporting import render_report_html, render_report_json, render_report_markdown


@dataclass
class _Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def text(self, *, visible_only=False):
        if visible_only and self.tag in {"details", "style", "script"}:
            return ""
        return "".join(
            child.text(visible_only=visible_only) if isinstance(child, _Element) else child
            for child in self.children
        ).strip()

    def find(self, tag):
        return [self] * (self.tag == tag) + [
            found
            for child in self.children
            if isinstance(child, _Element)
            for found in child.find(tag)
        ]


class _Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.root = _Element("document")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = _Element(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"meta", "br", "hr", "link", "img", "input"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        assert self.stack[-1].tag == tag, f"Unbalanced HTML: {tag}"
        self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def table(self, caption):
        matches = [
            table
            for table in self.root.find("table")
            if any(item.text() == caption for item in table.find("caption"))
        ]
        assert len(matches) == 1, f"Expected one readable {caption} table"
        table = matches[0]
        headers = [cell.text() for cell in table.find("thead")[0].find("th")]
        return [
            dict(zip(headers, [cell.text() for cell in row.find("td")], strict=True))
            for row in table.find("tbody")[0].find("tr")
        ]

    def definitions(self):
        return {
            term.text(): value.text()
            for term, value in zip(self.root.find("dt"), self.root.find("dd"), strict=True)
        }


@pytest.mark.parametrize(
    ("validation", "expected_status", "errors", "warnings"),
    [
        ({"valid": True, "errors": [], "warnings": []}, "通过", "0", "0"),
        (
            {"valid": False, "errors": [{"code": "unit_mismatch"}], "warnings": []},
            "未通过",
            "1",
            "0",
        ),
        ({}, "未提供", "未提供", "未提供"),
    ],
)
def test_overview_reports_actual_status_and_does_not_invent_missing_counts(
    validation, expected_status, errors, warnings
):
    report = {
        "file": {"filename": "experiments.h5"},
        "record_count": 483,
        "fields": [{"name": "strain"}, {"name": "stress"}],
        "validation": validation,
    }

    values = _Document(render_report_html(report)).definitions()

    assert values["验证状态"] == expected_status
    assert values["记录数"] == "483"
    assert values["字段数"] == "2"
    assert values["错误"] == errors
    assert values["警告"] == warnings


def test_tabular_statistics_table_preserves_large_integers_precision_and_unknowns():
    report = {
        "schema": {"fields": [{"name": "unknown", "unit": "K"}]},
        "fields": [{"name": "reading", "unit": "MPa"}, {"name": "unknown", "unit": None}],
        "statistics": {
            "record_count": 3,
            "numeric_fields": {
                "reading": {
                    "min": 18446744073709551614,
                    "max": 18446744073709551615,
                    "mean": 1.8446744073709552e19,
                    "std": 0.3333333333333333,
                },
                "unknown": "not available",
            },
            "missing_values": {"reading": 1, "unknown": "not available"},
        },
    }

    rows = _Document(render_report_html(report)).table("数值统计")

    assert rows == [
        {
            "字段": "reading",
            "单位": "MPa",
            "数量": "3",
            "缺失": "1",
            "最小值": "18446744073709551614",
            "最大值": "18446744073709551615",
            "均值": "1.8446744073709552e+19",
            "标准差": "0.3333333333333333",
        },
        {
            "字段": "unknown",
            "单位": "未声明",
            "数量": "3",
            "缺失": "未提供",
            "最小值": "未提供",
            "最大值": "未提供",
            "均值": "未提供",
            "标准差": "未提供",
        },
    ]


def test_scientific_statistics_use_array_counts_and_observed_units_without_fabricated_std():
    report = {
        "schema": {"variables": [{"name": "temperature", "unit": "K"}]},
        "fields": [{"name": "temperature", "unit": None}],
        "statistics": {
            "dimensions": {"time": 2, "x": 3},
            "fields": {
                "temperature": {
                    "count": 6,
                    "missing_count": 2,
                    "unit": None,
                    "min": 0.0,
                    "max": 2.5e-18,
                    "mean": 0.000000000000000001,
                },
                "strain": {"count": 2, "missing_count": 0, "unit": "1", "min": 0.0},
                "specimen": {"count": 2, "missing_count": 0, "dtype": "<U4", "unit": None},
            },
        },
    }

    document = _Document(render_report_html(report))
    rows = document.table("数值统计")

    assert rows == [
        {
            "字段": "temperature",
            "单位": "未声明",
            "数量": "6",
            "缺失": "2",
            "最小值": "0.0",
            "最大值": "2.5e-18",
            "均值": "1e-18",
        },
        {
            "字段": "strain",
            "单位": "1",
            "数量": "2",
            "缺失": "0",
            "最小值": "0.0",
            "最大值": "未提供",
            "均值": "未提供",
        },
    ]
    assert document.definitions()["记录数"] == "未提供"


def test_provenance_is_readable_without_expanding_json_and_keeps_full_evidence():
    report = {
        "provenance": {
            "source_doi": "10.5281/zenodo.10820299",
            "license": "CC-BY-4.0",
            "source_authors": ["Author One", "Author Two"],
            "processing": {"experiment": "interpolated to 161 points"},
            "input_hashes": {"experimental-curves.json": "a" * 64},
            "custom_evidence": {"original_count": 161},
        }
    }

    document = _Document(render_report_html(report))
    visible = document.root.text(visible_only=True)

    assert document.definitions()["来源 DOI"] == "10.5281/zenodo.10820299"
    assert document.definitions()["许可"] == "CC-BY-4.0"
    assert "Author One" in visible and "Author Two" in visible
    assert "interpolated to 161 points" in visible
    assert document.table("输入文件校验信息") == [
        {"文件": "experimental-curves.json", "SHA-256": "a" * 64}
    ]
    details = document.root.find("details")
    assert any('"original_count": 161' in item.text() for item in details)
    assert "custom_evidence" not in visible


def test_readable_sections_escape_untrusted_content_and_preserve_report_contracts():
    report = {
        "file": {"filename": "<img src=x onerror=alert(1)>.h5"},
        "fields": [{"name": "<field>", "unit": "<svg/onload=alert(2)>"}],
        "statistics": {"numeric_fields": {"<field>": {"min": 9007199254740993}}},
        "provenance": {
            "source_doi": "<script>alert(3)</script>",
            "input_hashes": {"<source>": "<hash>"},
            "api_token": "secret-report-token",
        },
    }
    before = deepcopy(report)
    original_json = render_report_json(report)
    original_markdown = render_report_markdown(report)

    html = render_report_html(report)
    document = _Document(html)

    assert document.table("数值统计")[0]["最小值"] == "9007199254740993"
    assert document.table("输入文件校验信息")[0] == {"文件": "<source>", "SHA-256": "<hash>"}
    assert not document.root.find("script")
    assert not document.root.find("img")
    assert not document.root.find("link")
    assert not document.root.find("svg")
    assert "secret-report-token" not in html
    assert report == before
    assert render_report_json(report) == original_json
    assert render_report_markdown(report) == original_markdown


def test_real_tensile_report_displays_existing_experiment_evidence(tmp_path):
    from cpdatakit.application import ReportRequest, build_report, integrate_tensile_bundle

    reference = Path(__file__).resolve().parents[1] / "examples/cpfe-tensile/reference"
    integrated = tmp_path / "integrated"
    result = integrate_tensile_bundle(reference, integrated)
    assert result.ok, result.to_dict()
    output = tmp_path / "experiments-report.html"
    result = build_report(
        ReportRequest(integrated / "experiments.h5", integrated / "experiments.schema.json", output)
    )
    assert result.ok, result.to_dict()

    document = _Document(output.read_text(encoding="utf-8"))

    assert document.definitions()["记录数"] == "483"
    assert document.definitions()["字段数"] == "4"
    rows = document.table("数值统计")
    assert [row["字段"] for row in rows] == ["engineering_strain", "nominal_axial_stress"]
    assert rows[0]["最大值"] == "0.008"
    assert rows[0]["数量"] == "483"
    assert rows[1]["单位"] == "MPa"
    assert document.definitions()["来源 DOI"] == "10.5281/zenodo.10820299"
    assert document.definitions()["许可"] == "CC-BY-4.0"
