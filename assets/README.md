# Repository visual assets

`social-preview-v2.jpg` is the GitHub social preview image. It is 1280x640 pixels, opaque RGB, and
under GitHub's 1 MB limit. It was created on 2026-08-12 from `social-preview.png` and contains
synthetic diagrams and labels.

The v2 image changes three labels:

- `CONSISTENCY & PHYSICAL CHECKS` to `SCHEMA & UNIT CHECKS`.
- `TRUSTED DATA` to `TRACEABLE DATA`.
- `Works with any crystal plasticity solver` to `Works with explicit tabular inputs`.

The HDF5 label is `Auditable`, and the bottom banner reads `SCHEMA-VALIDATED · TRACEABLE ·
WELL-DOCUMENTED MATERIALS DATA`. The original PNG remains in the Git repository for comparison. The source distribution includes
the current JPG.

## Workbench screenshots

The two PNGs in `workbench/` are unmodified browser captures of CPDataKit v0.12.0, taken on
2026-10-13 with Microsoft Edge on Windows at a 1100 x 940 CSS-pixel viewport. The application
source is the same as commit `f007fb76a91311b22b7a6d07833818c8e201e2df`.

| File | Capture | Dimensions |
|---|---|---|
| `csv-confirmation-v0.12.0.png` | The Add data section before confirming CSV import | 1000 x 792 |
| `checks-and-results-v0.12.0.png` | The project page after validation, HTML report generation and HDF5 conversion | 1100 x 1080 |

Both use `examples/csv-intake/instrument.csv`, the three-row synthetic example in the READMEs.
The declarations match `examples/csv-intake/settings.json`: retain five fields, exclude the unnamed
fourth column, and convert force from N to kN. The project is named `仪器数据整理 · 合成示例`;
its source description explicitly identifies the data as synthetic. The result screenshot shows
three records, five fields, zero errors and zero warnings, plus `report.html` and `csv-demo.h5`.
These are interface illustrations, not evidence of experimental or physical validity.

The CSV image captures a page element; the results image captures the full page with CSV import,
task history and advanced tools collapsed through their normal controls. No UI text, values or
styles were changed for the captures. The images contain no browser toolbar, personal filesystem
paths or research data. Together they are about 98 KiB. Both READMEs reuse these files through
absolute image URLs so that package-index readers can load them as well as GitHub readers.
