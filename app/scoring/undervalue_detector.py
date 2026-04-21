"""
Identifies undervalued listings using ML price prediction + composite score thresholds.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class UndervaluedListing:
    listing: dict
    predicted_price: int
    actual_price: int
    discount_pct: float
    absolute_discount_wan: float  # 万元
    composite_score: float
    reason: str


def detect_undervalued(
    listings: list[dict],
    predictions: list,  # list of PredictionResult
    scores: list,       # list of ValueScoreResult
    discount_threshold: float = 6.0,  # % below predicted
    min_composite_score: float = 60.0,
    max_days_on_market: int = 90,
) -> list[UndervaluedListing]:
    """
    Flag undervalued listings.
    Criteria:
      1. Actual price >= discount_threshold% below ML prediction
      2. Composite score >= min_composite_score
      3. Days on market <= max_days_on_market (if data available)
    """
    results = []

    for listing, pred, score in zip(listings, predictions, scores):
        if pred.discount_pct < discount_threshold:
            continue
        if score.composite_score < min_composite_score:
            continue

        dom = listing.get("days_on_market")
        if dom is not None and dom > max_days_on_market:
            continue

        area = listing.get("area_sqm", 90)
        abs_discount = round((pred.predicted_price - listing["unit_price_sqm"]) * area / 10000, 1)

        reason = _build_reason(pred.discount_pct, score, listing)

        results.append(UndervaluedListing(
            listing=listing,
            predicted_price=pred.predicted_price,
            actual_price=listing["unit_price_sqm"],
            discount_pct=round(pred.discount_pct, 1),
            absolute_discount_wan=abs_discount,
            composite_score=score.composite_score,
            reason=reason,
        ))

    # Sort by absolute discount value descending
    results.sort(key=lambda x: x.absolute_discount_wan, reverse=True)
    return results[:8]  # Return top 8


def _build_reason(discount_pct: float, score, listing: dict) -> str:
    parts = [f"定价低于市场预测价{discount_pct:.1f}%"]

    dims = score.dimensions
    strong = [k for k, v in dims.items() if v >= 80]
    dim_labels = {
        "school": "学区优质", "amenity": "配套完善",
        "sunlight": "采光好", "noise": "环境安静",
        "decoration": "装修好", "layout": "户型合理",
    }
    for k in strong:
        if k in dim_labels:
            parts.append(dim_labels[k])

    return "，".join(parts[:3])
