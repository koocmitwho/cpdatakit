> 历史记录：以下内容记录 2026-10-01 的发布与验收，原先仅保存在本地，于 2026-10-13 补入仓库。文中的“本轮”“当前”和测试结果均指当时的版本与检查，不代表 v0.12.0 或本次文档更新重新执行了这些验证。

# CPDataKit 中英文说明发布核验 — 2026-10-01

状态：两份 README 已合并到 GitHub 默认分支；默认分支内容与最终提交 CI 已全部核验，未发现阻碍。
此文件为本地发布收据，未包含在文档 PR 中。

## 发布范围

- [PR #50](https://github.com/koocmitwho/cpdatakit/pull/50)：先以 draft 创建，审核与 24 项检查通过后转 ready，按正常保护流程 squash 合并。
- 文档分支提交：`dac348bce8e4ace9eb0c14c7e0ad3849e773392b`。
- 默认分支提交：[f26a97584e6dc8eee415580847a7281ce76c7508](https://github.com/koocmitwho/cpdatakit/commit/f26a97584e6dc8eee415580847a7281ce76c7508)，合并时间 `2026-10-01T05:03:16Z`。
- 仅发布 [英文 README](https://github.com/koocmitwho/cpdatakit/blob/main/README.md) 和 [中文 README](https://github.com/koocmitwho/cpdatakit/blob/main/README.zh-CN.md)。
- 以用户认可的中文稿为结构和事实基准重写英文；中文保留定位与正文，补上原有徽章，并恢复此前就有的 Python 3.10/3.11 与 v0.5.x 兼容线说明。
- 软件版本仍为 v0.10.0；应用代码、依赖、工作流、许可证、既有标签及发布资产均未改变。

## 内容与本地审核

两种语言的 5 个示例代码块逐字一致，21 个相对链接目标均存在；共同安装版本、CSV 参数、列声明、单位、预期数值、限制、历史 CI 指标和实际中文界面按钮均已核对。
保留原有 CI、Release、PyPI、License 徽章和双向语言入口。英文明确说明工作台界面为中文。

首次本地全量测试为 1556 passed、2 failed、1 skipped、40 warnings；两项失败均为重写遗漏历史 Python 兼容说明的文档断言。恢复原有说明后，18 项项目/版本元数据测试通过。
Ruff、格式检查、wheel/sdist 构建及 v0.10.0 版本一致性检查通过。后续 PR 完整 CI 已重新验证最终文档提交。

中文稿交付时新环境的 PyPI 安装、依赖检查、CLI/API 和 CSV 示例共 16 项检查均退出 0；安装 wheel 哈希与正式 v0.10.0 一致。新环境 Chromium 三行 CSV 流程通过，来源字节不变，数值最大绝对读回差为 0，服务退出 0。
英文沿用相同示例代码块，未扩大该验证范围。

## PR 检查

- [CI](https://github.com/koocmitwho/cpdatakit/actions/runs/36817205550)：10/10 jobs success；Windows/Linux/macOS × Python 3.12/3.13 六套完整测试各 1559 passed、40 warnings，安装、构建和浏览器检查通过。
- [依赖兼容矩阵](https://github.com/koocmitwho/cpdatakit/actions/runs/36817205561)：12/12 jobs success。
- [CodeQL](https://github.com/koocmitwho/cpdatakit/actions/runs/36817205549)：success。
- 合并前确切 head 与两份文件范围再次核对；全部 24 个 PR 检查通过，未使用强推或管理员绕过。

## 默认分支与资产核对

GitHub 内容 API 返回的两份默认分支 README 字节与审核提交一致；默认分支合并树与文档分支审核树相同。
相对 v0.10.0 发布提交 `7ef4ece92707c7faf61baa032870012517d398d1`，最终提交仅改变两份 README。

远端 v0.10.0 标签对象仍为 `712788c59642faf0707688938c90f628031e0ba0`，仍指向上述软件发布提交。
发布记录及资产 ID、名称、大小、摘要和更新时间前后相同。本次未创建软件版本、标签或包发布。
原工作树的 43 个候选文件及先前发布收据共 44 个文件的 SHA-256 均保持一致。

合并提交的新检查：

- [CI](https://github.com/koocmitwho/cpdatakit/actions/runs/36817967858)：10/10 jobs success；六套完整测试各 1559 passed、40 warnings。构建、干净安装、浏览器与 85% 覆盖率门禁均通过。
- [依赖兼容矩阵](https://github.com/koocmitwho/cpdatakit/actions/runs/36817967844)：12/12 jobs success，含 Windows Python 3.12/latest。
- [CodeQL](https://github.com/koocmitwho/cpdatakit/actions/runs/36817967882)：success。最终提交全部 23 个检查均已完成且成功。

## 验证边界

本轮是 GitHub 文档更新；PyPI 展示文档及已发布软件包仍属于原 v0.10.0 发布内容。
README 中真实 IN718 全量验收数字和既有发布 CI 链接明确属于此前软件发布；本次没有重跑该原始档案。
本机没有执行 macOS/Linux 的首页 shell 命令；这些平台的包安装、应用与示例路径由 CI 覆盖。
NetCDF3/日期坐标兼容问题保持原有边界；规则检查和数值换算通过不构成实验物理正确性证明。

本轮支持证据保存在文档工作树忽略目录 `.artifacts/bilingual-docs-publish-20261001/`。
