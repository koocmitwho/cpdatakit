# 多维查看、schema 草案与批处理

安装 `python -m pip install "cpdatakit==0.8.0"` 后，即可查看多维数据、
编辑 schema 和运行批处理。需要 Python >=3.12。

## 查看 temperature(time, y, x)

启动 `cpdatakit ui --workspace .cpdatakit`，创建项目并上传 NetCDF、Zarr 3 或
CPDataKit HDF5 2.0 数据。在 **Explore a multidimensional field** 点击
**Load field controls**，选择变量、水平和垂直维度，再为其余维度设置索引。
例如选择 `x`、`y`，设置 `time=5`，即可查看第六个时间步。

色标上下限留空时自动计算，也可以手动输入并选择 viridis、magma、cividis 或 coolwarm。
点击 **Render heatmap**，生成的图片会显示在页面上，随后可用 **Download PNG** 下载。
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
点击 **Validate dataset** 可校验完整数据。当前支持二维标量切片，网格拓扑、
曲面坐标、矢量场和张量投影留待后续用例确定。

## 从文件生成 schema 草案

点击 **Draft schema from dataset**，草案会填入字段名、支持的 dtype、数组维度
和文件中的单位声明。展开 **Declarations to review** 核对单位、物理角色和约定，
补齐 JSON 中缺失的数值单位或变量角色，再点击 **Save reviewed schema**。
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

在 **Field mapping for conversion** 中填写 JSON，再点击 **Preview mapping**。
表格列出改名前后的字段、单位、维度和最多五个样本值，便于转换前核对。
**Convert dataset** 使用同一套标准化、单位换算和校验逻辑。

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
网页中的映射仅用于转换，常规 **Validate dataset** 仍检查上传文件本身。

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

清单通过临时文件原子替换，运行时用同名 `.lock` 文件标记占用，正常退出时释放。
若进程被强制结束，确认它已停止后再处理遗留锁。
如果中断发生在数据写出和清单更新之间，结果文件会保留。核对该文件后，
选择新路径重试。

Python 使用 `run_batch(config_path, manifest_path, retry=False)`，结果对象的 `value`
包含每个文件的处理记录和验证问题，部分失败时同样可读取。

## 接入已有科研结果

运行 [KupferDigital 集成示例](../examples/cpfe-tensile/README.md)，可将已有试验曲线
和 FE 训练数据转换为带 schema、单位和来源记录的 HDF5 2.0。示例保留原有数据与划分，
并分别标明试验派生数据和模拟标签。
