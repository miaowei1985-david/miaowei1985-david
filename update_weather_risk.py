#!/usr/bin/env python3
"""
天气风险脚本：
根据 receiverCityName 和 estimateConsignTime 查询 Open-Meteo 天气数据，
更新 weather_risk 字段标记榴莲到货风险。
"""

import sqlite3
import os
import json
import urllib.request
import urllib.parse
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'erp_all.db')

CITY_COORDS = {
    "北京": (39.9, 116.4), "上海": (31.2, 121.5), "天津": (39.1, 117.2), "重庆": (29.6, 106.5),
    "广州": (23.1, 113.3), "深圳": (22.5, 114.1), "东莞": (23.0, 113.7), "佛山": (23.0, 113.1),
    "珠海": (22.3, 113.6), "中山": (22.5, 113.4), "惠州": (23.1, 114.4), "汕头": (23.4, 116.7),
    "湛江": (21.3, 110.4), "茂名": (21.9, 110.9), "肇庆": (23.1, 112.5), "江门": (22.6, 113.1),
    "清远": (23.7, 113.0), "韶关": (24.8, 113.6), "河源": (23.7, 114.7), "梅州": (24.3, 116.1),
    "潮州": (23.7, 116.6), "揭阳": (23.5, 116.4), "汕尾": (22.8, 115.4), "阳江": (21.9, 112.0),
    "云浮": (22.9, 112.0),
    "杭州": (30.3, 120.2), "宁波": (29.9, 121.5), "温州": (28.0, 120.7), "绍兴": (30.0, 120.6),
    "嘉兴": (30.7, 120.8), "台州": (28.7, 121.4), "金华": (29.1, 119.6), "湖州": (30.9, 120.1),
    "衢州": (28.9, 118.9), "丽水": (28.5, 119.9), "舟山": (30.0, 122.1),
    "南京": (32.1, 118.8), "苏州": (31.3, 120.6), "无锡": (31.5, 120.3), "常州": (31.8, 119.9),
    "徐州": (34.3, 117.2), "南通": (32.0, 120.9), "扬州": (32.4, 119.4), "盐城": (33.4, 120.1),
    "镇江": (32.2, 119.4), "泰州": (32.5, 119.9), "淮安": (33.5, 119.0), "连云港": (34.6, 119.2),
    "宿迁": (34.0, 118.3),
    "济南": (36.7, 117.0), "青岛": (36.1, 120.4), "烟台": (37.5, 121.4), "潍坊": (36.7, 119.1),
    "临沂": (35.1, 118.4), "淄博": (36.8, 118.0), "济宁": (35.4, 116.6), "威海": (37.5, 122.1),
    "德州": (37.4, 116.3), "聊城": (36.5, 116.0), "滨州": (37.4, 118.0), "菏泽": (35.2, 115.5),
    "日照": (35.4, 119.5), "枣庄": (34.9, 117.5), "东营": (37.5, 118.5), "泰安": (36.2, 117.1),
    "郑州": (34.8, 113.7), "洛阳": (34.6, 112.5), "南阳": (33.0, 112.5), "周口": (33.6, 114.6),
    "驻马店": (33.0, 114.0), "信阳": (32.1, 114.1), "新乡": (35.3, 113.9), "许昌": (34.0, 113.8),
    "开封": (34.8, 114.4), "商丘": (34.4, 115.6), "平顶山": (33.7, 113.3), "安阳": (36.1, 114.4),
    "焦作": (35.2, 113.2), "濮阳": (35.8, 115.0), "漯河": (33.6, 114.0), "三门峡": (34.8, 111.2),
    "长沙": (28.2, 113.0), "株洲": (27.8, 113.1), "湘潭": (27.9, 112.9), "衡阳": (26.9, 112.6),
    "邵阳": (27.2, 111.5), "岳阳": (29.4, 113.1), "常德": (29.0, 111.7), "郴州": (25.8, 113.0),
    "永州": (26.4, 111.6), "怀化": (27.6, 110.0),
    "合肥": (31.8, 117.3), "芜湖": (31.3, 118.4), "蚌埠": (32.9, 117.4), "淮南": (32.6, 117.0),
    "马鞍山": (31.7, 118.5), "安庆": (30.5, 117.0), "黄山": (29.7, 118.3), "阜阳": (32.9, 115.8),
    "福州": (26.1, 119.3), "厦门": (24.5, 118.1), "泉州": (24.9, 118.6), "漳州": (24.5, 117.6),
    "莆田": (25.4, 119.0), "龙岩": (25.1, 117.0), "宁德": (26.7, 119.5),
    "武汉": (30.6, 114.3), "宜昌": (30.7, 111.3), "襄阳": (32.0, 112.1), "荆州": (30.3, 112.2),
    "南昌": (28.7, 115.9), "赣州": (25.9, 114.9), "九江": (29.7, 116.0), "吉安": (27.1, 115.0),
    "成都": (30.6, 104.1), "绵阳": (31.5, 104.7), "德阳": (31.1, 104.4), "南充": (30.8, 106.1),
    "宜宾": (28.8, 104.6), "泸州": (28.9, 105.4), "乐山": (29.6, 103.8), "眉山": (30.1, 103.8),
    "南宁": (22.8, 108.3), "桂林": (25.3, 110.3), "柳州": (24.3, 109.4), "玉林": (22.6, 110.2),
    "梧州": (23.5, 111.3), "百色": (23.9, 106.6), "钦州": (22.0, 108.6), "北海": (21.5, 109.2),
    "昆明": (25.0, 102.7), "大理": (25.6, 100.2), "曲靖": (25.5, 103.8), "玉溪": (24.4, 102.5),
    "西安": (34.3, 108.9), "宝鸡": (34.4, 107.1), "咸阳": (34.3, 108.7), "汉中": (33.1, 107.0),
    "石家庄": (38.0, 114.5), "保定": (38.9, 115.5), "唐山": (39.6, 118.2), "邯郸": (36.6, 114.5),
    "秦皇岛": (39.9, 119.6), "廊坊": (39.5, 116.7), "沧州": (38.3, 116.9), "张家口": (40.8, 114.9),
    "承德": (41.0, 117.9),
    "沈阳": (41.8, 123.4), "大连": (38.9, 121.6), "鞍山": (41.1, 122.9), "丹东": (40.1, 124.4),
    "长春": (43.9, 125.3), "吉林": (43.8, 126.5),
    "哈尔滨": (45.8, 126.6), "大庆": (46.6, 125.0), "齐齐哈尔": (46.8, 123.9),
    "贵阳": (26.6, 106.7), "遵义": (27.7, 106.9),
    "太原": (37.9, 112.6), "大同": (40.1, 113.3),
    "海口": (20.0, 110.3), "三亚": (18.2, 109.5),
    "兰州": (36.1, 103.8), "天水": (34.6, 105.7), "银川": (38.5, 106.3), "西宁": (36.6, 101.8),
    "呼和浩特": (40.8, 111.7), "包头": (40.6, 109.8),
    "乌鲁木齐": (43.8, 87.6), "拉萨": (29.6, 91.1),
}


