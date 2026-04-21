"""
Mock data generator — produces statistically realistic Shanghai/Beijing housing listings.
Used as fallback when live scraping is blocked or unavailable.
"""
import random
import math
from typing import Optional
from datetime import datetime

def _current_year() -> int:
    return datetime.now().year

# Seed for reproducibility within a session (re-seeded per community)
def _community_seed(community_name: str, city: str) -> int:
    return abs(hash(community_name + city)) % (2**31)


ORIENTATIONS = [
    ("南北通透", 1.08, 0.20),
    ("南向", 1.03, 0.30),
    ("东南", 1.02, 0.15),
    ("东向", 0.98, 0.12),
    ("西南", 1.00, 0.08),
    ("西向", 0.95, 0.08),
    ("北向", 0.92, 0.07),
]

DECORATIONS = [
    ("精装修", 1.05, 0.40),
    ("简装修", 1.00, 0.35),
    ("毛坯", 0.94, 0.15),
    ("豪华装修", 1.10, 0.10),
]

LAYOUTS = [
    ("1室1厅", 0.96, 0.10),
    ("2室1厅", 1.00, 0.25),
    ("2室2厅", 1.02, 0.30),
    ("3室1厅", 1.01, 0.15),
    ("3室2厅", 1.03, 0.15),
    ("4室2厅", 1.04, 0.05),
]

COMMUNITY_PRESETS = {
    "碧桂园": {"district": "闵行区", "base_price": 58000, "lat": 31.1128, "lng": 121.3811},
    "万科城市花园": {"district": "浦东新区", "base_price": 68000, "lat": 31.2170, "lng": 121.5440},
    "中海御景熙岸": {"district": "徐汇区", "base_price": 98000, "lat": 31.1825, "lng": 121.4488},
    "龙湖天街": {"district": "长宁区", "base_price": 88000, "lat": 31.2200, "lng": 121.4100},
    "保利天悦": {"district": "静安区", "base_price": 105000, "lat": 31.2350, "lng": 121.4550},
    "绿地海珀旭晖": {"district": "普陀区", "base_price": 65000, "lat": 31.2500, "lng": 121.4000},
    "仁恒滨江园": {"district": "浦东新区", "base_price": 110000, "lat": 31.2350, "lng": 121.5200},
    "汤臣一品": {"district": "浦东新区", "base_price": 280000, "lat": 31.2380, "lng": 121.5050},
}


def _weighted_choice(choices):
    """Pick from (name, multiplier, weight) list, returns (name, multiplier)."""
    weights = [c[2] for c in choices]
    name_mult = [(c[0], c[1]) for c in choices]
    return random.choices(name_mult, weights=weights, k=1)[0]


def generate_listings(
    community_name: str,
    city: str,
    base_price_per_sqm: Optional[int] = None,
    district: Optional[str] = None,
    center_lat: Optional[float] = None,
    center_lng: Optional[float] = None,
    count: int = 45,
) -> list[dict]:
    """Generate realistic mock listings for a community."""
    from app.config import DISTRICT_AVG_PRICES, MOCK_LISTING_COUNT

    rng = random.Random(_community_seed(community_name, city))

    # Look up preset or use defaults
    preset = None
    for key in COMMUNITY_PRESETS:
        if key in community_name:
            preset = COMMUNITY_PRESETS[key]
            break

    if base_price_per_sqm is None:
        base_price_per_sqm = preset["base_price"] if preset else (
            DISTRICT_AVG_PRICES.get(city, {}).get("default", 60000)
        )
    if district is None:
        district = preset["district"] if preset else _guess_district(city)
    if center_lat is None:
        center_lat = preset["lat"] if preset else _default_lat(city)
    if center_lng is None:
        center_lng = preset["lng"] if preset else _default_lng(city)

    count = count or MOCK_LISTING_COUNT
    listings = []

    # Determine community characteristics
    build_year_center = rng.randint(2005, 2020)
    total_floors_options = [6, 11, 18, 24, 32, 33]
    total_floors = rng.choice(total_floors_options)

    for i in range(count):
        # Area
        area = round(rng.gauss(92, 28), 1)
        area = max(35, min(280, area))

        # Floor
        floor = rng.randint(1, total_floors)
        floor_ratio = floor / total_floors

        # Orientation
        orient_name, orient_mult = _weighted_choice(ORIENTATIONS)

        # Decoration
        decor_name, decor_mult = _weighted_choice(DECORATIONS)

        # Layout
        layout_name, layout_mult = _weighted_choice(LAYOUTS)

        # Build year
        build_year = build_year_center + rng.randint(-5, 8)
        cur_year = _current_year()
        build_year = max(1990, min(cur_year, build_year))
        age_discount = max(0, (cur_year - build_year) * 0.008)

        # Floor adjustment
        if floor == 1:
            floor_mult = 0.95
        elif floor == 2:
            floor_mult = 0.97
        elif floor == total_floors:
            floor_mult = 0.97
        elif floor_ratio > 0.6:
            floor_mult = 1.02
        else:
            floor_mult = 1.00

        # Compute unit price with noise
        price_noise = rng.gauss(1.0, 0.06)
        unit_price = int(
            base_price_per_sqm
            * orient_mult
            * decor_mult
            * layout_mult
            * floor_mult
            * (1 - age_discount)
            * price_noise
        )
        unit_price = max(15000, unit_price)

        total_price_wan = round(unit_price * area / 10000, 1)

        # Coordinates (small Gaussian offset from community center)
        lat = round(center_lat + rng.gauss(0, 0.001), 6)
        lng = round(center_lng + rng.gauss(0, 0.001), 6)

        # Days on market
        days_on_market = int(rng.expovariate(1 / 45))
        days_on_market = max(1, min(365, days_on_market))

        listings.append({
            "listing_id": f"mock_{community_name}_{i:03d}",
            "title": f"{community_name} {layout_name} {area}平 {orient_name} {floor}层",
            "community_name": community_name,
            "district": district,
            "city": city,
            "total_price_wan": total_price_wan,
            "unit_price_sqm": unit_price,
            "area_sqm": area,
            "floor": floor,
            "total_floors": total_floors,
            "floor_ratio": round(floor_ratio, 3),
            "orientation": orient_name,
            "decoration": decor_name,
            "layout": layout_name,
            "build_year": build_year,
            "age_years": _current_year() - build_year,
            "lat": lat,
            "lng": lng,
            "days_on_market": days_on_market,
            "source": "mock",
        })

    return listings


def _guess_district(city: str) -> str:
    defaults = {"sh": "浦东新区", "bj": "朝阳区", "sz": "南山区", "gz": "天河区"}
    return defaults.get(city, "市辖区")


def _default_lat(city: str) -> float:
    lats = {"sh": 31.2304, "bj": 39.9042, "sz": 22.5431, "gz": 23.1291}
    return lats.get(city, 31.2304)


def _default_lng(city: str) -> float:
    lngs = {"sh": 121.4737, "bj": 116.4074, "sz": 114.0579, "gz": 113.2644}
    return lngs.get(city, 121.4737)
