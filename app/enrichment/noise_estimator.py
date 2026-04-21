"""
Noise estimation based on nearby POIs and road types.
Uses Amap POI API or falls back to rule-based estimation from community name.
"""
import math
import logging
import os

logger = logging.getLogger(__name__)

NOISE_KEYWORDS = {
    "高": ["高架", "高速", "快速路", "轻轨", "地上轨道"],
    "中高": ["主干道", "国道", "省道"],
    "中": ["次干道", "商业街"],
    "低": ["支路", "小区内"],
}

# Community name keyword hints
QUIET_KEYWORDS = ["花园", "庄园", "别墅", "森林", "绿地", "湖畔", "河畔", "公园"]
NOISY_KEYWORDS = ["高架", "机场", "铁路", "站", "商业", "广场"]


def estimate_noise_score(
    community_name: str,
    lat: float = None,
    lng: float = None,
) -> dict:
    """Estimate noise score 0-100 (100 = quietest)."""

    # Try Amap if coordinates available
    if lat and lng:
        result = _query_amap_noise(lat, lng)
        if result:
            return result

    # Fallback: keyword heuristic
    return _keyword_noise_estimate(community_name)


def _query_amap_noise(lat: float, lng: float) -> dict | None:
    api_key = os.getenv("AMAP_API_KEY", "")
    if not api_key:
        return None

    import requests
    try:
        score = 85  # start optimistic
        sources = []

        # Check for highways and railways within 500m
        for poi_type, label, penalty, radius in [
            ("150500", "铁路/高架", 35, 500),
            ("180300", "高速公路出入口", 25, 400),
            ("150104", "城市快速路", 20, 300),
            ("060100", "娱乐场所", 15, 300),
        ]:
            url = "https://restapi.amap.com/v3/place/around"
            params = {
                "key": api_key,
                "location": f"{lng},{lat}",
                "types": poi_type,
                "radius": radius,
                "output": "json",
            }
            resp = requests.get(url, params=params, timeout=4)
            pois = resp.json().get("pois", [])
            if pois:
                dist = int(pois[0].get("distance", radius))
                # Distance-weighted penalty
                weighted_penalty = penalty * (1 - dist / radius) * 0.7
                score -= weighted_penalty
                sources.append(f"{label}({dist}m)")

        score = max(10, min(100, score))
        level = _score_to_level(score)

        return {
            "score": round(score),
            "level": level,
            "sources": sources,
            "description": _level_description(level),
            "source": "amap",
        }
    except Exception as e:
        logger.debug(f"Amap noise query failed: {e}")
        return None


def _keyword_noise_estimate(community_name: str) -> dict:
    score = 72  # baseline

    for kw in QUIET_KEYWORDS:
        if kw in community_name:
            score += 8
            break

    for kw in NOISY_KEYWORDS:
        if kw in community_name:
            score -= 15
            break

    score = max(10, min(100, score))
    level = _score_to_level(score)
    return {
        "score": score,
        "level": level,
        "sources": [],
        "description": _level_description(level),
        "source": "heuristic",
    }


def _score_to_level(score: float) -> str:
    if score >= 85:
        return "安静"
    elif score >= 70:
        return "较安静"
    elif score >= 50:
        return "一般"
    elif score >= 35:
        return "较嘈杂"
    return "嘈杂"


def _level_description(level: str) -> str:
    descriptions = {
        "安静": "周边噪音源少，居住环境安静，适合对居住品质要求较高的家庭",
        "较安静": "噪音较小，偶有少量噪音源，整体居住体验良好",
        "一般": "有一定噪音来源，建议选择朝向背离噪音源的户型",
        "较嘈杂": "周边存在明显噪音源，建议实地勘察并考虑隔音措施",
        "嘈杂": "噪音环境较差，存在高架/铁路/商业集群等强噪音源，需慎重考虑",
    }
    return descriptions.get(level, "")
