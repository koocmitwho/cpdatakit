# CPDataKit 数值保真与持久恢复实施交接

> 用户已授权在本项目下新开对话实施。先测试失败再修复，持续做到本地验收完成；
> 不再询问模型、推理强度或是否开始。可并行分工，但共享文件必须指定唯一编辑者。

**Goal:** 完成下面五项已复现缺陷及三项工程改进，保留既有 API 和 R1–R5 的行为。
**Architecture:** 科学转换/统计与工作台恢复分离；任务终态与输出事务使用持久证据，
工作区所有权由明确生命周期管理。恢复清单只读列出候选，恢复操作须验证归属。
**Tech Stack:** Python >=3.12、NumPy/pandas/xarray、HDF5/NetCDF/Zarr、FastAPI、SQLite。
**Spec:** 用户 2026-09-19 在本对话确认“在本项目下新开对话完成这些优化，
推理强度和快速要求和本对话一致”。调查结果与约束均整理在本文件。
**状态：** 交接已准备；尚未实施本轮代码，不能把创建对话当作完成优化。

## 当前状态、设置与授权边界

- 实际仓库是保存项目目录下的 `cpdatakit-next/`；外层虽有 Git 元数据，但没有有效 HEAD。
  在现有项目目录创建本地任务，所有 Git/测试/构建命令显式以实际仓库为 cwd。
- 起点：`codex/20260916-reliability-followup`，
  `054b2fb9584f2d7f5ab40264a220d579c48811fb`，已推送。
  交接前工作区干净；本文件是本次新建、需保留的交接资产。
- 2026-09-19 实时核验：main 仍为 `2ce41c404c6228820bb190b5d41fb3684dfa6e7d`，
  GitHub/PyPI 最新发行版仍 0.8.1；054b2fb 没有 PR 或 Actions 运行记录。
- 当前父线程实际 turn context：`gpt-6-astra / ultra`。
  当前 Fast 配置：`service_tier = "priority"`。全局 reasoning 默认为 max，不能替代本线程
  已核验的 ultra；新线程需显式 ultra，并核验 Fast 继承。Fast 是服务档，不承诺固定耗时。
- 优先使用 `.venv/Scripts/python.exe`；父代理实测 Python 3.12.10 正常可用。
  子代理曾遇到引用路径启动失败，应先核对命令/cwd，不得据此改权限或重装用户环境。
- 本次授权本地实现、回归与验收，**不新增 commit、push、PR、merge、tag 或发布**。
  先前的“提交”“推送”已由 054b2fb 完成，不作为新工作的一揽子外部授权。
- 不清理、覆盖、stash 或 reset 兄弟 checkout；不改账户、安全权限和 API 凭据。
  不触碰 OSS 申请记录、邮箱、私有 CPFE/求解器数据。
- 保留 Python/public API/CLI、schema 1.0、默认 HDF5 1.0、v1 numeric_fields 及 R1–R5。
  CF 非单调全轴自动索引类型的已披露边界继续保留，不承诺零选区外 CF 类型探测。
- 不降低测试断言、85% 覆盖率门槛，不用新 skip 掩盖缺陷。测试只使用独立合成工作区。

## 文件所有权与执行次序

可并行：A 负责 data/scientific.py 及转换测试；B 负责统计实现与数值测试；
C 负责工作台/任务/目录/事务恢复及其测试。web/app.py、jobs/manager.py、catalog/sqlite.py
只能由 C 编辑；不要让“任务持久化”和“单实例约束”两个代理同时改这些文件。
根代理负责三项工程改进、整合、全套检查、真实浏览器与最终独立审查。
若统计需要新增共享 helper，应先与 A 约定所有权，不临时改对方文件。
工作台按所有权生命周期、终态保存、输出恢复的依赖顺序整合。

## 1. 数值与单位转换保真

