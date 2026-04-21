"""
Amenities scoring: metro, malls, hospitals, parks.
Uses Amap POI API or falls back to community-name heuristics.
"""
import math
import logging
import os

logger = logging.getLogger(__name__)

# Community name → preset amenity data
PRESET_AMENITIES = {
    "汤臣一品": {
        "metro": [{"name": "陆家嘴站", "lines": ["2号线"], "distance_m": 650}],
        "malls": [{"name": "正大广场", "distance_m": 800}],
        "hospitals": [{"name": "上海市第一人民医院", "distance_m": 2500}],
        "parks": [{"name": "滨江大道公园", "distance_m": 200}],
    },
    "仁恒滨江": {
        "metro": [{"name": "世纪大道站", "lines": ["2号线", "4号线", "6号线", "9号线"], "distance_m": 900}],
        "malls": [{"name": "第一八佰伴", "distance_m": 1200}],
        "hospitals": [{"name": "上海交通大学医学院附属仁济医院", "distance_m": 1800}],
        "parks": [{"name": "陆家嘴绿地", "distance_m": 500}],
    },
}


def get_amenity_score(
    community_name: str,
    lat: float = None,
    lng: float = None,
) -> dict:
    """Return amenity scores and breakdown."""

    # Try preset
    for key, preset in PRESET_AMENITIES.items():
        if key in community_name:
            return _score_from_preset(preset)

    # Try Amap
    if lat and lng:
        result = _query_amap_amenities(lat, lng)
        if result:
            return result

    # Fallback heuristic
    return _heuristic_amenity(community_name)


def _score_from_preset(preset: dict) -> dict:
    metro = preset.get("metro", [])
    malls = preset.get("malls", [])
    hospitals = preset.get("hospitals", [])
    parks = preset.get("parks", [])

    metro_score = _distance_score(metro[0]["distance_m"] if metro else 2000, 1500) * 100 if metro else 30
    mall_score = _distance_score(malls[0]["distance_m"] if malls else 3000, 2000) * 100 if malls else 40
    hospital_score = _distance_score(hospitals[0]["distance_m"] if hospitals else 5000, 3000) * 100 if hospitals else 50
    park_score = _distance_score(parks[0]["distance_m"] if parks else 2000, 1500) * 100 if parks else 50

    composite = (metro_score * 0.40 + mall_score * 0.25 + hospital_score * 0.20 + park_score * 0.15)

    return {
        "composite_score": round(composite),
        "metro": metro,
        "malls": malls,
        "hospitals": hospitals,
        "parks": parks,
        "metro_score": round(metro_score),
        "mall_score": round(mall_score),
        "hospital_score": round(hospital_score),
        "park_score": round(park_score),
        "source": "preset",
        "description": _amenity_description(metro, composite),
    }


def _query_amap_amenities(lat: float, lng: float) -> dict | None:
    api_key = os.getenv("AMAP_API_KEY", "")
    if not api_key:
        return None

    import requests
    try:
        location = f"{lng},{lat}"

        def fetch_pois(keywords, poi_type, radius):
            url = "https://restapi.amap.com/v3/place/around"
            params = {
                "key": api_key,
                "location": location,
                "keywords": keywords,
                "types": poi_type,
                "radius": radius,
                "sortrule": "distance",
                "offset": 3,
                "output": "json",
            }
            resp = requests.get(url, params=params, timeout=5)
            return resp.json().get("pois", [])

        metro_pois = fetch_pois("地铁站", "150500", 1500)
        mall_pois = fetch_pois("购物中心|商场", "060100", 2500)
        hospital_pois = fetch_pois("三甲医院|医院", "090100", 3000)
        park_pois = fetch_pois("公园", "110101", 2000)

        def format_pois(pois, key="name"):
            return [{"name": p.get("name", ""), "distance_m": int(p.get("distance", 0))} for p in pois[:3]]

        metro_list = []
        for p in metro_pois[:2]:
            metro_list.append({
                "name": p.get("name", ""),
                "lines": [],
                "distance_m": int(p.get("distance", 0)),
            })

        malls = format_pois(mall_pois)
        hospitals = format_pois(hospital_pois)
        parks = format_pois(park_pois)

        metro_dist = metro_list[0]["distance_m"] if metro_list else 2000
        mall_dist = malls[0]["distance_m"] if malls else 3000
        hosp_dist = hospitals[0]["distance_m"] if hospitals else 5000
        park_dist = parks[0]["distance_m"] if parks else 2000

        metro_score = _distance_score(metro_dist, 1500) * 100
        mall_score = _distance_score(mall_dist, 2000) * 100
        hospital_score = _distance_score(hosp_dist, 3000) * 100
        park_score = _distance_score(park_dist, 1500) * 100

        composite = metro_score * 0.40 + mall_score * 0.25 + hospital_score * 0.20 + park_score * 0.15

        return {
            "composite_score": round(composite),
            "metro": metro_list,
            "malls": malls,
            "hospitals": hospitals,
            "parks": parks,
            "metro_score": round(metro_score),
            "mall_score": round(mall_score),
            "hospital_score": round(hospital_score),
            "park_score": round(park_score),
            "source": "amap",
            "description": _amenity_description(metro_list, composite),
        }
    except Exception as e:
        logger.debug(f"Amap amenity query failed: {e}")
        return None


def _heuristic_amenity(community_name: str) -> dict:
    score = 62
    metro_keywords = ["地铁", "站附近", "轨交"]
    commercial_keywords = ["商业", "广场", "城", "天街", "万象"]
    for kw in metro_keywords:
        if kw in community_name:
            score += 15
            break
    for kw in commercial_keywords:
        if kw in community_name:
            score += 10
            break
    score = min(95, score)
    return {
        "composite_score": score,
        "metro": [],
        "malls": [],
        "hospitals": [],
        "parks": [],
        "metro_score": score,
        "mall_score": score,
        "hospital_score": 65,
        "park_score": 60,
        "source": "heuristic",
        "description": "配套数据基于小区名称估算，建议实地核查交通和商业配套",
    }


def _distance_score(distance_m: float, reference_m: float) -> float:
    """Exponential decay score: 1.0 at 0m, ~0.37 at reference_m."""
    return math.exp(-distance_m / reference_m)


def _amenity_description(metro_list: list, composite: float) -> str:
    if not metro_list:
        metro_part = "暂无地铁信息"
    else:
        closest = metro_list[0]
        dist = closest["distance_m"]
        if dist <= 500:
            metro_part = f"距{closest['name']}仅{dist}米，交通极便利"
        elif dist <= 1000:
            metro_part = f"距{closest['name']}{dist}米，步行可达"
        else:
            metro_part = f"距最近地铁站{dist}米，需骑车或乘车"

    if composite >= 80:
        return f"配套资源优秀，{metro_part}，生活便利度高"
    elif composite >= 60:
        return f"配套较完善，{metro_part}，日常生活需求基本满足"
    else:
        return f"配套一般，{metro_part}，生活便利度有待提升"
