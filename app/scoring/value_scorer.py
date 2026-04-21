"""
Composite value scorer — combines ML price prediction with rule-based dimension scores.
"""
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Dimension weights (must sum to 1.0)
WEIGHTS = {
    "price_value": 0.35,
    "school":      0.20,
    "amenity":     0.15,
    "sunlight":    0.12,
    "noise":       0.10,
    "decoration":  0.04,
    "layout":      0.04,
}

DECORATION_SCORES = {
    "豪华装修": 95, "精装修": 82, "简装修": 65, "毛坯": 45,
}

LAYOUT_SCORES = {
    "1室1厅": 60, "2室1厅": 72, "2室2厅": 85,
    "3室1厅": 80, "3室2厅": 92, "4室2厅": 88,
    "4室3厅": 85, "5室3厅": 78,
}


@dataclass
class ValueScoreResult:
    composite_score: float
    grade: str
    grade_color: str
    recommendation: str
    dimensions: dict  # name → score
    weights: dict


def score_listing(
    listing: dict,
    price_value_score: float,
    school_score: float,
    amenity_score: float,
    sunlight_score: float,
    noise_score: float,
) -> ValueScoreResult:
    """Compute composite value score for a single listing."""

    decoration_score = DECORATION_SCORES.get(listing.get("decoration", "简装修"), 65)
    layout_score = _layout_score(listing.get("layout", "2室2厅"), listing.get("area_sqm", 90))

    dimensions = {
        "price_value": round(price_value_score),
        "school": round(school_score),
        "amenity": round(amenity_score),
        "sunlight": round(sunlight_score),
        "noise": round(noise_score),
        "decoration": round(decoration_score),
        "layout": round(layout_score),
    }

    composite = sum(dimensions[k] * WEIGHTS[k] for k in WEIGHTS)
    composite = round(composite, 1)

    grade, grade_color, recommendation = _grade(composite, price_value_score)

    return ValueScoreResult(
        composite_score=composite,
        grade=grade,
        grade_color=grade_color,
        recommendation=recommendation,
        dimensions=dimensions,
        weights=WEIGHTS,
    )


def _layout_score(layout: str, area: float) -> float:
    base = LAYOUT_SCORES.get(layout, 70)
    # Area efficiency bonus/penalty
    rooms_match = None
    import re
    m = re.search(r"(\d+)室", layout)
    if m:
        rooms = int(m.group(1))
        ideal_area_per_room = 28
        actual_per_room = area / max(rooms, 1)
        if actual_per_room >= ideal_area_per_room:
            base = min(100, base + 5)
        elif actual_per_room < 18:
            base = max(0, base - 8)
    return base


def _grade(composite: float, price_value_score: float) -> tuple:
    if composite >= 85:
        return "S", "#16a34a", "强烈推荐 — 综合价值极高，建议优先考虑"
    elif composite >= 75:
        return "A", "#2563eb", "推荐 — 综合价值良好，性价比突出"
    elif composite >= 65:
        return "B", "#d97706", "可以考虑 — 综合价值中等，部分维度有所欠缺"
    elif composite >= 50:
        return "C", "#dc2626", "谨慎观望 — 综合价值偏低，建议进一步核查"
    else:
        return "D", "#7f1d1d", "不建议 — 综合价值较差，存在明显劣势项"
