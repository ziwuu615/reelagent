# ADR 0005：跨镜头一致性用「基准图 + 外观文本」双路注入

- **状态**：已接受
- **日期**：1.0.0 发布前
- **决策者**：算法 / 引擎
- **相关文件**：`reelagent/schema.py`（`Clue`）、`reelagent/agents/artdirector.py`、`reelagent/agents/director.py`、`reelagent/eval/consistency.py`、`reelagent/agents/planner.py`、`reelagent/llm/prompts.py`

---

## 背景

短视频生成的**头号质量问题是跨镜头不一致**：

- **角色变脸**：第 1 镜是齐肩黑发的年轻女性，第 4 镜变成卷发；
  或者同一角色在不同镜头里的脸型、年龄、气质完全不同。
- **服装漂移**：外套在第 2 镜是米色，第 5 镜变成灰色。
- **场景漂移**：同一间便利店的货架布局、灯光色温、墙面材质逐镜变化。
- **道具变形**：作为叙事关键的那把「黄铜钥匙」在第 3 镜和最后一镜里
  形状、材质、比例都不同。

这些问题的根源是：**每个镜头的生成请求是独立的**。
图像/视频模型不记得上一个镜头生成了什么，而自然语言的 prompt
天然带有描述歧义——「一个年轻女性」在不同采样下就是不同的人。

对本产品而言这不只是质量问题，**也是商业问题**：
一支观众能看出「换人了」的短片，等于不可交付。

### 约束条件

1. **不能训模型**。训视频生成基础模型是千卡级投入，
   而这个项目的定位是「把多模型编排成可用产品」的编排层。
2. **要能覆盖不同供应商**。用户的组织可能自带不同供应商的 Key（BYOK），
   因此方案不能依赖某一家模型的特有能力（如专有的角色一致性 API）。
3. **要能度量**。「一致性做得好不好」必须能被自动判定，
   否则无法进 CI 门禁，也无法在 prompt/模型变更时做回归对比。
4. **要能降级**。一致性机制本身失败时，不能整条流水线崩掉。

## 决策

**两路并行的注入，加上四个可自动计算的指标。**

### 第一步：为每条线索建立「视觉锚点」

`Clue`（线索）是这样一套抽象：

```python
@dataclass
class Clue(Serializable):
    id: str
    kind: str                      # character | prop | scene
    name: str
    description: str = ""
    appearance: str = ""           # 外貌/材质/风格的稳定描述，参与每个 shot 的 prompt
    base_image: str | None = None  # 基准图相对路径
    style_tags: list[str] = field(default_factory=list)
```

Planner 在拆解剧本时，把**任何需要跨镜头重复出现**的人、物、场景
登记为线索（这是 prompt 里的硬约束，不是事后补的）。

ArtDirector 随后为每条线索：

1. **生成 `appearance` 文本**——prompt 里明确要求**只描述不随镜头变化的特征**
   （发型 / 五官 / 服装 / 材质），**禁止写动作与光线**。
   这是整件事的前提：把「会变的」和「不变的」在文本层面就分开。
2. **生成一张基准图**（角色设定图 / 场景设定图 / 道具设定图），
   提示词为 `"{appearance}。{kind_cn}，单人/单物居中，正面平视，纯净背景，{style_look}，{label}风格，无文字水印"`。
3. 生成顺序按 `_KIND_PRIORITY = {character: 0, scene: 1, prop: 2}` 排序
   （角色最重要），数量受 `max_base_images` 限制。
4. 种子走 `stable_seed(clue.id, salt="base_image")`，保证同一线索的基准图可复现。

### 第二步：每个镜头双路注入

`Director._do_shot()`：

**路一 · 视觉参考图**

```python
refs = [c.base_image for c in bound_clues if c.base_image]
result = self.rt.tools.call("generate_keyframe", {
    "prompt": shot.image_prompt,
    "out_name": f"{shot.id}.png",
    "ref_images": refs,                        # ← 基准图作为参考图传给图像模型
    "seed": stable_seed(shot.id),
    "shot_id": shot.id,
})
```

**路二 · 外观文本原样嵌入**

```python
clue_lines = [f"{c.name}：{c.appearance}" for c in bound_clues if c.appearance.strip()]
```

`clue_lines` 进入 `prompts.director_user(...)`，并要求 LLM
在生成 `image_prompt` 时**原样保留**这些描述。
`video_prompt` 由图像 prompt 与镜头动作派生。

### 第三步：确定性兜底，保证约束一定被传下去

两条路都有兜底：