Files：`src/cpdatakit/data/scientific.py`、`src/cpdatakit/data/units.py`（必要时）；
新增 `tests/test_conversion_numeric_fidelity.py`。保留既有 ScientificDataset/格式读写测试。

已复现：
- 对象列 `[2**53+1, None]` 经 dataset_to_scientific 再 scientific_to_dataset，
  第一个值由 9007199254740993 变成 9007199254740992。
- int64 数组行与 float64 数组行经 np.stack 类型提升，可出现同样的精度损失。
- 直接构建的 ScientificDataset 仅有标准 xarray `attrs["units"]="MPa"` 时，
  转表格可能丢失单位。NetCDF/Zarr reader 会补 metadata，不能笼统说所有文件路径丢单位。

- [ ] 把以下错误后果写成回归，先运行确认 RED：
```python
n = 2**53 + 1
source = Dataset(pd.DataFrame({"count": pd.Series([n, None], dtype=object)}))
back = scientific_to_dataset(dataset_to_scientific(source))
assert int(back.data["count"].iloc[0]) == n
value = ScientificDataset(xr.Dataset({"stress": ("record", [1.0, 2.0], {"units": "MPa"})}))
assert scientific_to_dataset(value).metadata["units"]["stress"] == "MPa"
```
- [ ] 扩展到 2**53±1、int64/uint64 边界、整数/浮点/缺失混合、嵌套数组、坐标单位、
  unit/units/metadata 一致与冲突，断言原对象未被修改。
- [ ] 最小实现精确保留或明确拒绝：内存 object 可容纳不代表后端可无损写出；
  必须覆盖真实格式往返，不能只用 allclose。冲突沿用 declared_unit 规则，不悄悄选源。
- [ ] 聚焦命令：`.venv/Scripts/python.exe -m pytest tests/test_conversion_numeric_fidelity.py tests/test_scientific_dataset.py tests/test_scientific_data_core.py tests/test_scientific_integrity.py -q`。

## 2. 科学摘要的精度、有限性和真实报告

Files：`application/scientific.py`、`statistics.py`，必要时增加共享数值 helper；
新增 `tests/test_summary_numeric_fidelity.py`。

已复现：int64 min/max 统一转 float 后少 1；float32 [1e8,1,-1e8] 的均值为 0，
对相同已存储值高精度累加应为 1/3；全有限 float32 [3e38,3e38] 均值为 inf。
父代理用真实 h5netcdf 文件调用 build_report：JSON 返回 internal_error 且不生成输出，
HTML 可生成，因此不能写成所有报告格式都会崩溃。

- [ ] 先为整数极值、抵消、溢出写 RED：
```python
s = summarize_scientific(
    ScientificDataset(xr.Dataset({"v": ("record", np.array([2**53 + 1], dtype="int64"))})),
    ValidationResult(),
)
assert isinstance(s["fields"]["v"]["max"], int)
assert s["fields"]["v"]["max"] == 2**53 + 1
```
- [ ] 用真实 NetCDF/Zarr 和 ReportRequest(format="json") 覆盖有限 float32 极值；
  补 float64 大同号/抵消、全缺失/空数组、真实非有限输入、JSON 报告与比较路径。
- [ ] 整数 min/max 保持精确 Python int；mean 可近似但计算必须稳定。
  不能简单把 inf 改成 None 隐去计算错误；仅改 float64 累加不足以覆盖所有 float64 极端值。
  v1 numeric_fields 结构及 R2 冲突单位/不可比较语义不变。
- [ ] 聚焦命令：`.venv/Scripts/python.exe -m pytest tests/test_summary_numeric_fidelity.py tests/test_normalization_statistics.py tests/test_scientific_report_units.py tests/test_comparison_v2.py tests/test_reporting.py -q`。

## 3. 任务终态持久化补偿

Files：`web/app.py`、`jobs/manager.py`、必要的 `catalog/sqlite.py` 与独立 helper；
新增 `tests/test_job_persistence_recovery.py`。

