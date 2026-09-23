# 论文 PDF 库

> 下载日期：2026-09-21　共 **13 份**，全部为**合法开放获取**来源（arXiv / IJCAI / LIPIcs / PATAT / 机构仓储 / 作者主页）。
> 每份文件都已校验为有效 PDF，页数如下。**未使用任何盗版渠道**；付费墙文献见 `../待获取-付费墙清单.md`。

---

## 核心推荐（按重要性排序）

| 文件 | 页数 | 来源 | 为什么重要 |
|---|---|---|---|
| `Cooper-Kingston-1996-Complexity-of-Timetable-Construction.pdf` | 12 | 作者主页 | **证明排课 NP-complete**，且点名"要求连堂""要求周内均匀分布"——正是我们的 H6/H15 |
| `Demirovic-Musliu-MaxSAT-LNS-HighSchool-Timetabling.pdf` | 22 | TU Wien | 高中排课的 MaxSAT 大邻域搜索，**工程实现细节丰富** |
| `Kristiansen-Stidsen-2013-Educational-Timetabling-Survey.pdf` | 73 | DTU 仓储 | **最全的综述**，四大类问题 + 基准数据 + 方法对比 |
| `Ceschia-DiGaspero-Schaerf-2023-Educational-Timetabling-Survey.pdf` | 26 | arXiv 预印本 | **最新综述**（EJOR 期刊版），含 state-of-the-art 结果 |
| `Dubois-2026-CP-HalfBlocks-HighSchool.pdf` | 17 | LIPIcs (CP 2026) | 真实加拿大高中数据，**10 小时 → 1 小时** |
| `Post-2014-XHSTT-XML-Archive.pdf` | 7 | Springer 开放获取 | **国际标准格式** + 评估器设计，`V=λ·f(D)` 代价公式 |
| `Demirovic-2017-SAT-Based-Approaches-HSTT.pdf` | 2 | IJCAI 2017 | ⚠️ **仅 2 页**，应为短文/extended abstract，非全文 |

## 其他已下载

| 文件 | 页数 | 来源 | 说明 |
|---|---|---|---|
| `Demirovic-Musliu-MaxSAT-LNS-HighSchool-Timetabling.pdf` | 22 | TU Wien | MaxSAT + 大邻域搜索解高中排课 |
| `PATAT2012-Third-International-Timetabling-Competition.pdf` | 6 | PATAT 官网 | ITC2011 竞赛说明（高中排课赛道） |
| `PATAT2024-Proceedings.pdf` | **396** | PATAT 2024 官网 | **整本会议论文集**，含大量最新排课论文，可按目录检索 |
| `IJCAI2015-Diverse-Solutions-CSP.pdf` | 7 | IJCAI | **多解多样性**的经典表述（对应 3–8 套方案） |
| `IJCAI2022-Explaining-SoftGoal-Conflicts.pdf` | 7 | IJCAI | 通过**约束放宽**解释冲突（对应无解诊断） |
| `arXiv2204.03429-Counterfactual-Explanations-Relaxations.pdf` | 6 | arXiv | **反事实解释**："如果放宽 X 会怎样" |
| `OAJAST2024-Systematic-Review-Metaheuristics-Timetabling.pdf` | 8 | OAJAST | 启发式方法系统综述（了解 CP 之外的路线） |

---

## 使用提示

- **`PATAT2024-Proceedings.pdf` 是整本论文集（396 页）**，建议先看目录再定位；里面很可能有比上述更贴题的论文。
- **Ceschia 2023 是 arXiv 预印本**，与 EJOR 期刊版可能有细微差异，正式引用请以期刊版为准（DOI: 10.1016/j.ejor.2022.07.011）。
- **`Demirovic-2017` 只有 2 页**——如果你需要该工作的完整细节，看同目录的 `Demirovic-Musliu-MaxSAT-LNS-HighSchool-Timetabling.pdf`（22 页，同一研究线的完整论文）。
- **最想要但没拿到的是 Demirović & Stuckey 2018（CPAIOR, hot starts）**——这篇与我们的技术路线最匹配，但 Springer 付费墙。获取方式见 `../待获取-付费墙清单.md`。

## 引用格式

需要正式引用时，DOI 与完整出处见：
- `../01-综述与经典理论.md`
- `../02-CP-SAT建模实践.md`
- `../04-多解与无解诊断.md`
- `../05-数据格式与术语标准.md`