def get_coords(city):
    if not city:
        return None
    if city in CITY_COORDS:
        return CITY_COORDS[city]
    for k in CITY_COORDS:
        if k in city or city in k:
            return CITY_COORDS[k]
    return None


def fetch_weather(lat, lon, date_str):
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"
        f"&start_date={date_str}&end_date={date_str}&timezone=Asia/Shanghai"
    )
    try:
        req = urllib.request.urlopen(url, timeout=10)
        data = json.loads(req.read().decode())
        daily = data.get('daily', {})
        if not daily.get('temperature_2m_max'):
            return None
        return {
            'max_temp': daily['temperature_2m_max'][0],
            'min_temp': daily['temperature_2m_min'][0],
            'precipitation': daily['precipitation_sum'][0],
            'wind_speed': daily['wind_speed_10m_max'][0],
        }
    except Exception as e:
        return None


def assess_risk(weather):
    if not weather:
        return '天气数据获取失败'
    risks = []
    max_t = weather['max_temp']
    min_t = weather['min_temp']
    precip = weather['precipitation']
    wind = weather['wind_speed']
    if max_t > 35:
        risks.append(f"极高温 {max_t:.0f}C (严重!)")
    elif max_t > 30:
        risks.append(f"高温 {max_t:.0f}C")
    elif max_t > 28:
        risks.append(f"偏热 {max_t:.0f}C")
    if min_t < 5:
        risks.append(f"低温 {min_t:.0f}C")
    if precip > 50:
        risks.append(f"暴雨 {precip:.0f}mm")
    elif precip > 20:
        risks.append(f"大雨 {precip:.0f}mm")
    elif precip > 5:
        risks.append(f"中雨 {precip:.0f}mm")
    elif precip > 0:
        risks.append(f"小雨 {precip:.0f}mm")
    if wind > 60:
        risks.append(f"大风 {wind:.0f}km/h")
    if not risks:
        return f"天气良好 (最高{max_t:.0f}C)"
    return ' | '.join(risks)


