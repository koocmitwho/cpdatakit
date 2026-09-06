# CPDataKit

CPDataKit 是一个面向科学和工程数据的 schema-first Python 验证、标准化和审计工具。项目最初
从晶体塑性工作流开始，crystal plasticity 仍然是第一个完整支持的垂直场景。

> **Alpha 版本：** 验证报告说明记录是否符合所选 schema。物理结果使用领域方法解释。
> 仓库示例使用固定随机种子，公开参考数据保留在上游来源。

## 什么时候用

数据交接时，字段名和单位很容易分叉。例如热循环导出器使用摄氏度而下游需要开尔文；晶体
塑性导出器给出 `eps` 和 `sigma_pa`，分析脚本却需要 `strain` 和 `stress`。CPDataKit 把这些
约定写进 schema 和 mapping 文件，并把验证结果保存到输出 HDF5。

它可以放在分析脚本前或文件交接环节，也适合记录字段改名的原因。CPDataKit 把数据契约、
来源、验证和单位转换集中在数据边界，并提供 CPDataKit HDF5、选定 DAMASK DADF5 数据和
Surfalex 公开参考流程的文档化路径。

内置 `curve`、`point` 和 `field2d` 是来自 CP 垂直场景的兼容 profile。外部 JSON schema 可使用
其他非空 profile 名称。CPDataKit 读取 UTF-8 CSV、JSON records 和自有 HDF5，并提供 schema
diff 与离线报告比较。schema 显式声明字段、类型、shape、
物理角色、单位、缺失值、索引、
范围与科学约定。应力/应变量、张量顺序、取向表达、单位和 ID 含义都通过 schema 或 mapping
显式提供。DAMASK DADF5 只读适配器在文件中存在一个明确选择时，也能完成检查和报告。
Writer 还会把完整的 canonical schema 和 SHA-256 写入 HDF5。提供 schema URI 时，
CPDataKit 会把它记录为由调用方管理的 provenance。
v0.6 还提供 `ScientificDataset`、CPDataKit HDF5 2.0、NetCDF、Zarr 3 和仅限表格的 Parquet
适配器，所有 N 维数据和能力检查都保持显式。

## 安装与快速开始

v0.7 工作台支持多维数据上传、自定义 schema、验证、转换和报告。
使用方法见 [v0.7 工作台指南](docs/v0.7-workbench.md)。

当前 `v0.7.0` 已发布到 PyPI，使用以下命令安装：

```powershell
python -m pip install cpdatakit
```

v0.6.0 要求 Python 3.12 或更高版本，因为 xarray 和 Zarr 已经高于 v0.5 的依赖下限。
Python 3.10 和 3.11 用户继续使用已发布的 v0.5.x 兼容线。

如果需要固定 GitHub Release wheel，可使用：

```powershell
python -m pip install "https://github.com/17636365690/cpdatakit/releases/download/v0.7.0/cpdatakit-0.7.0-py3-none-any.whl"
```

