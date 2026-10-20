# ADR 0006：质量评测采用「确定性指标 + LLM-as-Judge」双轨

- **状态**：已接受
- **日期**：1.0.0 发布前
- **决策者**：算法 / 评测
- **相关文件**：`reelagent/agents/evaluator.py`、`reelagent/eval/consistency.py`、`reelagent/llm/prompts.py`、`reelagent/utils.py`

---

## 背景

生成式产品的核心工程问题是：**「这次改动让成片变好了还是变差了？」**

这个问题必须被回答，而且是**自动**回答，理由有三个：

1. **迭代频率高**。prompt、一致性策略、路由规则、模型版本
   都在持续变动。如果每次改动都要人肉看成片，迭代速度会塌陷。
2. **人肉评估不可靠**。同一支片子看第二遍的评价会变；
   不同人的标准不一致；看十支片子之后就疲劳了。
3. **成本敏感**。视频生成是「贵调用」，一次改动如果引入回归，
   意味着持续的额外供应商支出。

所以需要一套能进 CI 的评测门禁。但「一支片子好不好」是一个
多维度的、部分主观的判断，包含至少这几个面：

| 维度 | 例子 |
|---|---|
| 叙事 | 故事讲通了吗？镜头之间有因果吗？ |
| 视觉一致性 | 角色还是同一个人吗？场景是同一个地方吗？ |
| 节奏 | 景别与时长有变化吗？还是十个中景镜头各 3 秒？ |
| 编排正确性 | 我们的一致性约束真的被传到生成请求里了吗？ |
| 忠实度 | 成片有没有偏离用户给的素材？ |

这几类问题的**可计算性完全不同**：有的可以用像素算，
有的只能用语言模型判断。

## 决策

**两条腿走路。任何一条单独使用都不够，因此两条都要有。**

### 第一轨：确定性指标（`reelagent/eval/consistency.py`）

零成本、可复现、可进 CI。全部基于像素与结构化字段计算：

| 指标 | 计算方式 | 无对象时的值 |
|---|---|---|
| `palette_stability` | 相邻关键帧 RGB 三通道各 16 bins 归一化直方图 → Bhattacharyya 系数 → 均值 | `1.0`（少于 2 张直方图时） |
| `cross_shot_clue_similarity` | 灰度 32×32 → 减均值除标准差 → 两两归一化互相关 → 映射到 0~1 → 均值 | `1.0` |
| `clue_reference_coverage` | `covered / eligible` | **`None`** |
| `prompt_clue_fidelity` | `ok / checked`（`appearance[:12]` 包含判断） | **`None`** |
| `rhythm_variety` | 景别/运镜的 distinct 计数、时长 min/max/std、景别直方图 | 空集合对应的零值 |

补充一个统计严谨性措施：覆盖率同时汇报 **Wilson 置信下界**
（`coverage_wilson_lower`），避免镜头数少时的高估。

**确定性是硬要求**。所有随机种子走 `reelagent/utils.py::stable_seed()`
（md5），**刻意不用内置 `hash()`**——后者对 `str` 按进程随机化
（`PYTHONHASHSEED`），会让同一输入两次运行产出不同画面，
评测回归就彻底失去意义。已用 `PYTHONHASHSEED=7` 与 `=99` 验证结果一致，
并在测试中锁定。

### 第二轨：LLM-as-Judge（`Evaluator.run()`）

四个 rubric 维度：**叙事连贯性、视觉一致性、节奏、prompt 忠实度**，
输出 `overall` 总分与 `top_issues` 及可执行建议。

实现要点：

```python
judge = self.rt.llm_json(
    system=prompts.JUDGE_SYSTEM,
    user=prompts.judge_user(self._shot_table(project), project.style),
    task="judge",
    span_name="evaluator.judge",
    temperature=0.2,        # 低温：评审需要稳定，不需要创造性
    judge=True,             # 允许使用独立的、更强的评审模型
)
```

Judge 的输入是一张 Markdown 表格（`_shot_table()`），
逐镜头列出：镜头 ID、景别、运镜、时长、动作、绑定线索、
生成提示词；表格后面附上所有线索的 `appearance`。
也就是说，Judge 能看到「我们打算做什么」（prompt 与约束），
而不只是「最终画面」——这正是它能判断忠实度的原因。

**Judge 失败不阻塞整体评测**：

```python
except Exception as exc:
    judge_error = f"{type(exc).__name__}: {exc}"
    self.rt.tracer.record("evaluator.judge.failed", status="failed", error=judge_error)
```