def update_weather_risk(limit=0):
    if not os.path.exists(DB_PATH):
        print(f"找不到数据库：{DB_PATH}")
        return
    print(f"连接数据库：{DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # 确保 weather_risk 列存在
    try:
        cursor.execute("ALTER TABLE erp_wait_check ADD COLUMN weather_risk TEXT DEFAULT NULL")
    except Exception:
        pass
    conn.commit()

    query = """
        SELECT tradeId, receiverCityName, estimateConsignTime
        FROM erp_wait_check
        WHERE estimateConsignTime IS NOT NULL
          AND (weather_risk IS NULL
               OR weather_risk = '✅ 待查询'
               OR weather_risk = '✅ 待 API 查询')
    """
    if limit > 0:
        query += f" LIMIT {limit}"

    cursor.execute(query)
    rows = cursor.fetchall()

    if not rows:
        print("所有订单天气风险已更新完毕。")
        conn.close()
        return

    print(f"需要更新 {len(rows)} 条订单的天气风险...")

    city_date_set = set()
    weather_cache = {}
    for _, city, eta in rows:
        if city and eta:
            date_str = eta[:10]
            city_date_set.add((city, date_str))

    print(f"需要查询 {len(city_date_set)} 个（城市, 日期）组合的天气...")

    updated = 0
    skipped = 0
    for tradeId, city, eta in rows:
        if not city or not eta:
            skipped += 1
            continue

        date_str = eta[:10]
        coords = get_coords(city)
        if not coords:
            cursor.execute(
                "UPDATE erp_wait_check SET weather_risk = '❓ 未知城市' WHERE tradeId = ?",
                (tradeId,)
            )
            skipped += 1
            continue

        cache_key = (city, date_str)
        if cache_key in weather_cache:
            weather = weather_cache[cache_key]
        else:
            lat, lon = coords
            weather = fetch_weather(lat, lon, date_str)
            weather_cache[cache_key] = weather

        risk = assess_risk(weather)
        cursor.execute(
            "UPDATE erp_wait_check SET weather_risk = ? WHERE tradeId = ?",
            (risk, tradeId)
        )
        updated += 1

        if updated % 100 == 0:
            conn.commit()
            print(f"   已更新 {updated} 条...")

    conn.commit()
    print(f"\n完成！成功 {updated} 条，跳过 {skipped} 条")

    cursor.execute("""
        SELECT weather_risk, COUNT(*)
        FROM erp_wait_check
        WHERE weather_risk IS NOT NULL AND weather_risk NOT LIKE '✅ 待%'
        GROUP BY weather_risk
        ORDER BY COUNT(*) DESC
    """)
    print(f"\n风险分布:")
    for risk, cnt in cursor.fetchall():
        print(f"   {risk}: {cnt} 单")

    conn.close()


if __name__ == '__main__':
    import sys
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    update_weather_risk(limit)
