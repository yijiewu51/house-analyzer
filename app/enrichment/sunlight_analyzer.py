"""
Sunlight scoring based on orientation, floor, and building characteristics.
Pure rule-based — no external API needed.
"""

ORIENTATION_BASE_SCORES = {
    "南北通透": 100,
    "南向": 92,
    "东南": 85,
    "西南": 80,
    "东向": 70,
    "西向": 65,
    "北向": 38,
    "东北": 42,
    "西北": 40,
}


def analyze_sunlight(
    orientation: str,
    floor: int,
    total_floors: int,
    build_year: int,
    area_sqm: float,
) -> dict:
    """Return sunlight score and breakdown."""

    base = ORIENTATION_BASE_SCORES.get(orientation, 65)

    # Floor penalty: low floors in high-rises may be shadowed
    floor_penalty = 0
    floor_ratio = floor / max(total_floors, 1)

    if total_floors >= 18:  # High-rise
        if floor <= 3:
            floor_penalty = 20
        elif floor <= 6:
            floor_penalty = 10
        elif floor <= 9:
            floor_penalty = 3
    elif total_floors >= 7:  # Mid-rise
        if floor <= 2:
            floor_penalty = 12
        elif floor <= 4:
            floor_penalty = 5

    # Top floor: potential leak risk, slight penalty on value but not sunlight
    top_floor_note = ""
    if floor == total_floors and total_floors >= 6:
        top_floor_note = "顶层需注意隔热和防水"

    # Old building: narrower spacing
    old_building_penalty = 0
    if build_year < 2000:
        old_building_penalty = 8
    elif build_year < 2008:
        old_building_penalty = 3

    score = base - floor_penalty - old_building_penalty
    score = max(10, min(100, score))

    # Level
    if score >= 88:
        level = "极好"
    elif score >= 75:
        level = "良好"
    elif score >= 55:
        level = "一般"
    else:
        level = "较差"

    description = _build_description(orientation, floor, total_floors, floor_penalty, level)

    return {
        "score": round(score),
        "level": level,
        "orientation": orientation,
        "floor_info": f"{floor}/{total_floors}层",
        "floor_ratio_pct": round(floor_ratio * 100),
        "floor_penalty": floor_penalty,
        "base_orientation_score": base,
        "top_floor_note": top_floor_note,
        "description": description,
    }


def _build_description(orientation, floor, total_floors, floor_penalty, level):
    orient_desc = {
        "南北通透": "南北通透格局，空气对流好，日照时长最优",
        "南向": "纯南朝向，日照充足，为二手房最受欢迎朝向之一",
        "东南": "东南朝向，上午日照好，采光较优",
        "西南": "西南朝向，下午日照充足，注意夏季西晒",
        "东向": "东朝向，早晨采光好，下午无直射阳光",
        "西向": "西朝向，下午阳光强，夏季西晒明显，需注意遮阳",
        "北向": "北朝向，全天日照不足，采光条件较差，价格通常有折扣",
        "东北": "东北朝向，日照条件偏弱",
        "西北": "西北朝向，日照条件一般",
    }
    base = orient_desc.get(orientation, f"{orientation}朝向")
    if floor_penalty > 10:
        base += f"；{floor}/{total_floors}层位置较低，周边建筑可能有遮挡，建议实地验证采光"
    elif floor_penalty > 0:
        base += f"；{floor}层楼层适中"
    return base