- **文本路**：LLM 调用失败时走 `_fallback_prompt()`，
  它同样包含 `clue_lines`：

  ```python
  parts = [shot.shot_size, shot.action]
  if clue_lines:
      parts.append("；".join(clue_lines))
  parts.append(style_repr)
  return "，".join(p for p in parts if p)
  ```

- **appearance 本身**：ArtDirector 的 LLM 调用失败时，
  用 `clue.description` 或 `f"{name}（{kind}）"` 构造确定性描述。

**这个设计选择是关键的**：一致性不依赖 LLM 是否听话。
即使模型完全不可用，约束仍然被机械地传到了生成请求里。

### 第四步：度量「机制有没有被执行」，而不是「结果好不好」

`reelagent/eval/consistency.py` 的 docstring 点明了核心洞察：

> 后两个指标是这套方案的核心：它们度量的不是「生成结果好不好」（那依赖模型），
> 而是「我们的编排逻辑有没有真的把一致性约束传下去」——这是工程可控的部分。

| 指标 | 度量对象 | 属于 |
|---|---|---|
| `clue_reference_coverage` | 绑定线索的镜头中，实际把基准图作为参考注入的比例 | **编排逻辑** |
| `prompt_clue_fidelity` | 镜头 `image_prompt` 中是否原样包含所绑定线索的 `appearance`（取前 12 字符做包含判断，容忍轻微改写） | **编排逻辑** |
| `cross_shot_clue_similarity` | 同一角色线索在不同镜头画面之间的相似度（灰度 32×32 → 归一化 → 两两互相关） | 生成结果 |
| `palette_stability` | 相邻关键帧颜色直方图相关性（Bhattacharyya 系数） | 生成结果 |

前两个指标在编排逻辑正确时**必然达到 1.0**——这是设计使然，不是刷分。
在既有 README 记录的 mock 实测中它们都是 1.0000。

### 第五步：「无数据」不等于「满分」

```python
def clue_reference_coverage(shots, clues):
    eligible = [s for s in shots if any(clues.get(c) and clues[c].base_image for c in s.clue_ids)]
    if not eligible:
        return None, {"eligible": 0, "covered": 0}     # ← 而不是 1.0
    ...
```

综合分也只对**有可度量对象**的维度取均值：

```python
parts = [v for v in (pal, css, cov, fid) if v is not None]
overall = round(float(np.mean(parts)), 4) if parts else 0.0
```

门禁里还有一条专门针对这个的检查：

```python
if cov is None and fid is None:
    failures.append(
        "一致性策略未生效：没有任何镜头绑定到「有基准图 / 有外观描述」的线索，"
        "跨镜头一致性无从保证（请检查 Planner 的 clue_names 与 ArtDirector 是否生成成功）"
    )
```

## 后果

### 正面

- **不依赖任何模型的专有能力**。只用「参考图 + 文本」这两个所有主流
  图像生成接口都支持的输入通道，因此对 BYOK 换供应商友好。
- **可度量、可回归**。四个指标全部自动计算、零成本、可复现，
  能直接作为 CI 门禁阈值（`prompt_clue_fidelity ≥ 0.95`、
  `clue_reference_coverage ≥ 0.90`、`palette_stability ≥ 0.50`）。
- **失败模式清晰可诊断**。`prompt_clue_fidelity` 的 `fidelity_misses`
  会直接给出「`sc02_sh03`: 缺失 林晚」，`director.checkpoint_repaired`
  span 会记录回退了几个镜头。排查时不需要猜。
- **机制本身不会静默失效**。这正是「无数据 ≠ 满分」那条设计的价值：
  早期版本在无数据时返回 1.0，导致一致性机制完全失效时门禁照样通过。
  这是项目中最值得记住的一个教训，代码注释里明确留痕。
- **可复现**。`stable_seed()` 用 md5 而非内置 `hash()`，
  因此同一输入在不同 `PYTHONHASHSEED` 下画面一致（已用
  `PYTHONHASHSEED=7` 与 `=99` 验证），评测回归才有意义。

### 负面 / 代价

- **两个代理指标会「假性达标」**。`clue_reference_coverage` 与
  `prompt_clue_fidelity` 度量的是「约束被传下去了」，
  而不是「模型照做了」。它们必然为 1.0，
  因此**不能把它们当作质量信号**——真正的质量信号是
  `cross_shot_clue_similarity` 与 LLM Judge。
  文档与界面必须清楚区分这两类指标，否则会给人虚假的安心感。
- **`cross_shot_clue_similarity` 是粗糙的代理指标**。
  它用灰度归一化互相关，能捕捉大幅漂移，但不等价于人眼判断的
  「像同一个人」。在 mock 模式下它主要反映背景色调差异，
  因此既有 README 记录的 0.6986 是合理值而非「效果不好」。
  生产环境应换成人脸 / ReID 特征。