然后按照[五分钟快速教程](https://github.com/17636365690/cpdatakit/blob/main/docs/quickstart.md)
验证、统计、转换并绘制固定种子生成的示例。

## 仓库里的工作流

示例和测试覆盖以下路径：

- 在分析或交换前，按显式 schema 验证 curve、point 和二维 field 数据；
- 使用 JSON mapping 文件处理不同导出器的字段名和单位。声明过的向量、矩阵和张量字段会
  按元素转换单位，并保持原有 shape；
- 以声明的 shape 和 component order 保存向量/张量数据；
- 转换为包含单位、映射、来源和验证摘要的可审计 HDF5；
- 在 CI、文档和实验脚本中生成固定种子的合成测试数据。
- 复现公开 Surfalex HF（AA6016A）Workflow 7A 的转换，查看显式张量 mapping、来源 hash
  和 schema provenance。流程按需下载第三方原始文件，并记录来源 hash。
- 运行不含晶体塑性字段的 `examples/thermal-cycle/`，完成自定义 profile、显式温度/时间单位
  转换、HDF5 round-trip、检查、报告、比较和通用 x-y 绘图。
- 使用 `cpdatakit ui` 启动 loopback-only 本地工作台，执行项目上传、检查、验证、转换、报告、
  比较、绘图和 job 轮询；静态资源随 wheel 提供，不依赖 CDN。

## 项目与集成链接

- [PyPI 软件包](https://pypi.org/project/cpdatakit/)
- [v0.7.0 GitHub Release](https://github.com/17636365690/cpdatakit/releases/tag/v0.7.0)
- [v0.5.0 GitHub Release](https://github.com/17636365690/cpdatakit/releases/tag/v0.5.0)
- [五分钟快速教程](https://github.com/17636365690/cpdatakit/blob/main/docs/quickstart.md)
- [Schema authoring 与 mapping 指南](https://github.com/17636365690/cpdatakit/blob/main/docs/schema-authoring.md)
- [示例目录](https://github.com/17636365690/cpdatakit/tree/main/examples)
- [公共参考案例 #1：Surfalex HF](https://github.com/17636365690/cpdatakit/tree/main/examples/public-datasets/surfalex-aa6016a)
- [路线图与 Issue](https://github.com/17636365690/cpdatakit/issues)
如果需要新的数据契约或输入格式，请在 Issue 中附一个小型合成样例和字段规则，后续改动就有
具体的测试对象。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python examples/generate_sample_data.py --output sample_data
cpdatakit validate sample_data/synthetic_curve.csv --schema curve --json-output validation.json
cpdatakit summary sample_data/synthetic_curve.csv --schema curve --json-output summary.json
cpdatakit convert sample_data/synthetic_curve.csv --schema curve --output curve.h5
cpdatakit plot curve.h5 --schema curve --kind stress-strain --output curve.png
cpdatakit plot curve.h5 --schema curve --kind stress-strain --output curve.svg
cpdatakit inspect curve.h5 --format json --output inspect.json
cpdatakit report curve.h5 --schema curve --output report.html
```

通用热循环示例的核心命令如下，完整流程见 `examples/thermal-cycle/README.md`：

```powershell
cpdatakit validate examples/thermal-cycle/input/thermal-cycle.csv --schema examples/thermal-cycle/schema/thermal-cycle.json --mapping examples/thermal-cycle/mappings/thermal-cycle.json
cpdatakit convert examples/thermal-cycle/input/thermal-cycle.csv --schema examples/thermal-cycle/schema/thermal-cycle.json --mapping examples/thermal-cycle/mappings/thermal-cycle.json --output thermal-cycle.h5
cpdatakit plot thermal-cycle.h5 --schema examples/thermal-cycle/schema/thermal-cycle.json --kind xy --x time --y temperature --output temperature-vs-time.png
```

对于字段名或单位不同的导出数据，可显式提供 mapping 文件：

```powershell
cpdatakit convert raw.csv --schema curve --mapping mapping.json --output curve.h5
```

详见[schema authoring 与 mapping 指南](https://github.com/17636365690/cpdatakit/blob/main/docs/schema-authoring.md)。

比较两个 schema 契约：

```powershell
cpdatakit schema diff old-schema.json new-schema.json --format markdown --output schema-diff.md
```

结果分为 identical、backward-compatible 和 breaking。这个命令只读。记录迁移和 HDF5 重写另行处理。

启动本地工作台（默认绑定 loopback 并打开浏览器）：

```powershell
cpdatakit ui
cpdatakit ui --workspace .\cpdatakit-workspace --no-browser
```

`--no-browser` 适用于无头环境和 CI smoke；工作台的上传、验证、转换、报告、比较、绘图、能力
发现和 job 轮询都限制在显式 workspace 内。

比较两份 JSON 验证报告并生成离线 bundle：

```powershell
cpdatakit compare left-report.json right-report.json --output comparison-bundle
```

bundle 包含 JSON、Markdown、HTML 和带成员 hash 的 manifest。比较内容包括声明的 schema、验证结果、
结构和标量统计。原始张量记录和物理等价性需要另行分析。

`inspect` 的 schema 参数可选。它会显示文件类型、格式版本、字段 dtype/shape/单位、缺失值、
HDF5 chunk、provenance、adapter 和结构风险。`report` 要求显式 schema，默认生成可离线打开的
HTML，也支持 `--format markdown` 和 `--format json`。报告包含统计和验证结果，原始记录继续保留在
输入数据中。替换已有输出时显式传入 `--force`。处理成功且没有验证错误时退出码为 `0`；
验证错误，或 `inspect` 发现声明的结构/缺失值风险时为 `1`。只有 warning 的结果仍会被报告，
但不会让结果失效。参数、schema、读取和输出错误为 `2`。验证结果描述声明的结构检查，物理或科学判断结合领域方法完成。使用 `cpdatakit --help`
查看完整帮助。

## Python API

```python
from cpdatakit import (
    build_report,
    inspect_dataset,
    load_dataset,
    load_hdf5,
    validate_dataset,
    normalize_dataset,
    summarize_dataset,
)
from cpdatakit.adapters import DamaskDADF5Adapter

dataset = load_dataset("sample_data/synthetic_curve.csv")
result = validate_dataset(dataset, "curve")
summary = summarize_dataset(dataset, "curve", validation=result)
print(result.valid, summary)
print(inspect_dataset("curve.h5", schema="curve")["record_count"])
print(build_report("curve.h5", "curve")["validation"]["valid"])

dadf5 = DamaskDADF5Adapter(
    kind="homogenization", label="Taylor", field="mechanical", datasets=["F", "P"]
).load("result.hdf5")
window = load_hdf5("curve.h5", fields=["step", "stress"], start=10, stop=20)
```

字段映射与单位转换必须通过 `FieldMapping` 显式提供。未映射的原始字段默认保留。
绘图函数返回 Matplotlib `Figure/Axes`，支持 PNG 和 SVG，并适用于 CI 无显示环境。

## 文档、范围与贡献

详细格式见[数据格式文档](https://github.com/17636365690/cpdatakit/blob/main/docs/data-format.md)，
架构、适配器、维护和路线图见仓库 `docs/`。
仓库提供一个文档化的 DAMASK DADF5 只读选择适配器，适配器贡献按格式证据、许可、可复现
夹具和科学约定清单审核。贡献前请阅读
[CONTRIBUTING.md](https://github.com/17636365690/cpdatakit/blob/main/CONTRIBUTING.md)。项目采用
Apache-2.0，依赖许可核查见
[NOTICE](https://github.com/17636365690/cpdatakit/blob/main/NOTICE)，引用信息见
[CITATION.cff](https://github.com/17636365690/cpdatakit/blob/main/CITATION.cff)。
