# CPDataKit

简体中文 | [English](https://github.com/koocmitwho/cpdatakit/blob/main/README.md#english)

[![CI](https://github.com/koocmitwho/cpdatakit/actions/workflows/ci.yml/badge.svg)](https://github.com/koocmitwho/cpdatakit/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/koocmitwho/cpdatakit)](https://github.com/koocmitwho/cpdatakit/releases/latest)
[![PyPI](https://img.shields.io/pypi/v/cpdatakit)](https://pypi.org/project/cpdatakit/)
[![License](https://img.shields.io/github/license/koocmitwho/cpdatakit)](https://github.com/koocmitwho/cpdatakit/blob/main/LICENSE)

**把科学与工程数据中的字段、单位和检查规则写清楚，再完成验证、转换与可追查的交接。**

CPDataKit 是 Python 工具，提供本地中文工作台、命令行和 Python API。
适合需要整理仪器导出表格、统一不同数据源约定、或在分析前检查数据的实验与工程人员。
项目最初用于晶体塑性（CP），也能处理热循环等不含 CP 字段的数据。

例如，仪器用分号导出表格，力的单位是 `N`，下游需要 `kN`。
你可以预览实际列，确认字段名、类型、来源单位和输出单位，再保存转换数据、原始文件与规则。
后来检查或交接时，可以核对这次换算采用了什么声明。

本文对应 **v0.12.0**：本地工作台改为单列简洁界面，CSV 字段先以文字摘要显示、逐行编辑，
解析设置、原始数据和高级工具按需打开。已确认导入设置的复用方式不变；每份文件的来源说明、
实验事实和哈希独立保留。
安装入口见 [PyPI](https://pypi.org/project/cpdatakit/0.12.0/)，变更见
[发行说明](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/.github/release-notes/v0.12.0.md)和
[GitHub Release](https://github.com/koocmitwho/cpdatakit/releases/tag/v0.12.0)。

> 工具检查你声明的数据规则与换算，不判断实验是否正确，也不自动推断工程/真实应力应变等科学定义。

## 主要作用

- **确认 CSV 的实际含义。** 设置分隔符、表头行、独立单位行、小数符号和编码；预览后逐列确认字段、类型、单位与用途，保留原始字节及排除列的记录。
- **复用已确认导入设置。** 保存解析选项和字段声明，载入下一份同类文件；可见结构变化先显示差异，来源事实仍需逐文件确认。
- **按规则检查数据。** 数据规则（schema）声明字段、类型、形状、单位、缺失值、索引、范围与科学约定。检查结果分别列出错误和警告。
- **显式统一字段和单位。** 字段映射（mapping）用于改名与单位转换；向量、矩阵和张量按声明的形状处理。CSV 确认导入可直接填写这些声明。
- **保存可追查的结果。** CPDataKit HDF5 可保存规则、单位来源、源文件摘要、验证结果和处理记录；报告支持离线 HTML、Markdown 与 JSON。
- **继续使用已有成果。** 本地项目保存文件、规则、任务与结果；已核对的转换快照可以作为后续输入，无需下载后重新上传。
- **接入脚本工作流。** CLI 和 Python API 提供验证、统计、转换、检查、绘图、报告与比较；另有选择性读取、多维切片和批处理入口。

## 环境要求与安装

- Python **3.12 或更高版本**；本版本的完整 CI 覆盖 Python 3.12 和 3.13。
- Windows、macOS 或 Linux。使用工作台需要浏览器；使用 CLI/API 无需浏览器。
- Python 依赖由安装命令获取。工作台静态资源随包提供，**使用时无需安装 Node.js，也不依赖 CDN**。

Python 3.12 下限从 v0.6.0 开始采用。Python 3.10 和 3.11 用户可使用已发布的 v0.5.x 兼容线，
见 [v0.5.0 发行记录](https://github.com/koocmitwho/cpdatakit/releases/tag/v0.5.0)；
该旧版不包含本文的 v0.10.0 CSV 确认导入与结果复用流程。

在一个空目录建立独立环境。若下列环境、工作区或输出名称已经存在，请换用新名称。
不必激活环境，也不必修改 PowerShell 的脚本执行策略。

Windows PowerShell：

```powershell
python -m venv .venv-cpdatakit
.venv-cpdatakit\Scripts\python.exe -m pip install "cpdatakit==0.12.0"
.venv-cpdatakit\Scripts\cpdatakit.exe --version
.venv-cpdatakit\Scripts\cpdatakit.exe ui --workspace ./csv-demo-workspace
```

macOS / Linux：

```bash
python3 -m venv .venv-cpdatakit
.venv-cpdatakit/bin/python -m pip install "cpdatakit==0.12.0"
.venv-cpdatakit/bin/cpdatakit --version
.venv-cpdatakit/bin/cpdatakit ui --workspace ./csv-demo-workspace
```

版本输出应为 `cpdatakit 0.12.0`。工作台只绑定本机回环地址，默认打开浏览器；
如未打开，访问终端显示的地址。保持终端运行，结束后按 `Ctrl+C` 停止服务。
首次安装需要下载依赖；安装后，下面的本地 CSV 流程不需要外部服务或 AI 模型。

工作区存放原始文件、规则、任务记录及结果。保留这整个目录以便继续处理；对外分享前检查文件内容。
无头环境可在启动命令末尾添加 `--no-browser`，但交互操作仍需浏览器访问服务。

## 第一次使用：把三行 CSV 转成 HDF5

这个合成示例只演示文件处理，没有实验或材料验证含义。
将下列内容保存为 UTF-8 文件 `instrument-demo.csv`；也可使用仓库中的
[instrument.csv](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/examples/csv-intake/instrument.csv)。

```csv
(sec);(mm);(N);;(MPa);(mm/mm)
0;0;100;0;20;0
1;0.5;250;0;40;0.01
2;1.25;375;0;60;0.02
```

### 1. 预览原文件

在工作台创建新项目，在“添加数据”中选择“导入 CSV”，选择这个文件。
点击“解析设置”，分隔符选“分号”，表头行填 `1`，单位行填 `0`，小数符号选“.”，编码选“UTF-8 / UTF-8 BOM”。
点击“预览”，应看到 **3 条记录、6 列**。

行号从 1 开始，包含原文件中的空行；`0` 表示没有表头或独立单位行。
更改文件或解析设置后，需要重新预览。

### 2. 确认字段和单位

逐行点击“编辑”，按表填写后点击“应用”；五个保留字段的类型均选“小数”，第四列取消“保留”。

| 来源列 | 输出字段名 | 来源单位 | 输出单位 | 用途 |
|---|---|---|---|---|
| 1 `(sec)` | `time` | `s` | `s` | 时间 |
| 2 `(mm)` | `extension` | `mm` | `mm` | 测量值 |
| 3 `(N)` | `force` | `N` | `kN` | 测量值 |
| 4 无列名 | 取消保留 | — | — | — |
| 5 `(MPa)` | `reported_stress` | `MPa` | `MPa` | 测量值 |
| 6 `(mm/mm)` | `reported_strain` | `mm/mm` | `dimensionless` | 测量值 |

来源说明填写：“合成示例，单位由示例表头定义；第四列排除但保留原文件；不推断工程/真实应力应变。”
核对后勾选“已核对字段及来源”，点击“确认导入”。数字列的来源单位与输出单位必须明确；
未知单位须查证来源，不能填成无量纲。原始第四列仍保存在原文件中。

![CPDataKit CSV 确认界面：保留五列、排除第四列，力的单位由 N 转为 kN](https://raw.githubusercontent.com/koocmitwho/cpdatakit/main/assets/workbench/csv-confirmation-v0.12.0.png)

*v0.12.0 工作台，使用上面的三行合成示例。导入前可核对字段名、单位、排除列和来源说明。*

### 3. 检查、保存并继续处理

1. 导入后，当前数据与规则自动选中。点击“生成报告”，选择 JSON 或 HTML 及新的保存路径，检查 **3 条记录、零错误、零警告**。
2. 点击“转换并保存”，选择 HDF5 和新的项目内路径，例如 `results/csv-demo.h5`。
3. 完成后点击“使用此结果继续处理”。输入切换到转换时保存的快照及原规则，再用新路径生成一份报告。
4. 在“结果”中打开报告、下载 HDF5，并核对原文件、规则和导入清单。

![CPDataKit 工作台：三条记录校验通过，错误和警告均为零，结果区显示 HTML 报告、HDF5 文件和继续处理入口](https://raw.githubusercontent.com/koocmitwho/cpdatakit/main/assets/workbench/checks-and-results-v0.12.0.png)

*同一合成示例完成校验、HTML 报告生成与 HDF5 转换后的界面。结果区提供下载和“使用此结果继续处理”入口。*

结果字段顺序应为 `time, extension, force, reported_stress, reported_strain`；
`force` 应为 **`0.1, 0.25, 0.375 kN`**。CSV 导入已经应用表中声明的换算，
无需再到高级映射里重复转换。重复点击继续处理会复用同一份输入记录。

同名输出需改名另存，或明确确认替换。已登记的旧转换快照保留其原内容；
快照被改动、原规则不可用或结果来自另一项目时，不能继续使用。

详细操作、可运行的示例脚本及常见错误见
[CSV 完整示例](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/examples/csv-intake/README.md)和[中文工作台指南](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/docs/workbench-guide.md)。
普通 wheel 不包含仓库的 `examples/` 目录；上面的手动流程只需已安装的软件包。

## 得到哪些文件

| 成果 | 用途 |
|---|---|
| `source.csv` | 下载的原始字节；来源文件名与 SHA-256 记在导入清单中 |
| `schema.json` | CSV 导入后确认的数据规则，可在后续检查中复用 |
| `manifest.json` | 解析设置、字段声明、排除列、记录数与来源摘要 |
| 已确认的 HDF5 与后续转换文件 | 保存可处理的数据及其声明；文件名和目录由具体操作决定 |
| HTML / Markdown / JSON 报告 | 校验概览、字段与统计、问题及来源摘要；HTML 可离线打开与打印 |
| 项目与任务记录 | 保留处理状态、输入选择和结果引用，供重新打开工作区后查询 |

原始文件与处理结果分别保留。报告里的“有效”表示没有违反本次声明的规则；
只出现警告时仍可有效，需结合具体警告判断是否适合下游使用。

## 命令行与 Python API

### 不依赖源码仓库的命令行示例

在刚才的安装目录，使用同一个独立环境。以下命令生成固定种子的合成曲线，
完成验证、统计、转换、检查、离线报告与绘图。
这条路径使用内置 `curve` 规则，适用于生成器提供的字段，不能直接套用到任意仪器表格。

Windows PowerShell：

```powershell
.venv-cpdatakit\Scripts\python.exe -c "from cpdatakit.samples import generate_sample_data; generate_sample_data('cpdatakit-demo')"
.venv-cpdatakit\Scripts\cpdatakit.exe validate cpdatakit-demo/synthetic_curve.csv --schema curve --json-output validation.json
.venv-cpdatakit\Scripts\cpdatakit.exe summary cpdatakit-demo/synthetic_curve.csv --schema curve --json-output summary.json
.venv-cpdatakit\Scripts\cpdatakit.exe convert cpdatakit-demo/synthetic_curve.csv --schema curve --output curve.h5 --source-description "Fixed-seed README example"
.venv-cpdatakit\Scripts\cpdatakit.exe inspect curve.h5 --format json --output inspect.json
.venv-cpdatakit\Scripts\cpdatakit.exe report curve.h5 --schema curve --output report.html
.venv-cpdatakit\Scripts\cpdatakit.exe plot curve.h5 --schema curve --kind stress-strain --output stress-strain.png
```

macOS / Linux 使用相同参数，将解释器路径换为 `.venv-cpdatakit/bin/python`，
CLI 路径换为 `.venv-cpdatakit/bin/cpdatakit`。
CSV 没有声明来源单位时，这个内置规则示例会出现 `unit_not_declared` 警告；
报告区分来源声明和规则假定，不能把它写成“零警告”。

已有输出默认保留；确定要替换时，对支持该参数的命令显式添加 `--force`。
验证类命令的退出码通常为：`0` 无验证错误（可含警告），`1` 有验证错误或检查发现结构风险，
`2` 参数、规则、读取或输出错误。完整选项以 `cpdatakit --help` 和子命令帮助为准。

### 在 Python 中检查数据

先运行上面的生成器，再用同一环境执行：

```python
from cpdatakit import load_dataset, validate_dataset, summarize_dataset

dataset = load_dataset("cpdatakit-demo/synthetic_curve.csv")
validation = validate_dataset(dataset, "curve")
summary = summarize_dataset(dataset, "curve", validation=validation)
print(validation.valid)
print(summary)
```

自定义字段和单位见[规则与映射指南](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/docs/schema-authoring.md)。
更多完整操作见[五分钟教程](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/docs/quickstart.md)，选择性读取、多维查看、规则草案和批处理见
[进阶工作流](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/docs/post-v07-workflows.md)。

## 输入格式与适用范围

| 数据路径 | 范围与要求 |
|---|---|
| CSV 确认导入 | 逗号、分号、Tab；UTF-8/BOM 或 GB18030；显式字段、类型、单位与用途；默认上限 64 MiB、100,000 条记录 |
| 常规 CSV / JSON records | UTF-8 CSV 或 JSON 对象数组，配合内置或外部规则；来源单位缺失时报告规则假定警告 |
| CPDataKit HDF5 | 表格 HDF5 1.0 和多维 HDF5 2.0；保存规则、单位与来源信息，支持选择性读取 |
| 多维与其他格式 | `ScientificDataset` 的数值型多维数据、NetCDF、Zarr 3，以及 Parquet 表格；按适配器能力处理，不能假定格式之间任意互转 |
| DAMASK DADF5 | 文档化的只读选择适配器，需明确种类、标签、字段和数据集；不承诺读取所有 DAMASK 输出 |

内置 `curve`、`point`、`field2d` 规则来自原有 CP 场景。
外部 JSON 规则可以使用其他非空 profile 名称，但仍须显式声明类型、形状、单位和约定。
格式合同与适配器边界见[数据格式文档](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/docs/data-format.md)。

以下边界需要保留：

- 数字单位、应力应变定义、张量分量顺序、取向表达和 ID 含义由数据提供者确认；工具不补写未知科学含义。
- CSV 确认导入遇到异常行、缺失数字单位或无法保证精度的转换时停止，不丢弃异常行来得到通过结果。
- 新写出的 HDF5 保留列创建顺序；旧文件若没有顺序元数据，不能恢复最初输入列序。按位置使用特征数组时，应显式指定字段与顺序。
- v0.10.1 通过临时 ASCII 副本修复 Windows 中文路径下的原生 NetCDF3 读取。日期坐标可以读出，通用日期规则仍未实现。见[问题记录](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/docs/known-issues/netcdf3-datetime.md)。
- 工具不运行晶体塑性或有限元求解器；已有集成案例演示数据整理与交接，不构成独立项目间的自动接入。

## 示例与验证

先用合成数据熟悉流程，再按自己的来源记录建立规则。

- [三行仪器 CSV](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/examples/csv-intake/README.md)：逐列确认、单位换算、原字节保留与转换结果复用。
- [热循环](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/examples/thermal-cycle/README.md)：自定义 profile，摄氏度/开尔文与时间单位转换、HDF5 往返及通用 x-y 绘图。
- [KupferDigital/FE 拉伸案例](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/examples/cpfe-tensile/README.md)：使用有署名的 CC BY 4.0 处理后数据，演示实验数据交接。
- [Surfalex HF 公开参考流程](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/examples/public-datasets/surfalex-aa6016a/README.md)：显式张量映射与来源核对，原始第三方数据按需从上游获取。

本轮验证条件见
[v0.12.0 验收记录](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/docs/verification/2026-10-10-v0120.md)。
[重复导入案例](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/examples/repeated-import/README.md)
核对了 echemdb 曲线和 PyIRoGlass 光谱的独立来源与下游读取。它们证明减少了 CPDataKit
内部重复声明，没有测得相对原生项目的整体便利优势，也不构成科学有效性验证。
PyIRoGlass 使用未改写的读取器源码对照；完整包未在本次 Windows 环境安装成功。
以下为历史 v0.10.0 发布提交 `7ef4ece` 的
[完整 CI](https://github.com/koocmitwho/cpdatakit/actions/runs/36811526428)已通过：
Windows、Linux、macOS 的 Python 3.12/3.13 六套完整测试各 **1559 项通过**；
[依赖上下限矩阵](https://github.com/koocmitwho/cpdatakit/actions/runs/36811526427)的 12 套检查也已通过。
这些结果对应 2026-10-01 的发布提交。

发布验收还检查了干净安装、三行 CSV 浏览器流程，以及来自
[Mendeley DOI 10.17632/nx55jj48rx.2](https://doi.org/10.17632/nx55jj48rx.2) 的 IN718
原始压缩包中 12 个 CSV、24,073 行的来源字节、声明单位和数值。
仓库不附带该原始压缩包；仅包含来源指纹和合成 CSV。
测试通过说明被测路径满足声明与断言，不证明实验数据的物理正确性。
历史审核快照与测量条件保存在 [verification 目录](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/docs/verification/)，其中发布前快照不等同于最终发布收据。

## 贡献与许可

问题和功能建议提交到 [Issues](https://github.com/koocmitwho/cpdatakit/issues)。
请附最小合成样例、预期字段/单位和复现步骤；不要提交私有实验数据或凭据。
代码贡献见 [CONTRIBUTING.md](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/CONTRIBUTING.md)，安全问题见 [SECURITY.md](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/SECURITY.md)，
历史版本变化见 [CHANGELOG.md](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/CHANGELOG.md)。

项目采用 [Apache-2.0](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/LICENSE)，依赖与第三方材料说明见 [NOTICE](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/NOTICE)，
引用信息见 [CITATION.cff](https://github.com/koocmitwho/cpdatakit/blob/v0.12.0/CITATION.cff)。第三方数据遵循各自许可和署名要求。
