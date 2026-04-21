"""
DeepSeek AI analysis — generates natural language verdict for the report.
"""
import os
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI | None:
    global _client
    if _client is None:
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            return None
        _client = AsyncOpenAI(
            api_key=key,
            base_url="https://api.deepseek.com",
        )
    return _client


async def generate_ai_analysis(
    listing: dict,
    prediction,
    score_result,
    school_info: dict,
    amenity_info: dict,
    sunlight_info: dict,
    noise_info: dict,
    undervalued: list,
    district_avg_price: int,
    city_name: str,
) -> str:
    """
    Call DeepSeek to generate a natural language analysis paragraph.
    Returns HTML string (plain paragraphs, no markdown).
    Falls back to empty string if API key missing or call fails.
    """
    client = _get_client()
    if client is None:
        logger.info("DEEPSEEK_API_KEY not set, skipping AI analysis")
        return ""

    dims = score_result.dimensions
    discount = prediction.discount_pct
    predicted = prediction.predicted_price
    actual = listing.get("unit_price_sqm", 0)
    area = listing.get("area_sqm", 90)
    total_price = listing.get("total_price_wan", 0)
    community = listing.get("community_name", "")
    district = listing.get("district", "")

    # Suggested offer price: if undervalued, anchor below list; if overvalued, nudge down
    if discount >= 3:
        suggested_wan = round(total_price * 0.97, 1)
        price_signal = f"挂牌价低于AI公允价{discount}%，存在明确折让空间"
    elif discount <= -3:
        suggested_wan = round(total_price * 0.92, 1)
        price_signal = f"挂牌价高于AI公允价{abs(discount)}%，存在溢价"
    else:
        suggested_wan = round(total_price * 0.96, 1)
        price_signal = "挂牌价处于公允价合理区间"

    strengths = []
    weaknesses = []
    if dims["school"] >= 80:
        strengths.append(f"学区优质（{school_info.get('tier', '')}，{dims['school']}分）")
    elif dims["school"] < 55:
        weaknesses.append(f"学区较弱（{dims['school']}分）")
    if dims["amenity"] >= 75:
        strengths.append(f"配套完善（{dims['amenity']}分）")
    elif dims["amenity"] < 55:
        weaknesses.append(f"配套偏弱（{dims['amenity']}分）")
    if dims["sunlight"] >= 80:
        strengths.append(f"采光极好（{listing.get('orientation','')}，{dims['sunlight']}分）")
    elif dims["sunlight"] < 55:
        weaknesses.append(f"采光一般（{dims['sunlight']}分）")
    if dims["noise"] >= 75:
        strengths.append(f"噪音环境安静（{dims['noise']}分）")
    elif dims["noise"] < 55:
        weaknesses.append(f"噪音较大（{dims['noise']}分）")

    prompt = f"""你是一位专业的中国二手房交易顾问，正在为购房者撰写一份房产分析报告中的"AI综合判断"部分。

【房源基本信息】
- 小区：{city_name}{district} {community}
- 面积：{area}㎡，{listing.get('layout','')}，{listing.get('floor','')}层/共{listing.get('total_floors','')}层，{listing.get('orientation','')}
- 装修：{listing.get('decoration','')}，建筑年份：{listing.get('build_year','')}年（楼龄{listing.get('age_years','')}年）
- 挂牌总价：{total_price}万元（{actual:,}元/㎡）

【AI价格模型结果】
- 预测公允价：{predicted:,}元/㎡
- 价格信号：{price_signal}
- 区域均价参考：{district_avg_price:,}元/㎡

【综合评分】
- 综合得分：{score_result.composite_score}分，评级{score_result.grade}级
- 性价比：{dims['price_value']}分 | 学区：{dims['school']}分 | 配套：{dims['amenity']}分
- 采光：{dims['sunlight']}分 | 噪音：{dims['noise']}分 | 装修：{dims['decoration']}分

【优势】{' / '.join(strengths) if strengths else '无明显突出优势'}
【劣势】{' / '.join(weaknesses) if weaknesses else '无明显劣势'}

【同小区低估房源数量】{len(undervalued)} 套

请用3-4段话（共约200-280字）撰写分析。要求：
1. 第一段：直接给出综合判断结论和买入逻辑/观望理由
2. 第二段：解读价格信号，结合区域均价和预测价，给出具体建议出价（建议出价约{suggested_wan}万，可根据实际情况微调±2%，不要写死）
3. 第三段：点评核心优势/劣势，给出自住或投资角度的具体建议
4. 如有低估房源，第四段简短提示可对比参考

语气：专业但不生硬，像经验丰富的买方经纪人给朋友的建议。不要用markdown格式，不要用"##"等符号，直接输出纯文字段落，段落之间用换行分隔。"""

    try:
        resp = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.7,
        )
        text = resp.choices[0].message.content.strip()
        # Convert plain newlines to HTML paragraphs
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        html = "".join(f"<p style='margin-bottom:10px;'>{p}</p>" for p in paragraphs)
        return html
    except Exception as e:
        logger.warning(f"DeepSeek API error: {e}")
        return ""
