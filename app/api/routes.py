"""
FastAPI route handlers — orchestrates the full analysis pipeline.
"""
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from app.scrapers.playwright_scraper import fetch_listings_playwright, ScraperBlockedError
from app.scrapers.mock_data import generate_listings
from app.enrichment.school_district import get_school_score
from app.enrichment.geocoder import geocode_community
from app.enrichment.noise_estimator import estimate_noise_score
from app.enrichment.sunlight_analyzer import analyze_sunlight
from app.enrichment.amenities import get_amenity_score
from app.scoring.price_model import PriceModel
from app.scoring.value_scorer import score_listing
from app.scoring.undervalue_detector import detect_undervalued
from app.reports.generator import generate_report
from app.enrichment.ai_analysis import generate_ai_analysis
from app.config import DISTRICT_AVG_PRICES, CITY_NAMES, MOCK_LISTING_COUNT

logger = logging.getLogger(__name__)
router = APIRouter()


class AnalyzeRequest(BaseModel):
    community_name: str
    city: str = "sh"
    district: Optional[str] = None
    # 用户可手动指定坐标，避免同名小区歧义
    lat: Optional[float] = None
    lng: Optional[float] = None
    # Optionally specify a target listing (otherwise analyze the median listing)
    target_unit_price: Optional[int] = None
    target_area: Optional[float] = None
    target_floor: Optional[int] = None
    target_total_floors: Optional[int] = None
    target_orientation: Optional[str] = None
    target_decoration: Optional[str] = None
    target_layout: Optional[str] = None
    target_build_year: Optional[int] = None
    target_total_price_wan: Optional[float] = None


