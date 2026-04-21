"""
Lianjia/Beike live scraper.
Gracefully falls back to ScraperBlockedError on anti-scraping triggers.
"""
import time
import random
import logging
from datetime import datetime
import re
import json
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class ScraperBlockedError(Exception):
    pass


CITY_DOMAINS = {
    "sh": "sh", "bj": "bj", "sz": "sz", "gz": "gz",
    "cd": "cd", "hz": "hz", "nj": "nj", "wh": "wh",
}

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


def _headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.lianjia.com/",
    }


def _backoff_request(session: requests.Session, url: str, timeout: int, max_retries: int) -> requests.Response:
    for attempt in range(max_retries):
        try:
            resp = session.get(url, headers=_headers(), timeout=timeout)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (403, 429, 521):
                wait = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(f"HTTP {resp.status_code} on {url}, waiting {wait:.1f}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
            else:
                logger.warning(f"HTTP {resp.status_code} on {url}")
                break
        except requests.RequestException as e:
            logger.warning(f"Request error: {e}")
            time.sleep(2 ** attempt)

    raise ScraperBlockedError(f"Failed to fetch {url} after {max_retries} retries")


def fetch_community_listings(
    community_name: str,
    city: str,
    timeout: int = 15,
    max_retries: int = 4,
) -> list[dict]:
    """
    Attempt to scrape listings from Lianjia for a community.
    Raises ScraperBlockedError if anti-scraping triggers or network fails.
    """
    city_code = CITY_DOMAINS.get(city, city)
    session = requests.Session()

    # Step 1: Get cookies by visiting the homepage
    try:
        home_url = f"https://{city_code}.lianjia.com/"
        session.get(home_url, headers=_headers(), timeout=timeout)
        time.sleep(random.uniform(0.5, 1.5))
    except Exception:
        raise ScraperBlockedError("Cannot reach Lianjia homepage")

    # Step 2: Search for community
    search_url = f"https://{city_code}.lianjia.com/ershoufang/rs{requests.utils.quote(community_name)}/"

    try:
        resp = _backoff_request(session, search_url, timeout, max_retries)
    except ScraperBlockedError:
        raise

    soup = BeautifulSoup(resp.text, "lxml")

    # Detect CAPTCHA / verification page
    if "验证" in resp.text[:2000] or "captcha" in resp.text[:2000].lower():
        raise ScraperBlockedError("CAPTCHA detected")

    listings = []
    cards = soup.select("ul.sellListContent li.LOGVIEWDATA")

    if not cards:
        # Try alternative selector
        cards = soup.select("div.info.clear")

    if not cards:
        raise ScraperBlockedError("No listing cards found — page structure may have changed")

    for card in cards[:30]:
        try:
            listing = _parse_card(card, community_name, city)
            if listing:
                listings.append(listing)
        except Exception as e:
            logger.debug(f"Card parse error: {e}")
            continue

    if not listings:
        raise ScraperBlockedError("Parsed 0 listings from page")

    logger.info(f"Scraped {len(listings)} listings for {community_name} in {city}")
    return listings


def _parse_card(card, community_name: str, city: str) -> dict | None:
    """Parse a single listing card from Lianjia HTML."""
    try:
        title_el = card.select_one("div.title a") or card.select_one("a.LOGCLICKDATA")
        title = title_el.get_text(strip=True) if title_el else ""

        # House info line: "3室2厅 | 89.7平米 | 南 | 精装 | 高楼层(共32层) | 2018年建"
        house_info = card.select_one("div.houseInfo")
        house_text = house_info.get_text() if house_info else ""

        # Price
        total_price_el = card.select_one("div.totalPrice span")
        unit_price_el = card.select_one("div.unitPrice span")
        total_price_wan = float(total_price_el.get_text(strip=True)) if total_price_el else 0
        unit_price_text = unit_price_el.get_text(strip=True) if unit_price_el else "0元/平"
        unit_price = int(re.sub(r"[^\d]", "", unit_price_text)) if unit_price_text else 0

        # Parse house info
        parts = [p.strip() for p in house_text.split("|")]
        layout = parts[0] if parts else ""
        area_match = re.search(r"([\d.]+)平", house_text)
        area_sqm = float(area_match.group(1)) if area_match else 0

        floor_match = re.search(r"(低|中|高)楼层.*?共(\d+)层", house_text)
        if floor_match:
            floor_level = floor_match.group(1)
            total_floors = int(floor_match.group(2))
            floor_map = {"低": 0.2, "中": 0.5, "高": 0.8}
            floor = max(1, int(total_floors * floor_map.get(floor_level, 0.5)))
        else:
            floor, total_floors = 10, 20

        orient_match = re.search(r"(南北通透|东南|西南|南向|北向|东向|西向|南|北|东|西)", house_text)
        orientation = orient_match.group(1) if orient_match else "南向"
        if orientation in ("南", "北", "东", "西"):
            orientation = orientation + "向"

        decor_match = re.search(r"(精装|简装|毛坯|豪华装修|豪装)", house_text)
        decoration = decor_match.group(1) if decor_match else "简装修"
        if decoration == "精装":
            decoration = "精装修"

        year_match = re.search(r"(\d{4})年建", house_text)
        build_year = int(year_match.group(1)) if year_match else 2010

        return {
            "listing_id": f"lj_{hash(title) % 100000:05d}",
            "title": title,
            "community_name": community_name,
            "district": "",
            "city": city,
            "total_price_wan": total_price_wan,
            "unit_price_sqm": unit_price,
            "area_sqm": area_sqm,
            "floor": floor,
            "total_floors": total_floors,
            "floor_ratio": round(floor / max(total_floors, 1), 3),
            "orientation": orientation,
            "decoration": decoration,
            "layout": layout,
            "build_year": build_year,
            "age_years": datetime.now().year - build_year,
            "lat": None,
            "lng": None,
            "days_on_market": None,
            "source": "lianjia",
        }
    except Exception:
        return None