已复现：真实 report 完成回调注入一次 CatalogError，内存 succeeded、目录 queued、
产物已登记；移除故障后仅刷新项目不补写，重启详情为 failed/result=null，快照仍存在。
当前 GET /api/jobs/{id} 会重试同步，因此准确边界是没有后续详情轮询的窗口。

- [ ] 从临时探针提炼确定性 RED，保留“真实产物已生成、目录未保存”的断言。
- [ ] 增加独立于 HTTP 的有界重试及可观测待保存状态；shutdown 协调、未保存不得被
  内存保留策略先淘汰。重启可从完成证据恢复。
- [ ] 重试/回放幂等，不重复登记 artifact、不把成功终态降回 running；永久写失败必须可见。
  同时失去全部持久化介质时不能保证恢复，明确这一边界，不无限阻塞退出。
- [ ] 覆盖无浏览器轮询、短暂失败、持续失败、保存前退出、重启回放、容量上限与取消。
- [ ] 聚焦命令：`.venv/Scripts/python.exe -m pytest tests/test_job_persistence_recovery.py tests/test_jobs.py tests/test_job_retention.py tests/test_web_runtime_limits.py tests/test_web_active_jobs.py -q`。

## 4. 工作区实例所有权与生命周期

Files：由同一 C 编辑者修改 `web/app.py`、必要的锁 helper、`cli.py`；
新增 `tests/test_workspace_ownership.py`，保留原 CLI/ASGI 使用方式。

已复现：A 的真实 report 正在 running；B 对同一工作区请求该 job 的详情，
stored_job 仅凭本实例内存找不到任务，将共享目录写成 failed；A 仍 running。

- [ ] 先写同进程两个 app、两个真实进程争用同一工作区的 RED。
- [ ] 优先实现同工作区单实例约束；若采用多实例方案，必须有可验证的任务所有者。
  不把“本实例没有 job”当作“原所有者已死”。
- [ ] 明确服务生命周期：现 create_app 即创建目录/SQLite/JobManager，无 lifespan。
  在服务或首次操作前且早于遗留任务归类取得锁；正常关闭、启动失败和进程崩溃可靠释放。
  文件存在/PID 单独不足以证明所有权；考虑 PID 重用、路径别名和同进程多 app。
- [ ] 不在旧后台任务仍写文件时释放锁。保留 CLI、嵌入式 ASGI、TestClient 的可用关闭语义；
  不能为了测试通过删除现有重启或并发断言。
- [ ] 验证第二实例不能改写/取消存活任务，真正重启可以识别遗留记录。
- [ ] 聚焦命令：`.venv/Scripts/python.exe -m pytest tests/test_workspace_ownership.py tests/test_cli_ui.py tests/test_web_active_jobs.py tests/test_web_runtime_limits.py -q`。

## 5. 输出进程崩溃后的可发现恢复

Files：同一 C 编辑者负责 `web/outputs.py`、`web/artifacts.py`、恢复 helper/API；
新增 `tests/test_output_crash_recovery.py`，恢复入口以用户可理解的项目清单呈现。

已复现：独立子进程执行 report(force=true)，在快照复制后、register_artifact 前
os._exit(23)。重启：新公开输出和旧 previous 备份仍存在，artifact 登记为 0、
recovery.json 为 0、job failed/result=null。没有证据说旧文件已被删除。

- [ ] 把真实子进程退出探针转为 RED，覆盖备份/发布前后、登记前后边界。
- [ ] 在不可逆步骤前原子写入事务 ID、阶段、目标、旧备份、本次输出身份及摘要；
  明确必要 flush/fsync。只承诺经验证的进程崩溃恢复，不凭本机测试承诺断电一致性。
- [ ] 重启提供只读恢复清单；区分旧输出、已生成未登记输出和未知归属。
  恢复动作幂等、项目隔离、路径受限；复核后恢复到未占用位置。
