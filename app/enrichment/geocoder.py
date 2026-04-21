"""
用高德地理编码API把小区名+城市 → 经纬度坐标
结果缓存在内存，避免重复请求
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)
_cache: dict[str, tuple[float, float]] = {}

CITY_ADCODE = {
    "sh": "310000", "bj": "110000", "sz": "440300",
    "gz": "440100", "cd": "510100", "hz": "330100",
    "nj": "320100", "wh": "420100",
}


CITY_NAME_MAP = {
    "sh": "上海", "bj": "北京", "sz": "深圳", "gz": "广州",
    "hz": "杭州", "cd": "成都", "nj": "南京", "wh": "武汉",
    "sz_js": "苏州", "xm": "厦门", "cq": "重庆", "ty": "天津",
    "xa": "西安", "cs": "长沙", "zhengzhou": "郑州", "hf": "合肥",
    "qd": "青岛", "nb": "宁波", "fuzhou": "福州", "wx": "无锡",
    "sy": "沈阳", "dg": "东莞", "fo": "佛山", "km": "昆明",
    "nn": "南宁", "hk": "海口", "sy_hn": "三亚",
}


def geocode_community(community_name: str, city: str) -> tuple[float, float] | None:
    """返回 (lat, lng) 或 None。"""
    key = f"{city}:{community_name}"
    if key in _cache:
        return _cache[key]

    api_key = os.getenv("AMAP_API_KEY", "")
    if not api_key:
        return None

    try:
        city_name = CITY_NAME_MAP.get(city, city)

        resp = requests.get(
            "https://restapi.amap.com/v3/geocode/geo",
            params={
                "key": api_key,
                "address": community_name,
                "city": city_name,
                "output": "json",
            },
            timeout=5,
        )
        data = resp.json()
        geocodes = data.get("geocodes", [])
        if not geocodes:
            return None

        loc = geocodes[0].get("location", "")
        if not loc:
            return None

        lng_str, lat_str = loc.split(",")
        result = (float(lat_str), float(lng_str))
        _cache[key] = result
        logger.info(f"Geocoded {community_name}: {result}")
        return result

    except Exception as e:
        logger.debug(f"Geocoding failed for {community_name}: {e}")
        return None
