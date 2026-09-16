# 2026-09-16 R1–R5 本地实施与验收

本轮从 `2ce41c404c6228820bb190b5d41fb3684dfa6e7d`、
`codex/20260916-reliability-followup` 实施，版本元数据仍为 0.8.1。
构建产物来自未提交工作树，不是新发行版。本轮没有 commit、push、PR、merge、tag 或发布。
初始两份计划/审查文档保留，未操作其他 worktree。

## 实现与 TDD

| 项目 | 实现与错误后果回归 |
| --- | --- |
| R1 | 热图复用无覆盖发布；上传在私有暂存捕获身份及摘要，登记前后复核；回滚隔离后二次复核，不符保留内容和恢复记录。新增 43 例；先后复现 10、13、4 个失败，扩大既有回归 178 passed。 |
| R2 | 报告使用统一有效单位；冲突保留 validation.errors、unit=None，受影响数值及坐标依赖不比较。42 个真实 NetCDF/Zarr 用例先 20 failed；统一解析后另暴露 2 个 null-unit 标量坐标比较失败，修复后扩大回归 95 passed。 |
| R3 | 延迟坐标索引和 CF 解码，位置选择后物化；三后端使用实际返回数组/存储块计数。首轮读取边界分别复现 12、6、3 个失败；纳秒精度/cftime 12 个、空 CFTimeIndex 6 个、重复字符拼接 4 个、时间边界属性继承 6 个失败均修复。新增 96 例，扩大回归 258 passed。读取结果见单独 JSON。 |
| R4 | 当前实例任务摘要独立于分页历史；重启遗留 running 不自动轮询。API/HTML 先 2 failed，前端两类行为各先失败；独立审查再补旧刷新响应覆盖终态和终态未持久化两个确定性屏障回归。历史与活动按 ID 去重，当前终态覆盖落后的目录摘要。 |
| R5 | quickstart 和当前多维使用入口改为 0.8.1；修正编号和维护状态。离线白名单含两份 README、quickstart、maintenance、roadmap、post-v07-workflows；18 个陈旧 pin/wheel URL 用例分别先失败再修复，历史专用文档保留旧版本。 |

既有后台 I/O 回归仅迁移到新上传 helper 的故障注入入口，健康请求并发和输出内容断言保留。
没有降低 85% 覆盖率门槛，没有新增跳过项。

## 独立审查

两名未参与实现的代理分别检查规格及工程正确性。首轮发现并复现：

- R3 的 int64 纳秒时间在提前 mask/scale 时转 float64，损失精度。
- R3 选择后按子集自动推断可能改变原全轴的 CFTimeIndex 类型。
- R4 的 worker 已结束而目录回调尚未写入时，页面可能停留在 queued/running。

另一次独立 R4/R5 审查用 Promise 屏障复现旧活动快照覆盖终态。
这些问题均先补失败回归，再修改实现；特殊 CF 全轴类型的兼容限制须单独列明。

最终规格复核独立运行科学回归 126 passed、任务/前端/入口回归 40 passed，确认纳秒
精度和任务生命周期问题已关闭。工程复核独立运行相关回归 101 passed，最后的字符及
时间边界属性两组增量再跑 12 passed，结论通过且保留下述 CF 类型范围限制。

## 真实浏览器

先在源码环境、再在最终 wheel 的全新安装环境中，分别通过独立合成工作区、
实际 Uvicorn HTTP 服务和 Codex 内置浏览器操作：

1. 提交真实 report，将其执行暂停在可控屏障，随后登记 61 条较新的终态历史。
2. 新页面和刷新均显示该长任务及 Cancel；加载第二页后仍仅显示一条。
3. 点击 Cancel，释放屏障后看到 cancelled 和 View details；后台日志不再出现该任务的持续轮询。
4. 再刷新并加载历史，可找到 cancelled 记录。历史结果详情保持按需获取。

合成样例和服务器日志保留在本机独立临时验收目录，未使用用户工作台数据。
完成回调延迟和旧响应乱序使用自动回归中的确定性 Event/Promise 屏障验证。

## 环境与边界

