"""
Playwright-based Lianjia scraper.

流程:
  1. 首次运行 → 有头浏览器打开链家，用户手动过一次验证码
  2. Cookie 保存到本地文件，下次直接复用（通常可用数小时）
  3. Cookie 失效时自动重新打开浏览器让用户再过一次
"""
import re
import json
import asyncio
import logging
import random
import os
from datetime import datetime

# 服务器环境设为 true，跳过有头浏览器（无显示器）
HEADLESS_ONLY = os.getenv("HEADLESS_ONLY", "").lower() in ("1", "true", "yes")

def _current_year() -> int:
    return datetime.now().year
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def _get_city_code(city: str) -> str:
    from app.config import LIANJIA_CITY_CODE
    return LIANJIA_CITY_CODE.get(city, city)

COOKIE_DIR = Path(__file__).parent.parent.parent / "data"
COOKIE_FILE = COOKIE_DIR / "lianjia_cookies.json"


class ScraperBlockedError(Exception):
    pass


# ─────────────────────────────────────────────
# Cookie 管理
# ─────────────────────────────────────────────

def _load_cookies() -> list[dict]:
    if COOKIE_FILE.exists():
        try:
            return json.loads(COOKIE_FILE.read_text())
        except Exception:
            return []
    return []


def _save_cookies(cookies: list[dict]):
    COOKIE_DIR.mkdir(exist_ok=True)
    COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
    logger.info(f"Cookies saved → {COOKIE_FILE}")


# ─────────────────────────────────────────────
# 验证码辅助：有头浏览器让用户手动过
# ─────────────────────────────────────────────

async def _acquire_cookies_with_user(city_code: str) -> list[dict]:
    """打开可见浏览器，等用户手动过验证码后保存 Cookie。"""
    from playwright.async_api import async_playwright

    base_url = f"https://{city_code}.lianjia.com/ershoufang/"
    print("\n" + "="*60)
    print("🔑 需要手动过一次链家验证码")
    print(f"   浏览器将打开：{base_url}")
    print("   请在浏览器窗口中完成滑块验证码")
    print("   验证通过后，程序会自动继续")
    print("="*60 + "\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,   # 可见！让用户操作
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()
        await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)

        # 等用户过验证码：页面标题不再是 CAPTCHA 且房源列表出现
        print("⏳ 等待验证码通过（最多等60秒）...")
        try:
            await page.wait_for_function(
                "document.title !== 'CAPTCHA' && document.querySelector('ul.sellListContent') !== null",
                timeout=60000,
            )
            print("✅ 验证通过！正在保存 Cookie...")
        except Exception:
            # 用户可能已经通过了但选择器不同，尝试读 Cookie
            print("⚠️  超时，尝试直接读取当前 Cookie")

        cookies = await context.cookies()
        await browser.close()

    _save_cookies(cookies)
    return cookies


# ─────────────────────────────────────────────
# 主爬取逻辑
# ─────────────────────────────────────────────

async def _scrape_async(
    community_name: str,
    city: str,
    max_pages: int = 3,
) -> list[dict]:
    from playwright.async_api import async_playwright

    city_code = _get_city_code(city)
    search_url = f"https://{city_code}.lianjia.com/ershoufang/rs{community_name}/"

    async def _run_in_browser(p, headless: bool, cookies: list[dict]) -> tuple[list[dict], bool]:
        """
        Returns (listings, hit_captcha).
        如果遇到验证码返回 ([], True)，否则返回 (results, False)。
        """
        browser = await p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        if cookies:
            try:
                await context.add_cookies(cookies)
            except Exception:
                pass

        page = await context.new_page()
        listings = []
        hit_captcha = False

        for page_num in range(1, max_pages + 1):
            url = search_url if page_num == 1 else f"{search_url}pg{page_num}/"
            logger.info(f"Page {page_num}: {url}")

            await page.goto(url, wait_until="domcontentloaded", timeout=25000)

            # 如果是可见浏览器，等用户处理登录/验证码
            if not headless:
                await page.wait_for_timeout(2000)
                cur_url = page.url
                title = await page.title()
                need_action = (
                    title == "CAPTCHA"
                    or "login" in cur_url
                    or "passport" in cur_url
                    or "人机验证" in title
                )
                if need_action:
                    print(f"\n⏳ 请在浏览器中完成操作（登录 / 验证码）")
                    print(f"   当前页面：{cur_url}")
                    print("   完成后程序自动继续（最多等120秒）...")
                    try:
                        # 等待跳转回正常房源页面
                        await page.wait_for_url(
                            "**/ershoufang/**",
                            timeout=120000,
                        )
                        await page.wait_for_timeout(2000)
                        print("✅ 操作完成，继续爬取...")
                    except Exception:
                        print("⚠️  等待超时，尝试继续...")
            else:
                await page.wait_for_timeout(random.randint(1000, 1800))
                title = await page.title()
                content = await page.content()
                if title == "CAPTCHA" or "人机验证" in content:
                    hit_captcha = True
                    await browser.close()
                    return [], True

            # 等房源列表
            try:
                await page.wait_for_selector("ul.sellListContent li", timeout=8000)
            except Exception:
                logger.info(f"第{page_num}页无房源列表，停止翻页")
                break

            cards = await page.query_selector_all("ul.sellListContent li.LOGVIEWDATA")
            if not cards:
                cards = await page.query_selector_all("ul.sellListContent li")
            if not cards:
                break

            page_results = []
            for card in cards:
                item = await _parse_card(card, community_name, city)
                if item:
                    page_results.append(item)

            logger.info(f"第{page_num}页: {len(page_results)} 套")
            listings.extend(page_results)

            next_btn = await page.query_selector("div.page-box a.next:not(.disabled)")
            if not next_btn:
                break
            await page.wait_for_timeout(random.randint(800, 1500))

        # 保存最新 Cookie
        updated = await context.cookies()
        _save_cookies(updated)
        await browser.close()
        return listings, False

    async with async_playwright() as p:
        # 第一步：用已有 Cookie 无头爬取
        saved_cookies = _load_cookies()
        results, hit_captcha = await _run_in_browser(p, headless=True, cookies=saved_cookies)

        if not hit_captcha and results:
            return results

        # 第二步：遇到验证码 → 服务器环境直接放弃，本地开发才用有头浏览器
        if HEADLESS_ONLY:
            raise ScraperBlockedError("服务器环境遇到验证码，使用模拟数据")

        print("\n" + "="*60)
        print("🔑 链家需要人机验证")
        print(f"   即将打开浏览器，请完成滑块验证码后等待程序自动爬取")
        print("="*60)
        results, _ = await _run_in_browser(p, headless=False, cookies=saved_cookies)

        if not results:
            raise ScraperBlockedError("验证通过后仍无房源，请检查小区名称是否正确")
        return results