`judge` 为 `None`、`judge_error` 有值时，门禁会跳过 Judge 相关的判定，
其余指标照常生效。评测结果里也如实保留 `judge_error`。

### 合成门禁

`Evaluator._gate()` 把两条轨道合成一个布尔判定加失败原因列表：

```python
failures = []

cov = metrics["clue_reference_coverage"]
fid = metrics["prompt_clue_fidelity"]

if cov is None and fid is None:
    failures.append("一致性策略未生效：没有任何镜头绑定到「有基准图 / 有外观描述」的线索…")
else:
    if fid is not None and fid < 0.95:
        failures.append(f"一致性文本保真度 {fid:.2f} < 0.95：存在镜头未把线索 appearance 注入 prompt")
    if cov is not None and cov < 0.90:
        failures.append(f"基准图注入覆盖率 {cov:.2f} < 0.90")

if metrics["palette_stability"] < 0.5:
    failures.append(f"色调稳定性 {metrics['palette_stability']:.2f} < 0.50，画面风格漂移")

if judge and float(judge.get("overall") or 0) < 3.0:
    failures.append(f"LLM 评审总分 {judge.get('overall')} < 3.0")

return {
    "passed": not failures,
    "failures": failures,
    "thresholds": {
        "prompt_clue_fidelity": 0.95,
        "clue_reference_coverage": 0.90,
        "palette_stability": 0.50,
        "judge_overall": 3.0,
    },
}
```

阈值作为 `thresholds` 一并返回，因此报告是自解释的——
看报告的人不需要去翻代码就能知道标准是什么。

### 关键的安全网：「无数据」不等于「满分」

```python
def clue_reference_coverage(shots, clues):
    eligible = [s for s in shots if any(clues.get(c) and clues[c].base_image for c in s.clue_ids)]
    if not eligible:
        return None, {"eligible": 0, "covered": 0}   # ← 不是 1.0
```

综合分只对**有可度量对象**的维度取均值：

```python
parts = [v for v in (pal, css, cov, fid) if v is not None]
overall = round(float(np.mean(parts)), 4) if parts else 0.0
```

CLI 展示时 `None` 显示为 `N/A` 而不是 `1.0000`：

```python
def fmt_metric(value) -> str:
    """None 表示该维度没有可度量的对象，显示 N/A 而不是伪装成满分。"""
    if value is None:
        return "N/A"
```

**这一条是整个评测设计的核心安全网**，也是踩坑后加上的：
早期版本在无数据时返回 `1.0`，导致一致性机制完全失效时门禁照样通过。
代码注释里明确留痕：「这是本项目最值得记住的一个教训」。

## 后果

### 正面

- **成本与覆盖的分工明确**。确定性指标零成本，可以在每次运行、
  每次 CI 上都算；Judge 有一次 LLM 调用成本，
  但可以提供「故事好不好」这类确定性指标完全无法回答的判断。
- **不可复现的部分被隔离**。Judge 会漂移，但它不影响确定性指标的可回归性；
  确定性指标不会漂移，但它不懂叙事。
  两者组合后，「这次改动是回归还是改进」这个问题有了可操作的答案。
- **门禁可以分级诊断**。失败原因列表把问题分成三类：
  「机制没生效」（最严重）、「机制生效但结果差」（需要调模型/prompt）、
  「节奏问题」。这三类的处置方式完全不同，混在一起会让人无从下手。
- **Judge 失败不会炸掉流水线**。`judge_error` 被记录，
  评测结果照常产出，门禁退化到只用确定性指标。
  对一个「贵调用」的流水线，这个健壮性是必需的。
- **评测结果可归档、可对比**。`evaluator` 阶段写 `project.evaluation`，
  `python -m reelagent report <project_id>` 可以导出完整 JSON 报告
  （含 `project`、`cost`、`span_stats`），为「多版本评测对比」打好了基础。

### 负面 / 代价

- **Judge 增加了一次 LLM 调用成本与一次失败面**。可以用
  `use_judge=False`（CLI 的 `--no-judge`）跳过，代价是失去叙事与节奏判断。
- **Judge 的输出不可复现**。即使 `temperature=0.2`，
  同一个输入两次调用也可能给出不同的 `overall`（例如 4.25 vs 4.0）。
  跨过阈值的边界情况会造成门禁抖动。
  **当前没有对 Judge 做重复采样取均值或方差控制。**
- **阈值是经验值，没有数据支撑**。0.95 / 0.90 / 0.50 / 3.0 这四个数
  是设计时定的，尚未用真实用户反馈校准过。
  产品上线后应当根据实际投诉与人工评审结果重新校准。