- **只覆盖 `character` 类线索**。`cross_shot_clue_similarity` 里
  `if clue is None or clue.kind != "character": continue`，
  道具与场景不参与该指标。它们的稳定性只被 `palette_stability`
  与人工/Judge 评审间接反映——**道具变形无法被自动检测**，
  这是一个真实的能力缺口。
- **基准图本身是有成本的**。每张基准图是一次图像生成调用
  （`quote_project()` 里按 `base_images = min(max(2, shots // 2), max_base_images)`
  计入预估），且受 `max_base_images` 限制——线索多于上限时，
  靠后的线索（场景、道具）拿不到基准图，只能靠文本路。
- **依赖 Planner 的质量**。如果 Planner 没把某个角色登记为线索，
  后续所有机制都不会作用到它。这是整条链路的单点。
  门禁只能检查「已登记的线索」是否被正确处理，无法发现「应该登记但没登记」。

### 需要长期注意

- **不要为了「指标好看」而放宽阈值**。阈值（0.95 / 0.90 / 0.50 / 3.0）
  的意义是发现回归，不是给发布凑数。
- **新供应商接入时必须确认 `ref_images` 真的被使用**。
  如果某个供应商的适配器静默忽略了参考图，
  `clue_reference_coverage` 仍然会是 1.0（它看的是本地状态），
  但实际效果会退化。这是一个需要靠集成测试而非单元测试发现的
  风险点。

## 备选方案

### 方案 A：只用文本描述（把 appearance 写进每个 prompt）

- **优点**：零额外成本，实现最简单，所有模型都支持。
- **否决理由**：文本描述有固有的歧义与采样随机性。
  「齐肩黑发、圆脸、米色风衣」这串词在不同采样下会生成不同的人。
  文本必须与视觉锚点配对才能稳定。**否决（作为唯一手段）。**
  但它是方案的必要组成部分，不是被丢弃的选项。

### 方案 B：只用基准图作为参考图

- **优点**：视觉信息比文本更精确，没有描述歧义。
- **否决理由**：
  1. 不是所有图像接口都以同样的方式支持参考图
     （有的按 IP-Adapter 语义、有的按 image-to-image、有的只做风格迁移）。
     纯依赖它会把方案绑死在特定供应商的能力上。
  2. 参考图对「动作、光照、构图」也有影响，可能压制镜头的表现力。
  3. 文本还承担了「可审计性」的职责——`prompt_clue_fidelity`
     正是通过检查文本来验证机制是否被执行的。没有文本就没法做这个检查。
  **否决（作为唯一手段）。**

### 方案 C：为每个角色训练 LoRA / 微调

- **优点**：一致性效果最好，工业界主流方案。
- **否决理由**：
  1. 需要训练基础设施与 GPU 时间，与「编排层」定位冲突。
  2. 每个租户的每个角色都要训一次——成本模型完全不可行
     （用户愿意为一条成片付 ¥49/月，不可能承担一次微调）。
  3. 无法对接 BYOK 场景（用户的供应商不一定提供微调接口）。

### 方案 D：用固定的随机种子 + 相同 prompt 前缀

- **优点**：实现成本极低，在某些模型上确实有效果。
- **否决理由**：种子控制只对同一模型、同一版本、同一参数集有效，
  跨供应商完全失效。而且它无法解决「同一个角色出现在不同场景」的问题
  （场景变了，prompt 就必须变）。**否决。**

### 方案 E：生成完再做后处理（换脸 / 风格迁移）

- **优点**：可以做得很精确（人脸检测 + 换脸模型）。
- **否决理由**：
  1. 引入新的模型依赖与推理成本。
  2. 只能修脸，修不了服装、场景、道具的漂移。
  3. 后处理会引入新的失真（换脸边缘伪影、光照不匹配），
     对短视频这种低分辨率输出的伤害尤其明显。
  - **不是永远否决**：作为**可选的增强环节**（在 `render` 阶段之前）
    是合理的，但它不能替代前向的约束注入。

### 方案 F：只在评测层做「一致性打分」，不做注入

- 也就是放弃改善，只做检测。
- **否决理由**：产品的核心卖点就是「保证跨镜头角色与场景一致」，
  只检测不改善等于不做产品。

---

## 参考

- 一致性度量实现：[ARCHITECTURE.md](../ARCHITECTURE.md) 第 8 节
- 代码：`reelagent/eval/consistency.py`、`reelagent/agents/artdirector.py`、
  `reelagent/agents/director.py`、`reelagent/agents/evaluator.py`
- 门禁阈值定义：`reelagent/agents/evaluator.py::_gate()`
