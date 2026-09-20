# 2026-09-19 数值摘要局部验收

本记录仅覆盖本轮第二项及其比较链路；不是整轮构建、安装或工作台恢复已验收的声明。
实际运行环境为 Windows、仓库 `.venv` Python 3.12.10，无新增跳过项或断言放宽。

## 先失败再修复

1. 新增 `tests/test_summary_numeric_fidelity.py` 后首次运行：**35 failed、8 passed**。
   整数 min/max 被转为 float；float32 抵消得到 0；有限 float32/float64 同号求和溢出；
   float64 中间溢出及小残差丢失；v1 的总体标准差也会溢出或丢失相邻整数差。
   真实 NetCDF 与 Zarr 的 JSON 报告路径均重现 `internal_error`，底层错误为报告含非有限数值。
2. 引入共享数值摘要 helper，使用 Python 标准库按精确整数比累加的 `mean` 和 `pstdev`，
   只在最终输出均值/标准差时转为 float；整数极值保留 Python int。首轮 **43 passed**。
3. 补 object/缺失/数值字符串及比较差值溢出回归：先 **7 failed、46 passed**。
   v1 对整个 object 列进行数值转换仍会提前浮点化；科学摘要原来遗漏 numeric object；
   两份极端有限报告的差值可能溢出并被静默渲染为 `not available`。
   改为逐标量转换及显式 numeric object 摘要；不可表示的浮点差值归入 `unavailable`，
   附明确原因并保留左右原值。精确整数的 +1 比较差仍为 Python int。
4. 增补真实后端 int64/uint64 极值 JSON 回归后，初轮新文件 **59 个用例**。

## 实现代理初轮局部检查

执行：

```text
.venv/Scripts/python.exe -m pytest tests/test_summary_numeric_fidelity.py tests/test_normalization_statistics.py tests/test_scientific_report_units.py tests/test_comparison_v2.py tests/test_comparison.py tests/test_reporting.py -q --tb=short
```

结果：**183 passed，19.09 秒**。本次输出没有 warnings。
所改生产文件与新增测试的 Ruff 和格式检查通过，`git diff --check` 通过。

覆盖 2**53±1、int64/uint64 边界、缺失整数、混合整数/浮点 object、float32 的抵消及溢出、
float64 最大有限值同号/异号、溢出中间和后保留 1e-100 残差、最小 subnormal、空数组、
全缺失和真实非有限输入。真实格式使用实际 NetCDFWriter/ZarrWriter 与读取器，
并通过 ReportRequest 生成 JSON、ComparisonRequest 生成比较包。
R2 单位冲突及受影响字段不可比较语义继续由旧回归覆盖。
v1 的 `numeric_fields`、`min/max/mean/std` 及 `not available` 结构保持。

## 成本与边界

在同一源码环境对 `np.linspace(-1000.0, 1000.0, n, dtype=float64)` 做单次墙钟测量，
准备数据与对象构建不计时，摘要本身计时，传入已完成的 ValidationResult：

| 元素数 | 科学摘要，秒 | v1 摘要含总体标准差，秒 |
| --- | ---: | ---: |
| 100,000 | 0.039659 | 0.129595 |
| 1,000,000 | 0.427801 | 1.513733 |

这是本机单次实测，不是性能保证或跨平台基准。计算遍历标量并创建有限值列表，
比纯 NumPy 归约有额外 CPU/内存成本；本轮优先保证数值证据可靠。
均值和总体标准差为最终二进制浮点近似；整数极值保持精确。
numeric object 在内存可统计，不代表真实后端一定能无损存储，后端准入由转换层处理。
本轮只验证上述后端与当前依赖组合，不承诺全部平台或扩展浮点类型。

## 独立审查后的增量

独立审查及整合补充了 15 个回归，新增测试文件现为 74 个用例：

- 整数 `2**53+1` 与浮点 `2**53` 的报告比较，原直接减法仍把差值算成 0。
  v1 与真实 NetCDF/Zarr 报告比较改为先按精确比值相减、最后舍入，结果为 -1。
- v1 的缺失 object 列含 `2**64` 或 `2**80+1` 时，NumPy 有限性检查抛异常；
  Python 整数现在直接视为有限，极值保留整数。
- 标量 schema 下实际装入 list、array、dict 时，逐值转换不得使摘要抛异常；
  继续保留验证错误，摘要为 `not available`。比较也继续接受 NumPy float32 标量。
- 再补二维数组（先 1 failed、4 passed）和 NumPy 大整数/浮点相等判定
  （先 4 failed）；先验证标量再转换，比较前转为 Python 标量，避免 NumPy
  在执行减法之前把不相等的值误判为相等。零维数组也保留验证错误而不崩溃。

前两项先复现 5 个失败；后两项先复现 4 failed、2 passed，再修复。
暂停后使用独立安装的 C 盘 Python 3.12.13 验证环境重新运行相同扩大回归，
最终 **198 passed，12.17 秒，无 warnings**。原 Python 3.12.10 所在 E 盘不可访问，
原环境没有改写；该环境事件及完整验收结果在本轮总记录中说明。