- [ ] 目标被替换/修改、清单损坏、归属不明时保留全部证据，禁止自动按路径删除或覆盖。
  继续遵守 R1 私有随机暂存/隔离路径独占的可信边界。
- [ ] 聚焦命令：`.venv/Scripts/python.exe -m pytest tests/test_output_crash_recovery.py tests/test_web_output_transactions.py tests/test_web_review_recovery.py tests/test_artifact_versions.py tests/test_upload_ownership.py tests/test_batch_recovery.py -q`。

## 6. 五项完成后继续的三个工程改进

- [ ] **能力检测。** capabilities.py 的 local-ui 把只在 dev 中声明的 httpx 当运行必需；
  独立进程屏蔽 httpx 时，create_app/health 正常而 available=false 已复现。
  先 RED，再修检测契约；用真实纯运行依赖的干净 wheel 环境验证，不能只做 import mock。
- [ ] **Zarr 目录清点。** 一次 inspect_input 被实测调用 3 次根 rglob，
  合成 20 元素/chunk2 存储每次遍历 14 项。合并大小、数量、链接检查；
  保留超限提前退出、符号链接拒绝、数字 inspect 零数组载荷；记录实际访问次数，
  不承诺未测的耗时倍数。给独立测试文件，先复现失败再实现。
- [ ] **真实浏览器闭环。** 保留现有 Node harness，新增安装后 wheel 上的
  上传→schema→验证→转换→报告→下载→重新读回，并覆盖一次错误提示。
  在 CI 配置必要浏览器环境及失败证据；以事件/定位等待，避免固定 sleep。
  本地真实执行与托管 CI 结果分开报告；本轮没有新的 push/PR 授权。

## 调查探针与基线记录

探针只作线索，实施时必须重写为真正的回归测试。系统 TEMP：
- cpdatakit-workbench-audit-20260919.py：多实例与终态保存失败。
- cpdatakit-output-crash-audit-20260919.py：真实进程异常退出。
- 科学反例可按本文件直接重建；JSON 失败使用 float32 [3e38,3e38]、
  units="MPa"、schema 2.0 的 stress(record) float measured_field，真实 NetCDF 输入。

原验收参照：
`docs/verification/2026-09-16-reviewed-optimizations.md`，
`docs/verification/2026-09-16-coordinate-reads.json`。
9/16 的 1055 passed、89.27% 仅是历史基线，不是新修改的验收结果。
父代理9/19仅运行合成调查探针，没有重新跑完整测试，也没有改生产代码。

## 完成标准与授权外事项

- [ ] 各项 RED→GREEN，保持原回归；独立子代理审查最终改动并修复确认问题。
- [ ] 完整 `pytest --cov=cpdatakit --cov-report=term-missing --cov-fail-under=85`，
  `ruff check .`、`ruff format --check .`、`git diff --check`。
- [ ] wheel/sdist 构建、发行元数据、Twine、可复现构建；独立干净安装、CLI 和实际 HTTP。
  使用新输出目录，不清理旧用户构建或工作台数据。
- [ ] 所需真实浏览器、后端读取、跨进程所有权与异常退出证据全部记录；
  未执行平台/依赖组合明确标注，不把中间结果或工具超时写成通过。
- [ ] 更新 CHANGELOG 的 Unreleased 和本轮 verification 文档，保留本文件；
  中文总结五项、三项工程改进、验证结果和剩余边界。
- [ ] 仅当本地实施/验收完成后报告待授权的提交、PR、CI及发布步骤。
  不自动处理 pandas PR #42、CodeQL PR #43，不更改 pandas 范围或发布版本。
  不引入新适配器、网格拓扑、完整依赖 extras 拆分或真实求解器/训练工作。

独立交接审查已确认范围可实施，并要求上述数值往返、稳定统计、持久化重试、
锁生命周期、崩溃证据与工程验收六项约束；这不是本轮实现已通过审查的声明。
