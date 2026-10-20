"""跨镜头一致性度量。

对应 JD："跨镜头角色与场景一致"、"主体与场景一致性、时序稳定性"。

四个指标（全部可自动计算、可回归对比）：
  palette_stability          全局色调稳定性：各关键帧颜色直方图的相关性均值
  cross_shot_clue_similarity 同一线索在不同镜头中的画面相似度
  clue_reference_coverage    一致性策略的**执行覆盖率**：绑定线索的镜头中，
                             实际把基准图作为参考注入的比例
  prompt_clue_fidelity       一致性策略的**文本保真度**：镜头的 image_prompt 中
                             是否原样包含了所绑定线索的 appearance 描述

  后两个指标是这套方案的核心：它们度量的不是"生成结果好不好"（那依赖模型），
  而是"我们的编排逻辑有没有真的把一致性约束传下去"——这是工程可控的部分。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..schema import Clue, Shot


# --------------------------------------------------------------------------- #
# 基础图像工具
# --------------------------------------------------------------------------- #
def _load_gray(path: Path, size: int = 32) -> np.ndarray | None:
    try:
        with Image.open(path) as im:
            g = im.convert("L").resize((size, size), Image.Resampling.BILINEAR)
            arr = np.asarray(g, dtype=np.float32)
    except Exception:
        return None
    arr -= arr.mean()
    std = arr.std()
    if std < 1e-6:
        return None
    return arr / std


def _load_hist(path: Path, bins: int = 16) -> np.ndarray | None:
    try:
        with Image.open(path) as im:
            rgb = im.convert("RGB")
            arr = np.asarray(rgb)
    except Exception:
        return None
    hists = []
    for ch in range(3):
        h, _ = np.histogram(arr[:, :, ch], bins=bins, range=(0, 256))
        h = h.astype(np.float32)
        total = h.sum()
        hists.append(h / total if total else h)
    return np.concatenate(hists)


def _hist_corr(a: np.ndarray, b: np.ndarray) -> float:
    """直方图相关（Bhattacharyya 系数的补）：1 表示完全一致。"""
    bc = float(np.sum(np.sqrt(np.clip(a, 0, None) * np.clip(b, 0, None))))
    return max(0.0, min(1.0, bc))


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    """归一化互相关，映射到 0~1。"""
    v = float(np.mean(a * b))
    return max(0.0, min(1.0, (v + 1.0) / 2.0))


# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #
def palette_stability(paths: list[Path]) -> float:
    hists = [h for h in (_load_hist(p) for p in paths) if h is not None]
    if len(hists) < 2:
        return 1.0
    vals = []
    for i in range(len(hists) - 1):
        vals.append(_hist_corr(hists[i], hists[i + 1]))
    return round(float(np.mean(vals)), 4)


def cross_shot_clue_similarity(
    shots: list[Shot], clues: dict[str, Clue], base_dir: Path
) -> tuple[float, dict[str, Any]]:
    """同一线索在不同镜头中的画面相似度。"""
    by_clue: dict[str, list[Path]] = {}
    for shot in shots:
        if not shot.keyframe_path:
            continue
        p = base_dir / shot.keyframe_path
        if not p.exists():
            continue
        for cid in shot.clue_ids:
            clue = clues.get(cid)
            if clue is None or clue.kind != "character":
                continue
            by_clue.setdefault(cid, []).append(p)

    per_clue: dict[str, Any] = {}
    all_vals: list[float] = []
    for cid, paths in by_clue.items():
        if len(paths) < 2:
            continue
        grays = [g for g in (_load_gray(p) for p in paths) if g is not None]
        if len(grays) < 2:
            continue
        vals = []
        for i in range(len(grays)):
            for j in range(i + 1, len(grays)):
                vals.append(_ncc(grays[i], grays[j]))
        m = float(np.mean(vals))
        all_vals.append(m)
        per_clue[clues[cid].name] = {"shots": len(paths), "similarity": round(m, 4)}

    overall = round(float(np.mean(all_vals)), 4) if all_vals else 1.0
    return overall, per_clue


def clue_reference_coverage(shots: list[Shot], clues: dict[str, Clue]) -> tuple[float | None, dict[str, Any]]:
    """绑定线索的镜头里，有多少确实把基准图作为参考注入了。

    没有任何镜头绑定带基准图的线索时返回 None（而不是 1.0）——
    "没有可度量的对象"与"全部达标"是两回事，混为一谈会让指标失去意义。
    """
    eligible = [s for s in shots if any(clues.get(c) and clues[c].base_image for c in s.clue_ids)]
    if not eligible:
        return None, {"eligible": 0, "covered": 0}
    covered = [s for s in eligible if s.status in {"keyframe_done", "clip_done", "audio_done"}]
    return round(len(covered) / len(eligible), 4), {
        "eligible": len(eligible),
        "covered": len(covered),
    }


def prompt_clue_fidelity(shots: list[Shot], clues: dict[str, Clue]) -> tuple[float | None, list[str]]:
    """image_prompt 是否原样包含所绑定线索的 appearance。

    没有任何镜头绑定带 appearance 的线索时返回 None。
    """
    checked = 0
    ok = 0
    misses: list[str] = []
    for shot in shots:
        bound = [clues[c] for c in shot.clue_ids if c in clues and clues[c].appearance.strip()]
        if not bound:
            continue
        checked += 1
        prompt = shot.image_prompt or ""
        # 取 appearance 的前 12 个字符做包含判断（LLM 可能做轻微改写）
        missing = [c.name for c in bound if c.appearance[:12] not in prompt]
        if missing:
            misses.append(f"{shot.id}: 缺失 {', '.join(missing)}")
        else:
            ok += 1
    score = round(ok / checked, 4) if checked else None
    return score, misses


def rhythm_variety(shots: list[Shot]) -> dict[str, Any]:
    sizes = [s.shot_size for s in shots]
    cams = [s.camera for s in shots]
    durs = [s.duration_s for s in shots]
    return {
        "distinct_shot_sizes": len(set(sizes)),
        "distinct_cameras": len(set(cams)),
        "duration_min": min(durs) if durs else 0.0,
        "duration_max": max(durs) if durs else 0.0,
        "duration_std": round(float(np.std(durs)), 3) if durs else 0.0,
        "shot_size_histogram": {s: sizes.count(s) for s in sorted(set(sizes))},
    }


def build_metrics(
    shots: list[Shot],
    clues: list[Clue],
    workspace: Path,
) -> dict[str, Any]:
    clue_map = {c.id: c for c in clues}
    kf_paths = [workspace / s.keyframe_path for s in shots if s.keyframe_path]

    pal = palette_stability(kf_paths)
    css, per_clue = cross_shot_clue_similarity(shots, clue_map, workspace)
    cov, cov_detail = clue_reference_coverage(shots, clue_map)
    fid, misses = prompt_clue_fidelity(shots, clue_map)
    rhythm = rhythm_variety(shots)

    # 综合分：只对"有可度量对象"的维度取均值，None 表示该维度不适用
    parts = [v for v in (pal, css, cov, fid) if v is not None]
    overall = round(float(np.mean(parts)), 4) if parts else 0.0

    return {
        "overall": overall,
        "palette_stability": pal,
        "cross_shot_clue_similarity": css,
        "clue_reference_coverage": cov,
        "prompt_clue_fidelity": fid,
        "consistency_strategy_active": bool(cov),
        "per_clue_similarity": per_clue,
        "coverage_detail": cov_detail,
        "fidelity_misses": misses[:10],
        "rhythm": rhythm,
        "shots_evaluated": len(shots),
    }


def wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    """二项比例的 Wilson 置信下界——用于在样本少时汇报更诚实的覆盖率。"""
    if total == 0:
        return 0.0
    p = successes / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return round(max(0.0, (centre - margin) / denom), 4)
