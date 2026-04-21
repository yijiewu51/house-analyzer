"""
学区评估：高德POI查最近小学 + 上海/北京/深圳知名学校排名库匹配
策略：
  1. 用高德API找坐标周边1.5km内所有小学
  2. 对照知名学校数据库，返回最高等级的学校
  3. 没命中知名学校 → 按距离给普通/弱学区分
"""
import os
import math
import logging
import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 知名学校数据库（可持续扩充）
# ─────────────────────────────────────────────
TOP_SCHOOLS = {
    # ── 上海 顶级 ──
    "上海小学": ("顶级学区", 98),
    "上海市实验小学": ("顶级学区", 98),
    "大同中学附属小学": ("顶级学区", 97),
    "复旦大学附属小学": ("顶级学区", 96),
    "黄浦区卢湾第一中心小学": ("顶级学区", 97),
    "徐汇区高安路第一小学": ("顶级学区", 96),
    "静安区威海路幼儿园": ("顶级学区", 95),
    "第一师范附属小学": ("顶级学区", 96),
    "华东师范大学附属小学": ("顶级学区", 95),
    "上海师范大学附属第一实验小学": ("顶级学区", 94),
    "上海交通大学附属小学": ("顶级学区", 95),
    "建襄小学": ("顶级学区", 94),
    "延安初级中学": ("顶级学区", 94),
    "教科院附属实验小学": ("顶级学区", 93),
    "上海市实验学校": ("顶级学区", 96),
    "民办包玉刚实验学校": ("顶级学区", 95),
    "民办协和双语学校": ("顶级学区", 93),
    "尚德实验学校": ("优质学区", 88),
    # ── 上海 浦东/黄浦 ──
    "上海市黄浦区第一中心小学": ("顶级学区", 95),
    "黄浦区第一中心小学": ("顶级学区", 95),
    "上海市黄浦区卢湾第一中心小学": ("顶级学区", 97),
    "昌邑小学": ("优质学区", 82),
    "崂山小学": ("普通学区", 65),
    "上海市浦东新区明珠小学": ("优质学区", 80),
    "东方路小学": ("优质学区", 82),
    "塘桥小学": ("优质学区", 82),
    "浦东新区第二实验小学": ("优质学区", 84),
    "上海世博家园实验小学": ("优质学区", 80),
    # ── 上海 优质 ──
    "杨园学校": ("优质学区", 85),
    "上南路小学": ("优质学区", 83),
    "东方路小学": ("优质学区", 82),
    "塘桥小学": ("优质学区", 82),
    "甘泉外国语小学": ("优质学区", 80),
    "莘庄小学": ("优质学区", 78),
    "七宝明强小学": ("优质学区", 85),
    "七宝小学": ("优质学区", 82),
    "田林第三小学": ("优质学区", 80),
    "长宁区实验小学": ("优质学区", 82),
    "天山路小学": ("优质学区", 80),
    "延安中学": ("优质学区", 88),
    "控江中学": ("优质学区", 83),
    "建平中学": ("优质学区", 85),
    "华师大二附中": ("顶级学区", 96),
    "上海中学": ("顶级学区", 98),
    "复旦附中": ("顶级学区", 97),
    "交大附中": ("顶级学区", 96),
    # ── 北京 顶级 ──
    "北京市实验小学": ("顶级学区", 97),
    "北京第二实验小学": ("顶级学区", 97),
    "北京师范大学实验小学": ("顶级学区", 96),
    "中关村第一小学": ("顶级学区", 95),
    "中关村第三小学": ("顶级学区", 94),
    "人大附小": ("顶级学区", 96),
    "清华附小": ("顶级学区", 95),
    "北大附小": ("顶级学区", 95),
    "海淀区教师进修学校附属实验学校": ("优质学区", 87),
    "朝阳实验小学": ("优质学区", 85),
    "朝阳区实验小学": ("优质学区", 85),
    "芳草地国际学校": ("优质学区", 86),
    "史家胡同小学": ("顶级学区", 95),
    "北京小学": ("顶级学区", 94),
    # ── 深圳 ──
    "深圳小学": ("顶级学区", 95),
    "深圳市实验学校小学部": ("顶级学区", 96),
    "南山实验学校": ("顶级学区", 94),
    "深圳南山外国语学校": ("顶级学区", 93),
    "荔园小学": ("优质学区", 87),
    "深圳福田区福民小学": ("优质学区", 84),
    # ── 广州 ──
    "广州市天河区天府路小学": ("优质学区", 83),
    "广州市越秀区东风东路小学": ("顶级学区", 92),
    "广州市海珠区南武小学": ("优质学区", 84),
}