async def fetch_listings_playwright(
    community_name: str,
    city: str,
    max_pages: int = 3,
) -> list[dict]:
    """异步入口，供 FastAPI 路由直接 await 调用。"""
    try:
        listings = await _scrape_async(community_name, city, max_pages)
        if not listings:
            raise ScraperBlockedError("未获取到任何房源")
        logger.info(f"Playwright 爬取完成: {len(listings)} 套 [{community_name}]")
        return listings
    except ScraperBlockedError:
        raise
    except Exception as e:
        raise ScraperBlockedError(f"Playwright 错误: {e}")


def fetch_listings_playwright_sync(
    community_name: str,
    city: str,
    max_pages: int = 3,
) -> list[dict]:
    """同步入口，用于命令行测试脚本。"""
    return asyncio.run(fetch_listings_playwright(community_name, city, max_pages))


# ─────────────────────────────────────────────
# HTML 解析
# ─────────────────────────────────────────────

async def _parse_card(card, community_name: str, city: str) -> Optional[dict]:
    try:
        title_el = await card.query_selector(".title a")
        title = (await title_el.inner_text()).strip() if title_el else ""

        house_el = await card.query_selector(".houseInfo")
        house_text = await house_el.inner_text() if house_el else ""

        pos_el = await card.query_selector(".positionInfo")
        pos_text = await pos_el.inner_text() if pos_el else ""

        total_el = await card.query_selector(".totalPrice span")
        total_text = (await total_el.inner_text()).strip() if total_el else "0"
        total_price_wan = float(re.sub(r"[^\d.]", "", total_text) or "0")

        unit_el = await card.query_selector(".unitPrice span")
        unit_text = (await unit_el.inner_text()).strip() if unit_el else "0元/平"
        unit_price = int(re.sub(r"[^\d]", "", unit_text.split("元")[0]) or "0")

        if unit_price == 0 or total_price_wan == 0:
            return None

        parts = [p.strip() for p in house_text.split("|")]
        layout = parts[0] if parts else "2室1厅"

        area_match = re.search(r"([\d.]+)平", house_text)
        area_sqm = float(area_match.group(1)) if area_match else 90.0

        floor_match = re.search(r"(低|中|高)楼层.*?共(\d+)层", house_text)
        if floor_match:
            level_map = {"低": 0.15, "中": 0.50, "高": 0.80}
            total_floors = int(floor_match.group(2))
            floor = max(1, int(total_floors * level_map[floor_match.group(1)]))
        else:
            fm2 = re.search(r"(\d+)/(\d+)层", house_text)
            floor, total_floors = (int(fm2.group(1)), int(fm2.group(2))) if fm2 else (10, 20)

        orient_match = re.search(r"(南北通透|东南向|西南向|南北|东南|西南|南向|北向|东向|西向|南|北|东|西)", house_text)
        orientation = orient_match.group(1) if orient_match else "南向"
        if orientation in ("南", "北", "东", "西"):
            orientation += "向"
        orientation = orientation.replace("南北", "南北通透")

        decor_match = re.search(r"(精装修|豪华装修|豪装|精装|简装修|简装|毛坯)", house_text)
        decoration = decor_match.group(1) if decor_match else "简装修"
        decoration = (decoration.replace("精装", "精装修")
                                .replace("简装", "简装修")
                                .replace("豪装", "豪华装修"))

        year_match = re.search(r"(\d{4})年建", house_text)
        build_year = int(year_match.group(1)) if year_match else 2010

        district = pos_text.split("-")[0].strip() if pos_text else ""

        return {
            "listing_id": f"lj_{abs(hash(title)) % 999999:06d}",
            "title": title,
            "community_name": community_name,
            "district": district,
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
            "age_years": _current_year() - build_year,
            "lat": None,
            "lng": None,
            "days_on_market": None,
            "source": "lianjia_playwright",
        }

    except Exception as e:
        logger.debug(f"Card parse error: {e}")
        return None