@router.post("/analyze")
async def analyze(req: AnalyzeRequest):
    community = req.community_name.strip()
    city = req.city.lower().strip()

    if not community:
        raise HTTPException(status_code=400, detail="community_name is required")

    # --- Step 1: Fetch listings ---
    scraper_source = "mock"
    try:
        listings = await fetch_listings_playwright(community, city, max_pages=3)
        scraper_source = "lianjia"
        logger.info(f"Playwright scrape: {len(listings)} listings for {community}")
    except ScraperBlockedError as e:
        logger.warning(f"Scraper blocked ({e}), using mock data")
        listings = generate_listings(community, city, count=MOCK_LISTING_COUNT)
    except Exception as e:
        logger.error(f"Unexpected scraper error: {e}")
        listings = generate_listings(community, city, count=MOCK_LISTING_COUNT)

    if not listings:
        raise HTTPException(status_code=500, detail="No listing data available")

    # 样本不足时，扩展爬取同区域数据补充训练集
    MIN_SAMPLES = 15
    if scraper_source == "lianjia" and len(listings) < MIN_SAMPLES:
        district_hint = _guess_district(listings) or req.district or ""
        logger.info(f"Only {len(listings)} listings, expanding to district: {district_hint}")
        try:
            district_listings = await fetch_listings_playwright(
                district_hint or community, city, max_pages=2
            )
            # 只用于训练，不替换社区专属数据
            extra_train = [l for l in district_listings if l["listing_id"] not in {x["listing_id"] for x in listings}]
            listings = listings + extra_train[:30]
            logger.info(f"Expanded to {len(listings)} listings after district scrape")
        except Exception:
            # 爬失败就用mock补
            extra = generate_listings(community, city, count=20)
            listings = listings + extra

    # --- Step 2: Build target listing ---
    if req.target_unit_price:
        # User specified a specific listing to analyze
        target = {
            "listing_id": "target_user",
            "title": f"{community} 目标房源",
            "community_name": community,
            "district": req.district or _guess_district(listings),
            "city": city,
            "unit_price_sqm": req.target_unit_price,
            "total_price_wan": req.target_total_price_wan or round(req.target_unit_price * (req.target_area or 90) / 10000, 1),
            "area_sqm": req.target_area or 90.0,
            "floor": req.target_floor or 12,
            "total_floors": req.target_total_floors or 24,
            "floor_ratio": (req.target_floor or 12) / (req.target_total_floors or 24),
            "orientation": req.target_orientation or "南向",
            "decoration": req.target_decoration or "精装修",
            "layout": req.target_layout or "2室2厅",
            "build_year": req.target_build_year or 2015,
            "age_years": datetime.now().year - (req.target_build_year or 2015),
            "lat": None, "lng": None,
            "days_on_market": 15,
            "source": "user_input",
        }
    else:
        # Pick a representative listing (near median price)
        sorted_by_price = sorted(listings, key=lambda x: x["unit_price_sqm"])
        target = sorted_by_price[len(sorted_by_price) // 2]

    # --- Step 3: Enrich all listings ---
    district = req.district or target.get("district") or _guess_district(listings)

    # 坐标优先级：1）用户手动指定  2）链家房源坐标  3）高德地理编码
    lat = req.lat or target.get("lat")
    lng = req.lng or target.get("lng")
    if not lat or not lng:
        coords = geocode_community(community, city)
        if coords:
            lat, lng = coords
            logger.info(f"Geocoded {community}: ({lat:.4f}, {lng:.4f})")

    school_info = get_school_score(community, lat, lng)
    noise_info = estimate_noise_score(community, lat, lng)
    sunlight_info = analyze_sunlight(
        target["orientation"], target["floor"], target["total_floors"],
        target["build_year"], target["area_sqm"]
    )
    amenity_info = get_amenity_score(community, lat, lng)

    # Add enrichment scores to all listings for ML
    for lst in listings:
        lst["school_tier"] = school_info["tier"]
        lst["amenity_score"] = amenity_info["composite_score"]
        lst["noise_score"] = noise_info["score"]
        lst["sunlight_score"] = analyze_sunlight(
            lst["orientation"], lst["floor"], lst["total_floors"],
            lst["build_year"], lst["area_sqm"]
        )["score"]

    target["school_tier"] = school_info["tier"]
    target["amenity_score"] = amenity_info["composite_score"]
    target["noise_score"] = noise_info["score"]
    target["sunlight_score"] = sunlight_info["score"]

    # --- Step 4: Train price model ---
    model = PriceModel()
    model.train(listings)

    # --- Step 5: Score all listings ---
    all_predictions = [model.predict(lst) for lst in listings]
    all_scores = [
        score_listing(
            lst,
            pred.price_value_score,
            school_info["score"],
            amenity_info["composite_score"],
            analyze_sunlight(lst["orientation"], lst["floor"], lst["total_floors"],
                             lst["build_year"], lst["area_sqm"])["score"],
            noise_info["score"],
        )
        for lst, pred in zip(listings, all_predictions)
    ]

    # --- Step 6: Score target ---
    target_prediction = model.predict(target)
    target_score = score_listing(
        target,
        target_prediction.price_value_score,
        school_info["score"],
        amenity_info["composite_score"],
        sunlight_info["score"],
        noise_info["score"],
    )

    # --- Step 7: Detect undervalued (from all listings, excluding target) ---
    undervalued = detect_undervalued(listings, all_predictions, all_scores)

    # --- Step 8: District avg price ---
    city_prices = DISTRICT_AVG_PRICES.get(city, {})
    district_avg = city_prices.get(district, city_prices.get("default", 65000))

    # --- Step 9: AI analysis text ---
    city_name = CITY_NAMES.get(city, city.upper())
    ai_analysis_html = await generate_ai_analysis(
        listing=target,
        prediction=target_prediction,
        score_result=target_score,
        school_info=school_info,
        amenity_info=amenity_info,
        sunlight_info=sunlight_info,
        noise_info=noise_info,
        undervalued=undervalued,
        district_avg_price=district_avg,
        city_name=city_name,
    )

    # --- Step 10: Generate report ---
    report_html = generate_report(
        target_listing=target,
        comparable_listings=listings,
        school_info=school_info,
        amenity_info=amenity_info,
        sunlight_info=sunlight_info,
        noise_info=noise_info,
        prediction=target_prediction,
        score_result=target_score,
        undervalued=undervalued,
        district_avg_price=district_avg,
        city_name=city_name,
        scraper_source=scraper_source,
        ai_analysis_html=ai_analysis_html,
    )

    return {
        "report_html": report_html,
        "composite_score": target_score.composite_score,
        "grade": target_score.grade,
        "recommendation": target_score.recommendation,
        "predicted_price": target_prediction.predicted_price,
        "actual_price": target["unit_price_sqm"],
        "discount_pct": target_prediction.discount_pct,
        "undervalued_count": len(undervalued),
        "scraper_source": scraper_source,
        "listing_count": len(listings),
    }


@router.get("/community/{city}/{community_name}/listings")
async def get_listings(city: str, community_name: str):
    try:
        listings = await fetch_listings_playwright(community_name, city, max_pages=1)
        source = "lianjia"
    except ScraperBlockedError:
        listings = generate_listings(community_name, city)
        source = "mock"
    return {"listings": listings[:20], "source": source, "total": len(listings)}


@router.get("/geocode")
async def geocode(community_name: str, city: str = "sh"):
    """返回高德地理编码结果，让前端展示给用户确认位置是否正确。"""
    import os, requests as req_lib
    api_key = os.getenv("AMAP_API_KEY", "")
    if not api_key:
        return {"candidates": []}

    city_map = {"sh":"上海","bj":"北京","sz":"深圳","gz":"广州","cd":"成都","hz":"杭州","nj":"南京","wh":"武汉"}
    city_name = city_map.get(city, "上海")

    resp = req_lib.get("https://restapi.amap.com/v3/geocode/geo", params={
        "key": api_key, "address": community_name, "city": city_name, "output": "json"
    }, timeout=5)
    geocodes = resp.json().get("geocodes", [])

    candidates = []
    for g in geocodes[:3]:
        loc = g.get("location", "")
        if loc:
            lng_s, lat_s = loc.split(",")
            candidates.append({
                "address": g.get("formatted_address", ""),
                "district": g.get("district", ""),
                "lat": float(lat_s),
                "lng": float(lng_s),
            })
    return {"candidates": candidates}


@router.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


def _guess_district(listings: list[dict]) -> str:
    for lst in listings:
        if lst.get("district"):
            return lst["district"]
    return "未知区"
