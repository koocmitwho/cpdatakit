# 多维查看、schema 草案与批处理

安装 `python -m pip install "cpdatakit==0.9.1"` 后，即可查看多维数据、
编辑 schema 和运行批处理。需要 Python >=3.12。
首次使用页面时，先按[当前中文工作台指南](workbench-guide.md)完成上传、校验与报告。

## 查看 temperature(time, y, x)

启动 `cpdatakit ui --workspace .cpdatakit`，创建项目并上传 NetCDF、Zarr 3 或
CPDataKit HDF5 2.0 数据。展开 **高级查看 · 多维数据的二维切片**，点击
**读取可用变量**，选择变量、水平轴和垂直轴，再为其余维度设置索引。
例如选择 `x`、`y`，设置 `time=5`，即可查看第六个时间步。

色标上下限留空时自动计算，也可以手动输入并选择 viridis、magma、cividis 或 coolwarm。
点击 **生成切片图**，生成的图片会显示在页面上，随后可用 **下载 PNG** 下载。
图中标注变量、单位和切片位置，PNG Description 元数据另存索引和坐标值。
缺少单位声明的变量显示 `unit unknown`。

Python 使用同一服务：

```python
from pathlib import Path
from cpdatakit.application import SliceRequest, plot_scientific_slice

result = plot_scientific_slice(
    SliceRequest(
        Path("temperature.nc"),
        "temperature",
        "x",
        "y",
        {"time": 5},
        Path("results/time-5.png"),
        vmin=273,
        vmax=310,
        cmap="magma",
    )
)
assert result.ok, result.to_dict()
```

热图按所选切片读取数据，默认每张图最多 100 万个像素。
一维数值坐标须严格单调，其余情况按索引显示。绘图时检查所选平面，
点击 **校验数据** 可校验完整数据。当前支持二维标量切片，网格拓扑、
曲面坐标、矢量场和张量投影留待后续用例确定。

## 从文件生成 schema 草案

展开 **高级设置 · 生成规则草案与预览字段映射**，点击 **从数据生成规则草案**。
草案会填入字段名、支持的 dtype、数组维度和文件中的单位声明。
展开 **需要确认的声明** 核对单位、物理角色和约定，
补齐 JSON 中缺失的数值单位或变量角色，再点击 **保存已确认的规则**。
草案用 `null` 标记缺失项，无量纲单位填写 `"1"`。未补齐的必填项会在保存时报告错误。

命令行也可生成草案：

```bash
cpdatakit schema draft temperature.nc --output draft.json
```

`draft.json` 包含 `schema`、`observations` 和 `review`。按 `review` 核对字段后，
把编辑好的 `schema` 对象单独保存为 `schema.json`，供校验和转换命令使用。
`ready=false` 标记尚待确认的草案，对象数组等 dtype 不明确的字段也会列入审阅项。
应力应变定义、坐标系和张量分量顺序需依据试验记录或求解器说明填写。

## 转换前预览映射

在 **转换时使用的字段映射** 中填写 JSON，再点击 **预览映射**。
表格列出改名前后的字段、单位、维度和最多五个样本值，便于转换前核对。
**转换并保存** 使用同一套标准化、单位换算和校验逻辑。

预览会完整校验输入，默认上限为 64 MiB / 10,000 条记录。
字节上限同时用于文件大小和数值数组展开后的内存估算。
超限或可变长度科学数组无法估算内存时，会报告读取限制。

表格沿用现有映射格式：

```json
{"mappings": [{"source": "temp_C", "target": "temperature", "input_unit": "degC", "output_unit": "K"}]}
```

多维数据使用映射版本 2.0：

```json
{
  "mapping_version": "2.0",
  "dimensions": {"t": "time"},
  "mappings": [
    {"source": "temp_C", "target": "temperature", "input_unit": "degC", "output_unit": "K"}
  ]
}
```

维度改名后，数组按目标 schema 的维度顺序转置。`input_unit` 须与文件已有的
单位声明一致，所用的物理角色和应力应变定义需在 schema 中写明。

```bash
cpdatakit mapping preview temperature.nc --schema schema.json --mapping mapping.json --output preview.json
cpdatakit convert temperature.nc --schema schema.json --mapping mapping.json --output temperature.h5
```

Python 对应 `draft_schema(ImportInspectRequest(...))` 和
`preview_mapping(DatasetRequest(...))`。转换结果保留字段、维度映射和映射文件哈希。
网页中的映射仅用于转换，常规 **校验数据** 与 **生成报告** 仍检查所选上传文件本身。
要在页面检查转换后的文件，请将该结果作为新输入上传，再选择匹配规则。

## 一份配置处理多份输入

