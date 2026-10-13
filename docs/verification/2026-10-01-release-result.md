> 历史记录：以下内容记录 2026-10-01 的发布与验收，原先仅保存在本地，于 2026-10-13 补入仓库。文中的“本轮”“当前”和测试结果均指当时的版本与检查，不代表 v0.12.0 或本次文档更新重新执行了这些验证。

# CPDataKit 审核与发布结果 — 2026-10-01

状态：**已正式发布并完成公开后核验；没有剩余发布阻塞。**

- [GitHub Release v0.10.0](https://github.com/koocmitwho/cpdatakit/releases/tag/v0.10.0)
- [PyPI 0.10.0](https://pypi.org/project/cpdatakit/0.10.0/)
- [已合并 PR #49](https://github.com/koocmitwho/cpdatakit/pull/49)
- 发布提交：[`7ef4ece92707c7faf61baa032870012517d398d1`](https://github.com/koocmitwho/cpdatakit/commit/7ef4ece92707c7faf61baa032870012517d398d1)
- 原审核提交：`eee497039b521b439b11bc86904106e584a47ff0`，分支 `codex/release-v0100-20261001`。
- 远端 annotated tag：`v0.10.0`，tag object `712788c59642faf0707688938c90f628031e0ba0`，peeled commit 为上述发布提交。
- GitHub Release 时间：2026-10-01 03:58:44 UTC，正式版，非 draft/prerelease。

## 版本、范围与保全

v0.9.3 已存在于 GitHub/PyPI。新增 CSV 预览与逐列字段/类型/单位/用途确认、原字节及声明保留、转换结果复用属于公开功能增加，采用 minor 版本 **0.10.0**。HDF5 1.0 合约、Python >=3.12、运行依赖范围和 Apache-2.0 许可没有变更。

入场时原工作树有 43 个已修改/未跟踪候选文件，全部逐字节匹配昨晚 `delivery-manifest.json`。本轮在原工作树的 `.artifacts/release-review-20261001/` 保存了完整文件快照、SHA-256 和 tracked diff；只将清单中的文件复制到独立工作树。提交总共 51 个文件，额外范围为版本、当前安装文档、正式说明和本轮审核记录。本轮修正没有扩展产品功能。

最终再次核验：原 43 个文件的 SHA-256 全部未变；原分支 `codex/csv-release-readiness-20260930`、HEAD `f2cd9e5648f272287065f3fe20e2391b49bd5ca6` 和未暂存状态保留。新增此结果报告是本轮明确要求的报告写入。独立审核工作树的 tracked 状态干净；其他项目/工作树没有改动。没有强推、覆盖标签、清理未提交内容、创建凭据、修改全局 Git/网络配置或变更分支/发布保护。

旧 origin 和授权的 `koocmitwho/cpdatakit` 地址经 GitHub 仓库 ID 1331649261 确认为同一仓库，原 remote 配置保留。通过既有受保护 PR 流程 squash 合并；远端最终提交与审核提交的完整 Git 树完全一致。

## 新鲜审核与终态 CI

本轮核查了 CSV 精度/溢出/缺失值拒绝、显式单位、原字节保留、数据/规则事务、CSRF、项目边界、快照链接/哈希/原规则、重复复用、过期异步响应和 HDF5 列序。Ruff、格式、JS 语法、release metadata、twine、diff checks 均通过。

| 证据 | 本轮结果 |
|---|---|
| 本机 Windows / Python 3.12.13 / Node 24.18.0 全量 | 1558 passed、1 skipped、40 warnings，249.70 秒 |
| 本机覆盖率 | 90.172048%，7652/8486，门槛 85% |
| 必要版本/文档/打包修正后相关测试 | 20 passed；质量/格式/元数据重新通过 |
| PR CI / 依赖矩阵 / CodeQL | 10/10、12/12、CodeQL success，均对应 eee4970 |
| 最终 main CI | **10/10 success**；Ubuntu、macOS、Windows × Python 3.12/3.13 六项全量各 **1559 passed、0 skipped、40 warnings** |
| 最终 Ubuntu CI 覆盖率 | **89.91%**，门槛 85%；与本机结果按环境分开记录 |
| 最终依赖矩阵 | **12/12 success**，三个 OS × Python 3.12/3.13 × lower/latest，安装候选 wheel 并执行全量/覆盖率及 probe/smoke |
| 重点最终 Windows / Python 3.12 / latest | JUnit：1559 tests、0 errors、0 failures、0 skipped；Python 3.12.10 / Windows Server 2025；安装 cpdatakit 0.10.0，351.262 秒 |
| 最终 CodeQL | success |
| 既有 PyPI 发布任务 | build/publish 两项 success，已到终态 |

[最终主 CI](https://github.com/koocmitwho/cpdatakit/actions/runs/36811526428)、[最终依赖矩阵](https://github.com/koocmitwho/cpdatakit/actions/runs/36811526427)、[最终 CodeQL](https://github.com/koocmitwho/cpdatakit/actions/runs/36811526433)、[PyPI 发布](https://github.com/koocmitwho/cpdatakit/actions/runs/36812294795) 全部明确对应 `7ef4ece92707c7faf61baa032870012517d398d1`，没有使用旧提交/旧 CI 代替本轮通过。

本机唯一 skip 为既有符号链接用例；远端该用例实际运行并通过。保留了 15 秒 Node 进程限制和断言，没有通过增加 timeout、跳过或移除断言获得通过。历史六项 Node 超时的具体资源根因仍未确定；本轮及最终同平台矩阵通过是新的候选结果。

## 分发、许可证和隐私

第一份 sdist 检查发现历史 `docs/superpowers/plans/2026-08-31-post-v0.3.0-next-steps.md` 含本机绝对路径。在独立发布树中从 sdist 排除该确切文件后重新双重构建，可复现性、版本一致性、twine 及成员检查全部通过；旧仓库历史记录保留。既有 Apache-2.0 LICENSE/NOTICE 在 wheel/sdist 中均存在。直接运行依赖的许可证元数据/随附许可证已经本机核对，依赖未 vendored 进 CPDataKit。

公开前实际发布工作流产物逐成员与 tagged Git blob 核对；公开后从 PyPI 及 GitHub 分别回下载，两个渠道和工作流产物的字节完全一致。wheel 88 个成员、sdist 464 个成员；检查没有带入 `.artifacts`、虚拟环境、浏览器证据、raw IN718、私有数据/权重、凭据或本机绝对路径。

| 公开文件 | 大小（bytes） | SHA-256 |
|---|---:|---|
| cpdatakit-0.10.0-py3-none-any.whl | 217146 | `1aac6cb85ef1119cffc54e096e27e0c09675a65210ebdaa35b90b4648badfa32` |
| cpdatakit-0.10.0.tar.gz | 1074122 | `823dbd50e65526aaf2bc35e215f0fb16ef3b3e52579a9a3d914829a777859aa8` |

来源许可经实时 [DataCite 元数据](https://api.datacite.org/dois/10.17632/nx55jj48rx.2) 复核为 CC BY 4.0。IN718 压缩包只作为本机只读验收输入，固定 SHA-256 `8bd49396f5f78f255dadf41fb1ec1061cfe0b5d27cb3d7027c3ea61bc0a235db`；包内只含其来源指纹与三行合成 CSV，真实压缩包没有提交或上传。

在最终全部 CI 通过、标签和 PyPI 版本无重复后推送新标签。既有 `pypi` 环境要求 `koocmitwho` 审核，当前连接身份为该已配置审核者，`current_user_can_approve=true`、`prevent_self_review=false`；先检查实际 build 产物后按本轮授权正常审核，保留该保护，使用既有 OIDC publisher。GitHub Release 仅上传同一两个发行包。

## 公开后的真实安装和 CSV

新建独立 Python 3.12.10 环境，通过普通 `pip install cpdatakit==0.10.0` 安装实际 PyPI 包。pip report 的 cpdatakit wheel hash 与上述公开 hash 完全一致；模块位于该环境 site-packages，无 httpx 开发依赖。`pip check`、版本和 HDF5 API、18 条 CLI/API、确定性 CSV/JSON 样本逐字节比较、三行 CSV 原字节及 JSON/HTML 报告、thermal-cycle 验证/转换/绘图均通过。

公开包再运行 IN718 验收：**12 CSV / 24,073 行**，原 archive 未改动，原字节、列序、单位、独立数值对照、报告全部通过。Chromium 153.0.8010.12 / Playwright 1.63.0 再运行真实 **1,865 行** CSV：错误输入拒绝、字段/单位确认、导入、报告、原始文件下载、转换结果复用、重复登记、刷新和普通上传状态清理通过。读回最大绝对差 `8.881784197001252e-16 kN`，容差 `rtol=1e-15, atol=1e-12`；报告有效，测试服务 exit 0。

公开前的合成 CSV Chromium 和原有上传/转换/报告/下载路径也已通过。100,000/1,000,000 行 HDF5 full/selected/chunked 诊断均返回精确行数；Windows peak RSS 不可用。

## 证据位置与边界

本轮全部本机/远端日志、JUnit、安装 report、包成员/哈希、浏览器 trace/下载和终态摘要保存在独立 `cpdatakit-release-20261001/.artifacts/release-review-20261001/`，其中：

- `intake` 快照位于原项目 `.artifacts/release-review-20261001/`。
- `pytest.txt`、`tests.xml`、`coverage.json`：本机全量；`final-test-summary.json`：最终六项远端完整计数。
- `main-ci.json/.log`、`main-matrix.json/.log`、`main-codeql.json`、`publish-run.json`、`publish.log`：确切提交的终态证据。
- `final-win312-latest/`：最终重点单元的安装环境、覆盖率、JUnit 和 smoke。
- `publisher-artifact-audit.json`、`published-distribution-audit.json`、`publish-artifacts/`、`pypi-download/`、`github-release-download/`：实际分发一致性及隐私/许可。
- `published-install.json`、`published-cli-smoke.json`、`published-real-csv/acceptance.json`、`published-browser-in718/evidence.json`：真实公开包安装/CSV验收。
- `remote-tag.json`、`github-release.json`、`terminal-workflow-summary.json`、`release-state.json`：远端发布与本轮最终状态。

NetCDF3/日期坐标历史限制没有在本轮修复或重验。CSV 校验验证声明合约与数值转换，不证明实验的物理有效性。最终哈希检查不锁定外部进程后续写入。GitHub 自动生成源码归档未单独逐成员核验；包一致性核验覆盖上述两个实际上传的 wheel/sdist。旧公开 Git 源树中的历史计划记录仍保留。本轮没有剩余发布阻塞。
