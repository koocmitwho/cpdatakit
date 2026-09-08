# 选择性读取与取消检查点

NetCDF、Zarr 3 和 HDF5 2.0 先选字段与范围，再读取数组。Parquet 通过文件元数据定位
重叠行组，跳过其他行组，在选中行组内按最多 65,536 行的批次读取所需列。

```python
from pathlib import Path
from cpdatakit.formats import NetCDFReader, ParquetReader, Selection

plane = NetCDFReader().load(
    Path("temperature.nc"),
    selection=Selection(
        fields=("temperature",),
        indexers={"time": 5, "y": slice(10, 50), "x": slice(0, 80, 2)},
    ),
)
rows = ParquetReader().load(
    Path("records.parquet"),
    selection=Selection(
        fields=("stress",),
        start=100_000,
        stop=101_000,
    ),
)
```

`start` / `stop` 指定半开记录范围。科学数组的记录维是所选首个字段的首维。
`indexers` 使用非负整数或步长为正的切片，整数索引保留长度为 1 的维度。
记录范围和具名索引可同时使用，分别作用于不同维度。Parquet 使用记录范围。
读取结果包含关联坐标、单位、JSON 元数据和源路径，所选数据会加载到内存。
因此，内存须能容纳选择后的结果。

实际读取量取决于 Parquet 页和行组、NetCDF/Zarr 存储块的大小。
读取使用项目已有的 xarray、h5py 和 PyArrow。

## 可复现基准

```bash
python scripts/benchmark_selective_read.py --output-dir .artifacts/selective-baseline --time 128 --side 512 --rows 2000000 --repeats 3
```

脚本需要一个新的空目录，用固定随机种子生成三个 `float32(128,512,512)` 数组
（合计 384 MiB）和 200 万行、8 列 Parquet。
它在独立进程中交替运行 v0.7 的先加载后切片方法与选择性读取，每次核对选中值的 SHA-256。
`report.json` 记录文件大小、环境、形状、耗时和操作系统进程峰值 RSS。

计时从 Python 导入完成后开始，峰值内存包含导入开销。基准沿用当时的操作系统缓存状态。
本机结果及三次原始测量见 [验证记录](verification/2026-09-07-post-v07.md)。

## 后台任务的进度和取消

`JobManager` 传入兼容 `threading.Event` 的 `JobContext`，原有 Event 任务可继续使用。
`context.checkpoint(stage)` 更新阶段日志并检查取消请求，页面进度和目录状态读取同一份日志。

科学格式转换在加载、校验前和写出前检查取消。NetCDF/Zarr 还会在变量和约 8 MiB
的首轴数据片段之间检查，单行超过该大小时按一行读取。Parquet 在读取批次之间检查，
HDF5 2.0 则在选中数组读取完成后进入下一检查点。
当前底层读取、校验或写入调用先执行完毕，取消请求在后续检查点生效。

工作台转换先写暂存结果，再将结果移到目标路径并登记，最后这一步串行执行。
进入该阶段前的检查点收到取消请求时，任务停止并保留原目标。
结果写入和登记完成后，任务保留 `succeeded` 状态和结果引用。
目录登记失败会恢复旧目标，新热图登记失败会删除本次图片。
若文件系统恢复也失败，旧目标留在备份目录中。
