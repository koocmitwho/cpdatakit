# KupferDigital → FE training data → CPDataKit

这个示例把 experiment-to-CPFE v0.1.0 的 CuSn8Ni2 拉伸结果转换为 CPDataKit 数据集。
`reference/` 中有 483 条试验派生数据和 729 条已有 FE 训练数据，以及原划分和训练配置。
运行后得到 `experiments.h5`、`training.h5`、各自的 schema JSON 和验证清单。

在当前 CPDataKit 开发目录安装后运行：

```python
from pathlib import Path
from cpdatakit.application import integrate_tensile_bundle

result = integrate_tensile_bundle(
    Path("examples/cpfe-tensile/reference"),
    Path(".artifacts/tensile-integration"),
)
assert result.ok, result.to_dict()
```

请将输出路径设为尚不存在的新目录。写出前会检查输入哈希、字段、单位、数组长度、分组和应变窗口。
实验数据沿用原来的用途：H_08 用于校准，H_16 用于模型检查，H_18 用作最终留出。
训练标签来自既有 FE 输出，同一参数案例的全部记录仍归入同一个训练、验证或测试集合。
转换过程保留已有数值和物理标签，不运行 Abaqus 或重新训练网络。

## 来源和转换

原始数据为 Hossein Beygi Nasrabadi、Felix Bauer、Ladji Tikana、Patrick Uhlemann、
Steffen Thärig、Birgit Rehmer 和 Birgit Skrotzki 发布的
*KupferDigital mechanical testing datasets: Brinell hardness, Vickers hardness, and tensile tests*，
version 2，[DOI 10.5281/zenodo.10820299](https://doi.org/10.5281/zenodo.10820299)，
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)。

试验预处理减去首个应力应变数据点，在递增加载段内选取 0–0.8% 工程应变并插值到
161 点。应力是从预载起点计算的名义应力增量，单位 MPa。FE 使用同一窗口内的小应变、
均匀方形标距段和各向同性双线性塑性模型。训练数据将各 FE 曲线插值到 81 点，输入为
工程应变、弹性模量、屈服应力和塑性模量，标签为顶面 RF3 合力除以初始面积。

上游发布：
[experiment-to-CPFE v0.1.0](https://github.com/17636365690/experiment-to-cpfe-pipeline/releases/tag/v0.1.0)，
提交 `47dc839f29de1cb3b5570a04fefbb8dc82050c71`。
`reference/manifest.json` 记录各输入哈希、原始 LIS 文件名和哈希、12 个案例的 HDF5/ODB
哈希及已有轴向检查结果。2026-09-07 接入时，逐项核对本地既有运行记录，共 84 个阶段。
`upstream-summary.json` 保留该发布版本的原始摘要。

`reference/` 中的资料按 CC-BY-4.0 交付，保留以上署名、许可和处理说明。
程序代码按本项目 Apache-2.0 许可交付。原始 ODB 和商业求解器不在此示例中分发。

这是自有仓库之间的集成案例。离线运行可复核随附输入及 CPDataKit 转换结果，原始 ODB
哈希记录接入时的来源核对。物理验证结论沿用上游结果，完整试样、晶体塑性或更大
应变范围需另行验证。