- **两个「编排指标」必然为 1.0，容易被误读为质量信号**。
  `clue_reference_coverage` 与 `prompt_clue_fidelity` 度量的是
  「我们的代码有没有把约束传下去」，而注入逻辑是确定性的，
  因此达到 1.0 是设计使然。真正的质量信号是
  `cross_shot_clue_similarity` 与 Judge。
  文档与界面必须清楚区分这两类指标（见 ADR 0005）。
- **`cross_shot_clue_similarity` 只覆盖角色**，道具与场景不参与计算。
  因此「道具变形」这类问题**无法被自动检测**——只有 Judge 可能发现，
  而 Judge 只看文本表格（不含图像），实际上也发现不了。
  **这是一个真实的能力缺口。**
- **`rhythm_variety` 没有阈值**。它给出统计量但不参与门禁判定。
  也就是说「十个中景镜头各 3 秒」这种节奏问题不会导致门禁失败。

### 需要长期注意

- **不要因为 Judge 抖就把阈值往下调**。正确的做法是提高采样次数
  或改用更稳定的评审模型。
- **确定性指标的实现改动必须配回归测试**。它们是门禁的地基，
  一次实现错误会让整个门禁失去意义（这正是「无数据 ≠ 满分」那个坑的本质）。
- **`PYTHONHASHSEED` 相关测试不能删**。它是确定性承诺的唯一守护者。

## 备选方案

### 方案 A：只用确定性指标

- **优点**：零成本、完全可复现、无失败面、实现简单。
- **否决理由**：**它无法回答最重要的问题**。
  一支「角色一致、色调稳定、注入覆盖率 100%」的片子完全可能是
  「故事讲不通、镜头之间没有因果、观众看不懂」。
  确定性指标度量的是流程合规性，不是作品质量。

### 方案 B：只用 LLM-as-Judge

- **优点**：能理解语义、能评价叙事与忠实度、实现简单（一次调用）。
- **否决理由**：
  1. **不可复现**。同一个 prompt 模型版本更新后，
     历史分数就不再可比，CI 回归失去基线。
  2. **会漂移**。今天判 4.0 的片子明天可能判 3.5。
  3. **成本**：每次评测都要调用 LLM，CI 上每次都跑会持续烧钱。
  4. **对「机制是否生效」不敏感**。Judge 看的是镜头表格，
     如果 `clue_reference_coverage` 是 0（基准图根本没被注入），
     Judge 很可能仍给出「视觉一致性尚可」的评价——
     因为它没看到图像，只能从文本推断。
     **这个盲点是最致命的**：它意味着一整类 bug 无法被检出。

### 方案 C：人工评审

- **优点**：唯一能判断「好不好看」的方法。
- **否决理由**：不可扩展、成本高、标准不一致、有疲劳效应。
  **不是永远否决**：人工评审应当用于**校准阈值**与**抽样验收**，
  而不是作为每次改动的门禁。产品上线后需要建立这个校准流程
  （当前没有）。

### 方案 D：用 CLIP / 图像嵌入相似度替换灰度互相关

- **优点**：比灰度互相关更贴近语义相似度，能更好地捕捉「同一个人」。
- **否决理由（暂缓，而非否决）**：引入模型权重依赖，
  与「核心零依赖、clone 即跑」冲突。
  更合适的方向是引入人脸检测 / ReID 特征（既有 README 的 Roadmap 里
  已经列了这一项：「人脸/ReID 特征替换灰度互相关，提升一致性指标可信度」）。
  这应当是**可选依赖**而非核心依赖。

### 方案 E：引入专门的评测模型或评测服务

（例如微调一个打分模型，或接入第三方质量评估 API）

- **优点**：可能比通用 LLM Judge 更准确、更稳定、更便宜（若自托管）。
- **否决理由（暂缓）**：1.0.0 阶段没有评测数据来训这样的模型——
  这是「鸡生蛋」问题：需要先有线上评测数据。
  正确的顺序是先用双轨跑一段时间收集数据，再考虑专用模型。

---

## 参考

- 评测与一致性细节：[ARCHITECTURE.md](../ARCHITECTURE.md) 第 8 节
- 一致性方案：[ADR 0005](0005-cross-shot-consistency-dual-injection.md)
- 代码：`reelagent/agents/evaluator.py`、`reelagent/eval/consistency.py`、
  `reelagent/utils.py`
- 报告导出：`python -m reelagent report <project_id>`
