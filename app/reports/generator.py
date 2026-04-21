"""
HTML report generator — assembles all analysis results into a Jinja2-rendered HTML report.
"""
import os
import json
from jinja2 import Environment, FileSystemLoader
from datetime import datetime

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


def generate_report(
    target_listing: dict,
    comparable_listings: list[dict],
    school_info: dict,
    amenity_info: dict,
    sunlight_info: dict,
    noise_info: dict,
    prediction,       # PredictionResult
    score_result,     # ValueScoreResult
    undervalued: list,  # list of UndervaluedListing
    district_avg_price: int,
    city_name: str,
    scraper_source: str = "mock",
) -> str:
    """Render and return the full HTML report as a string."""

    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    env.filters["format_price"] = _format_price
    env.filters["format_wan"] = _format_wan
    template = env.get_template("report.html")

    # Build comparable listings summary for chart
    comparables_sorted = sorted(comparable_listings, key=lambda x: x["unit_price_sqm"])[:20]
    chart_prices = [l["unit_price_sqm"] for l in comparables_sorted]
    chart_labels = [f"{l['floor']}层/{l['orientation'][:2]}" for l in comparables_sorted]

    # Radar chart data
    radar_labels = ["性价比", "学区", "配套", "采光", "噪音", "装修/户型"]
    dims = score_result.dimensions
    radar_values = [
        dims["price_value"],
        dims["school"],
        dims["amenity"],
        dims["sunlight"],
        dims["noise"],
        round((dims["decoration"] + dims["layout"]) / 2),
    ]

    # Feature importance for bar chart
    fi = prediction.feature_importances
    fi_labels_map = {
        "area_sqm": "面积", "floor_ratio": "楼层", "age_years": "楼龄",
        "orientation_enc": "朝向", "decoration_enc": "装修", "school_tier_enc": "学区",
        "room_count": "户型", "amenity_score": "配套", "noise_score": "噪音",
        "sunlight_score": "采光",
    }
    fi_items = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:6]
    fi_labels = [fi_labels_map.get(k, k) for k, _ in fi_items]
    fi_values = [round(v * 100, 1) for _, v in fi_items]

    context = {
        "report_date": datetime.now().strftime("%Y年%m月%d日"),
        "listing": target_listing,
        "city_name": city_name,
        "school": school_info,
        "amenity": amenity_info,
        "sunlight": sunlight_info,
        "noise": noise_info,
        "prediction": prediction,
        "score": score_result,
        "undervalued": undervalued,
        "district_avg_price": district_avg_price,
        # Chart data
        "chart_prices_json": json.dumps(chart_prices),
        "chart_labels_json": json.dumps(chart_labels),
        "target_price": target_listing.get("unit_price_sqm", 0),
        "predicted_price": prediction.predicted_price,
        "radar_labels_json": json.dumps(radar_labels),
        "radar_values_json": json.dumps(radar_values),
        "fi_labels_json": json.dumps(fi_labels),
        "fi_values_json": json.dumps(fi_values),
        "comparable_count": len(comparable_listings),
        "community_name": target_listing.get("community_name", ""),
        "is_mock_price": scraper_source != "lianjia",
    }

    return template.render(**context)


def _format_price(value: int) -> str:
    """Format price as 元/㎡ with thousand separators."""
    return f"{value:,}"


def _format_wan(value: float) -> str:
    """Format price in 万元."""
    return f"{value:.0f}万"
