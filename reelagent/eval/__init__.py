"""评测层：跨镜头一致性指标 + LLM-as-Judge 报告。"""

from .consistency import (
    build_metrics,
    clue_reference_coverage,
    cross_shot_clue_similarity,
    palette_stability,
    prompt_clue_fidelity,
    rhythm_variety,
    wilson_lower_bound,
)

__all__ = [
    "build_metrics",
    "clue_reference_coverage",
    "cross_shot_clue_similarity",
    "palette_stability",
    "prompt_clue_fidelity",
    "rhythm_variety",
    "wilson_lower_bound",
]