批处理 JSON 使用配置文件所在目录作为相对路径基准：

```json
{
  "batch_version": 1,
  "defaults": {"schema": "curve", "output_format": "hdf5"},
  "items": [
    {"input": "input/first.csv", "output": "results/first.h5"},
    {"input": "input/second.csv", "output": "results/second.h5"}
  ]
}
```

每项可覆盖 `schema`、`mapping`、`output_format` 和 `source_description`。
schema 接受内置名称、JSON 路径或内嵌对象，映射使用 JSON 路径。

```bash
cpdatakit batch examples/batch/batch.json --manifest .artifacts/batch-run.json
cpdatakit batch examples/batch/batch.json --manifest .artifacts/batch-run.json --retry
```

批处理先检查输出重名、目录包含关系，以及输出与输入、schema、映射或清单路径的冲突。
一个文件失败后会继续处理下一个，验证问题、处理参数和输入输出哈希逐项写入清单。
CLI 全部成功返回 0，逐文件部分失败返回 1，配置或清单错误返回 2。

遇到已有输出时会报告冲突并保留文件。重试会核对上次成功项的参数、输入和输出哈希，
全部匹配就跳过，修正后的失败输入则重新处理。
如果成功项的输出被改动，清单记录 `output_changed`。要更换该项的输入或参数，
使用新的输出路径重新运行。

每项处理前先保存执行意图、参数指纹和独立暂存位置。转换完成后，先保存结果摘要与
产物哈希，再将产物发布到目标路径。若中断发生在发布与清单完成之间，`--retry`
会核对准备记录、输入、暂存内容和目标哈希；证据一致时恢复完成状态。

运行使用操作系统文件锁，进程退出后锁自动释放；小型 `.lock` 文件会保留，以免删除
锁文件时产生另一个锁实例。锁路径不能与配置、输入、schema、组合引用或 mapping 重叠。
没有完整准备记录的旧中断、被改动的输入或结果不会被自动认领。尚未验证的中断暂存
内容会保留供检查。清理失败会记录遗留位置，不把已经发布的结果改判为失败。

Python 使用 `run_batch(config_path, manifest_path, retry=False)`，结果对象的 `value`
包含每个文件的处理记录和验证问题，部分失败时同样可读取。

## 比较多维报告

schema 2.0 报告比较会检查维度、坐标、变量、单位、角色和约定，并读取各字段的
聚合统计。相关坐标的实际单位或形状有冲突时，依赖变量的数值变化列为
`incomparable`，同时保留原因；缺失统计也单独列出。比较结果中的数值变化带单位。
schema 1.0 保留原有结果结构；跨版本比较需先提供显式映射。

## 工作台的结果与任务规模

转换、报告、绘图和比较包共用暂存、发布、登记和恢复流程。失败时只恢复仍属于本次
操作的输出；遇到并发替换，保留新目标和旧备份，并在 `recovery.json` 记录恢复位置。
登记快照会核对本次实际产物的哈希。

v0.9.0 的任务记录会显示 **结果等待保存** 状态，终态通过独立完成证据和目录记录保存，
暂时失败时进行有限重试。工作区同时只允许一个参与此机制的工作台实例持有写入权。
在任务记录旁点击 **查看中断任务的恢复信息 →**，可查看中断输出的证据，并将通过
身份与哈希校验的内容复制到项目内新位置。已有文件不会被覆盖，冲突或损坏的证据会保留。
这些机制针对进程中断；如果目录数据库和独立完成证据都未能写入，无法保证退出后恢复内存结果。

文件禁止覆盖发布使用同一文件系统的硬链接；目录使用平台的排他重命名操作。
不支持这些原语的文件系统会明确报错。显式覆盖仍需 `force`。

工作台默认最多接收 64 个待处理任务，在内存中保留最近 256 个已持久化的完成任务；
单个进度日志最多 256 条、每条 512 字符。这些值可通过 `create_app` 的
`max_pending_jobs`、`max_retained_jobs`、`max_log_entries`、
`max_log_entry_chars` 调整。结果存入 SQLite 后才淘汰内存状态，重启后仍可读取详情。

页面先显示各类资源最近 50 条，可按需加载更早记录。历史完成任务不会自动轮询，
详情在点击时读取。API 支持 `limit`（1–200）、`offset` 和 `newest_first`；
不传分页参数时保留原有完整查询行为。

## 接入已有科研结果

运行 [KupferDigital 集成示例](../examples/cpfe-tensile/README.md)，可将已有试验曲线
和 FE 训练数据转换为带 schema、单位和来源记录的 HDF5 2.0。示例保留原有数据与划分，
并分别标明试验派生数据和模拟标签。