TIER_SCORE = {"顶级学区": 95, "优质学区": 82, "普通学区": 60, "弱学区": 35}


def get_school_score(community_name: str, lat: float = None, lng: float = None) -> dict:
    """返回学区信息和评分。优先高德真实查询，fallback静态数据库。"""

    api_key = os.getenv("AMAP_API_KEY", "")

    if lat and lng and api_key:
        result = _query_amap_schools(lat, lng, api_key)
        if result:
            return result

    # 关键词兜底（用于没有坐标的情况）
    return _keyword_fallback(community_name)


def _query_amap_schools(lat: float, lng: float, api_key: str) -> dict | None:
    """高德POI查周边学校，匹配知名学校库。"""
    try:
        url = "https://restapi.amap.com/v3/place/around"
        params = {
            "key": api_key,
            "location": f"{lng},{lat}",
            "keywords": "小学",
            "radius": 1500,
            "sortrule": "distance",
            "offset": 20,
            "output": "json",
        }
        resp = requests.get(url, params=params, timeout=6)
        pois = resp.json().get("pois", [])

        if not pois:
            return None

        # 过滤掉大学/培训机构
        exclude_kws = ["大学", "学院", "高校", "职业", "技校", "培训", "辅导", "补习"]
        pois = [p for p in pois if not any(kw in p.get("name", "") for kw in exclude_kws)]
        if not pois:
            return None

        # 先尝试命中知名学校库
        best_match = None
        for poi in pois:
            name = poi.get("name", "")
            dist = int(poi.get("distance", 9999))
            for key, (tier, score) in TOP_SCHOOLS.items():
                if key in name or name in key:
                    # 距离越近加权越高
                    dist_bonus = max(0, 10 - dist // 150)
                    effective_score = min(100, score + dist_bonus)
                    if best_match is None or effective_score > best_match["score"]:
                        best_match = {
                            "school_name": name,
                            "tier": tier,
                            "score": effective_score,
                            "distance_m": dist,
                            "source": "amap+db",
                        }

        if best_match:
            best_match["description"] = _tier_description(best_match["tier"], best_match["school_name"], best_match["distance_m"])
            return best_match

        # 没有命中知名学校 → 按最近学校距离给普通评分
        nearest = pois[0]
        name = nearest.get("name", "附近小学")
        dist = int(nearest.get("distance", 1000))

        if dist <= 400:
            tier, score = "普通学区", 65
        elif dist <= 800:
            tier, score = "普通学区", 58
        else:
            tier, score = "弱学区", 40

        return {
            "school_name": name,
            "tier": tier,
            "score": score,
            "distance_m": dist,
            "source": "amap",
            "description": _tier_description(tier, name, dist),
        }

    except Exception as e:
        logger.debug(f"高德学区查询失败: {e}")
        return None


def _keyword_fallback(community_name: str) -> dict:
    """基于小区名推断（仅当没有坐标时使用）。"""
    high_end = ["汤臣一品", "仁恒", "中海御景", "保利", "华润", "绿城", "龙湖", "万科翠湖"]
    for kw in high_end:
        if kw in community_name:
            return {
                "school_name": "待查询",
                "tier": "优质学区",
                "score": 75,
                "distance_m": None,
                "source": "heuristic",
                "description": "该小区位于核心板块，通常对口优质学区，建议用坐标查询获取精确结果",
            }
    return {
        "school_name": "暂无数据",
        "tier": "无学区信息",
        "score": 50,
        "distance_m": None,
        "source": "default",
        "description": "暂未获取到学区信息，建议提供坐标或向当地教育局核实",
    }


def _tier_description(tier: str, school_name: str, dist_m: int | None) -> str:
    dist_str = f"，距离约{dist_m}米" if dist_m else ""
    base = {
        "顶级学区": f"对口顶级名校「{school_name}」{dist_str}，升学资源极优，学区溢价显著",
        "优质学区": f"对口优质学校「{school_name}」{dist_str}，教育资源良好，区域竞争力强",
        "普通学区": f"对口学校「{school_name}」{dist_str}，教育资源一般，无明显学区溢价",
        "弱学区": f"附近学校「{school_name}」{dist_str}，学区资源偏弱，建议核实最新划分",
        "无学区信息": "暂未获取到学区信息，建议实地核查",
    }
    return base.get(tier, "")
