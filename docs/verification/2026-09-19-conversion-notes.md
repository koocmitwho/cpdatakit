# 2026-09-19 数值与单位转换保真

本记录对应本地工作树，未提交、推送或发布。测试使用独立临时合成数据；没有读取或修改用户科学数据。

## 实现

- 原生 NumPy 标量列保留 dtype，包括 int8、float32、int64、uint64。
- 对象和 pandas 可空整数列遇缺失时保留整数对象，不经过 float64；无法精确统一的混合标量同样保留 object。
- 嵌套列表与不同 dtype 数组分别检查行内构造和跨行堆叠；检查以原始 Python 整数为基准，避免比较本身再次提升成 float64。会丢失数值，或把布尔/数字/字符串类别混在一起的数组转换明确拒绝。
- 变量及记录坐标的单位都通过既有 `declared_unit` 规则解析 `unit`、`units` 和 metadata；不一致或无效声明抛出 `DataValidationError`，输入不变。
- NetCDF/Zarr 写入准备层拒绝非纯字符串 object，防止后端推断改变整数或缺失。HDF5 2.0 既有拒绝行为得到真实写入回归保护。
- 默认 HDF5 1.0 writer 共用受检列转换。可精确表示的纯数值 object、普通混合整数/浮点和数组仍可写入；无法保真的数据即使 `allow_invalid=True` 也明确拒绝，`force=True` 不破坏旧目标，暂存文件被清理。

## TDD 证据

1. 新建 `tests/test_conversion_numeric_fidelity.py` 后执行：

   `.venv/Scripts/python.exe -m pytest tests/test_conversion_numeric_fidelity.py -q`

   **44 failed、24 passed**。实际复现大整数加缺失四舍五入、`pd.NA` 转浮点异常、uint64 列重推断、两处数组提升、标准单位丢失、单位冲突未拒绝，以及 Zarr 自动编码 object 导致的缺失/数值改变。

2. 首次修复后，追加 pandas `Int64`/`UInt64`、超 uint64 Python 整数、无效 metadata.units 容器回归；同命令得到 **6 failed、68 passed**，再修复。

3. 追加默认 HDF5 1.0 回归，执行：

   `.venv/Scripts/python.exe -m pytest tests/test_conversion_numeric_fidelity.py -q -k default_hdf5`

   **10 failed、3 passed、74 deselected**。实际复现 nullable 整数和混合数组静默损失，以及原 object 写入泄漏 h5py `TypeError`。共享受检转换后全部通过。

## 最终聚焦验收

执行：

```text
.venv/Scripts/python.exe -m pytest tests/test_conversion_numeric_fidelity.py tests/test_scientific_dataset.py tests/test_scientific_data_core.py tests/test_scientific_integrity.py tests/test_scientific_report_units.py tests/test_format_metadata.py tests/test_format_interfaces.py tests/test_format_adapters_v06.py tests/test_hdf5_v2.py tests/test_hdf5_v2_fixtures.py tests/test_hdf5_validation_integrity.py tests/test_application_multiformat.py tests/test_io.py -q
```

结果：**310 passed、3 warnings，16.91 秒**。3 条警告来自现有 netCDF4/NumPy 形状赋值弃用提示，无新增 skip。

本项 87 个新用例包括：

- `2**53-1`、`2**53+1`、int64 最小/最大值、uint64 最大值和 `2**80+1`；None、NaN、pd.NA；原生及 pandas 可空整数 dtype；嵌套与混合数组。
- HDF5 1.0、HDF5 2.0、h5netcdf、netcdf4 和 Zarr 3 真实磁盘写读；整数比较使用精确整数断言，不使用 allclose。
- 四个科学后端对不能无损存储的数值/字符串/布尔 object 缺失明确拒绝，且未创建目标；默认 HDF5 force 拒绝后保留既有目标。
- 标准及兼容单位属性、metadata、记录坐标、一致/冲突/无效声明和输入不变。

本项五个代码/测试文件 Ruff check、Ruff format --check 及 git diff --check 通过。完整仓库覆盖率、构建、干净安装及浏览器验收由根任务另行记录，不能用这里的聚焦结果代替。

## 实际边界

内存 object 保留精确值不代表后端具备同样的表示能力。当前格式未增加整数缺失掩码编码；这类值保留在内存，写出时明确拒绝。不能恢复调用 CPDataKit 之前已被 pandas/NumPy 浮点化而丢失的精度。验证是在当前 Windows/Python 3.12 环境中进行，不代表完整平台及依赖版本矩阵。
