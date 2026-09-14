**P1 数据完整性修复验证 · 2026-09-11**

已经完成本轮确认的 P1：表格绘图单位、标量坐标处理、HDF5 全局属性与验证摘要、工作台产物版本身份。聊天清单中归入数据保真的辅助坐标选择也一并修复。范围对应项目审阅 F01–F05，以及 F06 的坐标与字段顺序问题。

验证截止：2026-09-11 15:51:55 +08:00。工作分支为 codex/p1-data-integrity，基于 v0.8.0（ab83c6550b10efe0b4d323cb2add579a52331ccf）。本记录对应提交前的本地验证，未涉及推送或发布。

**现在的行为**

| 问题 | 修复结果 | 回归覆盖 |
| --- | --- | --- |
| Pa 数值被标为 MPa | 按数据实际单位标注；未带单位的旧输入沿用 schema 默认值；数值换算通过显式映射完成 | 曲线、直方图、XY、二维场；Pa/MPa、mm/m、degC/K |
| 映射输入单位冲突 | 核对单位尺度和偏移；接受 pascal、Pa、N/m² 等等价表达式，拒绝矛盾声明 | 标量、固定形状数组、偏移单位、别名及组合表达式 |
| 标量坐标静默丢失 | 转表格时返回 LossyConversionError，并保留原数组和元数据 | 数值和字符串标量坐标 |
| HDF5 全局属性丢失 | 用独立 attributes_json 保存可往返的 JSON 属性；旧文件缺少该字段仍可读取 | 嵌套属性、命名空间分离、损坏和不支持属性 |
| HDF5 辅助坐标遗漏 | 按维度关联保留辅助与标量坐标，按首个请求字段确定记录轴 | stage(time)、环境标量、坐标单独选择、混合记录轴 |
| HDF5 默认 valid=true | 写入前执行共享科学校验；默认拒绝无效值；显式 allow_invalid=True 保存真实失败摘要及实际单位 | NaN、Inf、dtype、单位缺失/冲突、伪造或过期摘要 |
| 旧 artifact ID 指向新内容 | 新登记产物保存独立版本；结果路径绑定该版本；下载核对内容哈希 | 转换、报告、绘图、Zarr、比较包、重启和重复覆盖 |
| 新的异常与耗时边界 | 非法绘图单位在创建图前拒绝；下载哈希检查在线程中执行 | 图对象清理、并发健康请求、登记失败清理 |

共享的 validate_scientific 和 declared_unit 移至 data 层，application 中原有导入位置继续可用。HDF5 1.0 和 v0.5 公共契约的既有测试通过。

**TDD 过程**

每组先增加回归并观察当前实现失败，然后修改实现：

| 组别 | 初始失败证据 | 修复后的验证 |
| --- | --- | --- |
| 单位 | 9 failed，1 passed | 直接绘图与应用服务、单位换算的定向套件通过 |
| 数据保真 | 10 failed，1 passed | 全量与选择性 xarray 往返断言通过 |
| HDF5 验证摘要 | 12 failed，1 passed | 默认拒绝与显式无效写入均通过 |
| 产物身份 | 8 failed | 旧、新版本下载及重启测试通过 |
| 图对象与下载响应 | 5 failed | 图对象不泄漏；健康请求不等待文件摘要 |
| 等价组合单位 | 1 failed，1 passed | N/m² 与 Pa 的等价表达式通过 |

已有选择性读取 fixture 未在数据中声明单位，现补齐它本来声明为 1 的合成单位；没有放宽新验证规则。绘图测试增加结束后的图对象清理，消除按不同顺序组合测试时遗留图对象的干扰。

**最终结果**

| 检查 | 结果 |
| --- | --- |
| Windows / CPython 3.12.10 整套测试 | 615 passed，7 warnings，65.19 秒 |
| 语句覆盖率 | 87.66%，门槛 85% |
| 相对审阅基线新增回归 | 49 项 |
| Ruff check / format check | 通过 |
| pip check | 通过 |
| git diff --check | 通过 |
| 版本元数据检查 | 保持 0.8.0，一致 |
| 隔离构建环境生成 wheel | 通过，hatchling==1.32.0 |
| wheel 安装目标中的 P1 回归 | 49 passed，4.76 秒 |
| 安装后的 CLI 冒烟 | version、ui help、validate、summary、convert、plot 通过 |
| 实际 Uvicorn HTTP | /health、/、四项静态资源均为 200，服务正常停止 |
| 旧 HDF5 冒烟 | 61 条记录可读 |

源码测试命令：

~~~powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --cov=cpdatakit --cov-report=term-missing --cov-fail-under=85
.\.venv\Scripts\python.exe -B -m ruff check . --no-cache
.\.venv\Scripts\python.exe -B -m ruff format --check . --no-cache
.\.venv\Scripts\python.exe -B -m pip check
git diff --check
~~~

四个新增回归文件：

- [test_plot_unit_integrity.py](../../tests/test_plot_unit_integrity.py)
- [test_scientific_integrity.py](../../tests/test_scientific_integrity.py)
- [test_hdf5_validation_integrity.py](../../tests/test_hdf5_validation_integrity.py)
- [test_artifact_versions.py](../../tests/test_artifact_versions.py)

本地 wheel 的 SHA-256：
e0a2819a8cd8319dcc01d38a89c69501f18358e04f9416e3a0948454f3f529ad。

wheel 安装到独立临时目标目录，并断言实际导入路径来自该目录；运行时依赖复用当前虚拟环境。因此这是已安装 wheel 的验证，不是从空环境重新安装整套依赖。未重跑 Linux/macOS 矩阵或真实浏览器视觉验收。七条警告来自已有 xarray/netCDF4 的 NumPy shape 弃用提示及 multipart 导入弃用提示。

**使用边界**

全局属性目前保存 JSON 可往返的类型；不支持的类型会明确报错。标量非记录坐标目前采用拒绝有损转换，不新增隐式展平规则。HDF5 2.0 的直接写入调用需要真实单位声明，无效写入需明确指定 allow_invalid=True。

工作台仍保留用户选择的输出路径，同时保存每次登记的独立版本，历史副本占用额外磁盘空间。旧记录若内容已被覆盖，下载会拒绝提供与原哈希不符的数据；没有快照的历史字节无法凭哈希恢复。新目录产物哈希覆盖成员路径与内容，旧比较包继续识别原有 manifest 哈希。

本地构建用于验证，项目版本仍为 0.8.0。其他工作区中的既有未提交文档保留原状。