- 实际运行：Windows 11 build 26200，Python 3.12.10；没有运行 Linux/macOS、Python 3.13 或完整依赖上下界矩阵。
- R1 的随机私有暂存/隔离路径由本次操作独占；不防御同权限进程主动扫描篡改这些路径或持有旧句柄继续写入，不提供原子比较删除。
- R3 证据统计真实后端返回的元素/块缓冲区，不代表操作系统物理磁盘 I/O。Zarr 实际读取受块粒度约束。
- 时间自动索引类型依赖全轴取值。任意非单调轴跨出 datetime64 范围时，严格只读子集与完全重现全轴自动类型不能同时保证；需要全轴类型的调用方应完整加载后再 `.isel`。具体有界探测和兼容范围见 [选择性读取](../selective-reading.md)。

## 最终执行结果

| 检查 | 实际结果 |
| --- | --- |
| `pytest --cov=cpdatakit --cov-report=term-missing --cov-fail-under=85` | **1055 passed、1 skipped、37 warnings，89.27%**，115.46 秒；跳过项是既有 Windows 符号链接权限测试。 |
| `ruff check .` / `ruff format --check .` | 通过；252 个文件格式一致。 |
| `git diff --check` | 通过。 |
| `python -m build` | 隔离构建 wheel 和 sdist 成功，构建依赖 hatchling 1.32.0。 |
| `check_release.py v0.8.1 --dist-dir ...` / `twine check` | wheel/sdist 版本及发行元数据一致，两项描述元数据检查通过。 |
| 全新 venv 安装 | `include-system-site-packages=false`，运行依赖经 pip 独立解析安装；下载 URL 来自 `files.pythonhosted.org`（部分命中 pip 缓存）。随后安装本轮 wheel，`pip check` 通过，导入路径确认位于该环境 `Lib/site-packages/cpdatakit`。 |
| 独立安装包回归 | 对安装包执行 R1/R2/R3/R4 四组新回归，**187 passed、30 warnings**，25.60 秒。测试工具 pytest/httpx 单独安装；没有依赖源码 editable 安装或复用源码环境的 site-packages。 |
| quickstart | 全新目录运行样例生成、版本、validate、summary、convert、inspect、report、plot、UI help 共 9 条命令，通过并核验输出非空。另实际启动 CLI `ui --no-browser`，`/health` 和主页 HTTP 200。 |
| 实际 HTTP smoke | 独立安装包读取 61 行 legacy HDF5；`/health`、主页、app.js、fields.js、authoring.js、style.css 均 HTTP 200。 |
| 实际浏览器 | 最终安装包的刷新、历史分页、单行去重、取消、cancelled 终态与历史可达性均完成实际操作。 |

37 条完整测试警告由 24 条刻意覆盖古日期范围的 cftime 提示、10 条 netCDF4/NumPy
弃用提示、2 条合成 S1 字符夹具的 Zarr 规格提示及 1 条 python-multipart 弃用提示组成。
没有隐藏这些警告或以新增 skip 绕过测试。

源码环境关键依赖：NumPy 2.5.3、xarray 2026.7.0、h5py 3.16.0、h5netcdf 1.8.1、
netCDF4 1.7.4、Zarr 3.3.0。全新安装采用同一组 NumPy/xarray/HDF 版本，Zarr 为 3.4.0，
FastAPI 0.141.1、Uvicorn 0.53.0；这只是本次真实安装组合，不代表整个允许范围已穷尽验证。

## 读取证据与本地归档

实际记录：[2026-09-16-coordinate-reads.json](2026-09-16-coordinate-reads.json)。
在 10,000 行、Zarr 块长 128 的合成样例中：

- 数字单条读取：两个 NetCDF 引擎各 1 个坐标值 + 1 个变量值，共 16 字节；Zarr 各读取目标一块。
- CF 时间单条读取：NetCDF 另读两个类型探测端点，总计坐标 24 字节 + 变量 8 字节；
  Zarr 读首尾及目标三个坐标块，实测坐标缓冲区共 412 字节，变量块 174 字节。
- 数字 inspect，以及数字/时间因记录限额拒绝：没有数组载荷读取。
- 纳秒精度六个后端/context 组合均保留原始整数对应的时间及 NaT。

最终构建与日志保存在仓库忽略目录 `.artifacts/reliability-20260916/`，
其中 `build-check.log` 记录两份 wheel/sdist 的逐字节对照、元数据和 Twine 校验结果；
`pytest-accepted.log`、`installed-regressions.log`、`installed-http-smoke.json` 和
`verification-summary.json` 保留本次原始执行结果与产物哈希。该目录不是待提交资产。
