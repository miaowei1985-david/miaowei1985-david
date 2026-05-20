#!/usr/bin/env python3
"""
全国城市天气数据获取 + 缓存到SQLite
策略: 每日获取一次全国主要城市7天天气预报
"""
import sqlite3
import json
import http.client
import time
from datetime import datetime, timedelta
import logging
logging.basicConfig(filename="/tmp/erp_weather.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


DB_PATH = '/Users/macmini4pro/pdd/Claudecode/erp_all.db'

# 全国主要城市坐标（省会+计划单列市+重点地级市+东南亚城市）
CITIES = {
    # 中国大陆 - 直辖市
    "北京": (39.9, 116.4), "上海": (31.2, 121.5), "重庆": (29.6, 106.5), "天津": (39.1, 117.2),
    # 省会城市
    "广州": (23.1, 113.3), "深圳": (22.5, 114.1), "成都": (30.6, 104.1), "杭州": (30.3, 120.2),
    "武汉": (30.6, 114.3), "南京": (32.1, 118.8), "西安": (34.3, 108.9), "长沙": (28.2, 112.9),
    "郑州": (34.8, 113.7), "青岛": (36.1, 120.4), "大连": (38.9, 121.6), "厦门": (24.5, 118.1),
    "宁波": (29.9, 121.5), "合肥": (31.8, 117.3), "福州": (26.1, 119.3), "济南": (36.7, 117.0),
    "哈尔滨": (45.8, 126.6), "长春": (43.8, 125.3), "沈阳": (41.8, 123.4), "昆明": (25.0, 102.7),
    "贵阳": (26.6, 106.7), "南宁": (22.8, 108.3), "海口": (20.0, 110.3), "兰州": (36.1, 103.8),
    "乌鲁木齐": (43.8, 87.6), "拉萨": (29.7, 91.1), "西宁": (36.6, 101.8), "银川": (38.5, 106.2),
    "呼和浩特": (40.8, 111.7), "石家庄": (38.0, 114.5), "太原": (37.9, 112.5), "南昌": (28.7, 115.9),
    # 广东重点城市
    "佛山": (23.0, 113.1), "东莞": (23.0, 113.8), "惠州": (23.1, 114.4), "湛江": (21.2, 110.4),
    "揭阳": (23.5, 116.4), "珠海": (22.3, 113.6), "中山": (22.5, 113.4), "汕头": (23.4, 116.7),
    "肇庆": (23.0, 112.5), "江门": (22.6, 113.1), "茂名": (21.7, 110.9), "梅州": (24.3, 116.1),
    "潮州": (23.7, 116.6), "韶关": (24.8, 113.6), "清远": (23.7, 113.0), "阳江": (21.9, 112.0),
    # 江浙重点城市
    "苏州": (31.3, 120.6), "无锡": (31.5, 120.3), "温州": (28.0, 120.7), "嘉兴": (30.7, 120.8),
    "绍兴": (30.0, 120.6), "台州": (28.7, 121.4), "金华": (29.1, 119.6), "南通": (32.0, 120.9),
    "常州": (31.8, 119.9), "扬州": (32.4, 119.4), "徐州": (34.3, 117.2), "盐城": (33.4, 120.1),
    "湖州": (30.9, 120.1), "丽水": (28.5, 119.9), "衢州": (28.9, 118.9), "舟山": (30.0, 122.1),
    # 山东重点城市
    "烟台": (37.5, 121.4), "潍坊": (36.7, 119.1), "淄博": (36.8, 118.1), "临沂": (35.1, 118.3),
    "威海": (37.5, 122.1), "泰安": (36.2, 117.1), "德州": (37.5, 116.3), "济宁": (35.4, 116.6),
    "菏泽": (35.2, 115.4), "聊城": (36.5, 115.9), "滨州": (37.4, 118.0), "东营": (37.4, 118.5),
    # 四川重点城市
    "德阳": (31.1, 104.4), "绵阳": (31.5, 104.7), "乐山": (29.6, 103.8), "雅安": (29.9, 103.0),
    "南充": (30.8, 106.1), "达州": (31.2, 107.5), "泸州": (28.9, 105.4), "宜宾": (28.8, 104.6),
    "自贡": (29.4, 104.8), "内江": (29.6, 105.1), "眉山": (30.1, 103.8), "广元": (32.4, 105.8),
    # 其他省份重点城市
    "洛阳": (34.6, 112.5), "永州": (26.4, 111.6), "抚州": (27.9, 116.3), "承德": (41.0, 117.9),
    "保定": (38.9, 115.5), "唐山": (39.6, 118.2), "廊坊": (39.0, 116.7), "桂林": (25.3, 110.3),
    "三亚": (18.3, 109.5), "柳州": (24.3, 109.4), "遵义": (27.7, 106.9), "大理": (25.7, 100.2),
    "丽江": (26.9, 100.2), "景洪": (22.0, 100.8), "普洱": (22.8, 101.0),
    # 海外城市
    "曼谷": (13.8, 100.5), "河内": (21.0, 105.9), "胡志明": (10.8, 106.7),
    "尖竹汶": (13.4, 102.0), "清迈": (18.8, 98.9),
}

WMO_WEATHER = {
    0: ("晴", "#ffc107"), 1: ("大部晴", "#ffc107"), 2: ("多云", "#8b949e"),
    3: ("阴天", "#8b949e"), 45: ("雾", "#8b949e"), 48: ("结冰雾", "#8b949e"),
    51: ("小毛毛雨", "#64b5f6"), 53: ("中毛毛雨", "#64b5f6"), 55: ("大毛毛雨", "#42a5f5"),
    61: ("小雨", "#42a5f5"), 63: ("中雨", "#2196f3"), 65: ("大雨", "#1565c0"),
    71: ("小雪", "#90caf9"), 73: ("中雪", "#64b5f6"), 75: ("大雪", "#42a5f5"),
    80: ("小阵雨", "#42a5f5"), 81: ("中阵雨", "#2196f3"), 82: ("大阵雨", "#1565c0"),
    95: ("雷暴", "#ff5252"), 96: ("雷暴+小冰雹", "#ff5252"), 99: ("雷暴+大冰雹", "#ff5252"),
}


def create_table(conn):
    """建表"""
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS city_weather (
        city TEXT PRIMARY KEY,
        latitude REAL,
        longitude REAL,
        fetch_time TEXT,
        forecast_days INTEGER,
        daily_data TEXT
    )""")
    conn.commit()


def fetch_single_city(city, lat, lon, forecast_days=7):
    """获取单个城市天气"""
    conn = http.client.HTTPSConnection('api.open-meteo.com')
    url = f'/v1/forecast?latitude={lat}&longitude={lon}&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max&forecast_days={forecast_days}'
    try:
        conn.request('GET', url, headers={'User-Agent': 'python-requests/2.31.0'})
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        data = json.loads(resp.read())
        daily = data.get('daily', {})
        dates = daily.get('time', [])
        rows = []
        codes = daily.get('weathercode', [])
        tmaxs = daily.get('temperature_2m_max', [])
        tmins = daily.get('temperature_2m_min', [])
        precips = daily.get('precipitation_sum', [])
        winds = daily.get('windspeed_10m_max', [])
        for i in range(len(dates)):
            code = codes[i] if i < len(codes) else -1
            desc, color = WMO_WEATHER.get(code, ("未知", "#8b949e"))
            tmax = tmaxs[i] if i < len(tmaxs) else None
            tmin = tmins[i] if i < len(tmins) else None
            precip = precips[i] if i < len(precips) else None
            wind = winds[i] if i < len(winds) else None
            rows.append({
                "date": dates[i],
                "code": code, "desc": desc, "color": color,
                "tmax": tmax, "tmin": tmin, "precip": precip, "wind": wind,
            })
        conn.close()
        return rows
    except Exception as e:
        conn.close()
        return None


def update_weather(conn, force=False):
    """
    更新天气数据
    force=True 强制更新, 否则只更新超过6小时的数据
    """
    c = conn.cursor()
    create_table(conn)

    now = datetime.now()
    cutoff = (now - timedelta(hours=6)).strftime('%Y-%m-%d %H:%M:%S')

    # 检查需要更新的城市
    if force:
        cities_to_update = list(CITIES.items())
    else:
        c.execute("SELECT city FROM city_weather WHERE fetch_time > ?", (cutoff,))
        fresh = set(r[0] for r in c.fetchall())
        cities_to_update = [(k, v) for k, v in CITIES.items() if k not in fresh]

    if not cities_to_update:
        print(f"  所有 {len(CITIES)} 个城市天气数据均有效，无需更新")
        return

    print(f"  需要更新 {len(cities_to_update)} 个城市天气...")
    success = 0
    fail = 0

    for i, (city, (lat, lon)) in enumerate(cities_to_update):
        daily_data = fetch_single_city(city, lat, lon)
        if daily_data:
            c.execute(
                "INSERT OR REPLACE INTO city_weather VALUES (?,?,?,?,?,?)",
                (city, lat, lon, now.strftime('%Y-%m-%d %H:%M:%S'), 7, json.dumps(daily_data, ensure_ascii=False))
            )
            success += 1
        else:
            fail += 1

        # 每5个城市暂停1秒，避免被限流
        if (i + 1) % 5 == 0:
            time.sleep(1)

    conn.commit()
    print(f"  更新完成: 成功{success}, 失败{fail}")


def get_city_weather(conn, city_name):
    """查询城市天气（支持模糊匹配）"""
    c = conn.cursor()
    # 精确匹配
    c.execute("SELECT daily_data FROM city_weather WHERE city = ?", (city_name,))
    row = c.fetchone()
    if row:
        return json.loads(row[0])
    # 模糊匹配：从city_name中提取关键字
    for key in CITIES:
        if key in city_name:
            c.execute("SELECT daily_data FROM city_weather WHERE city = ?", (key,))
            row = c.fetchone()
            if row:
                return json.loads(row[0])
    return None


def get_weather_summary(conn, city_names):
    """
    获取多个城市的天气摘要
    city_names: list of full names like ["广东省深圳市", "上海上海市"]
    返回: {city_short: (weather_rows, order_count)}
    """
    result = {}
    for full_name in set(city_names):
        # 提取短名称
        short = None
        for key in CITIES:
            if key in full_name:
                short = key
                break
        if not short:
            continue

        daily_data = get_city_weather(conn, short)
        if daily_data:
            result[short] = daily_data

    return result


if __name__ == "__main__":
    now = datetime.now()
    print(f"🌤 获取全国城市天气... {now.strftime('%Y-%m-%d %H:%M')}", flush=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    # 建表
    create_table(conn)

    # 检查是否需要更新
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM city_weather")
    count = c.fetchone()[0]

    if count == 0:
        print(f"  首次获取全部 {len(CITIES)} 个城市...")
        update_weather(conn, force=True)
    else:
        print(f"  数据库已有 {count} 个城市天气数据")
        update_weather(conn, force=False)

    # 验证
    c.execute("SELECT COUNT(*), MIN(fetch_time), MAX(fetch_time) FROM city_weather")
    total, earliest, latest = c.fetchone()
    print(f"  数据库总计: {total} 个城市 | 最早: {earliest} | 最新: {latest}")

    conn.close()
    print("✅ 天气数据更新完成", flush=True)

import sys as _sys
_orig_excepthook = _sys.excepthook
def _global_excepthook(exc_type, exc_val, exc_tb):
    logger.exception("Unhandled exception", exc_info=(exc_type, exc_val, exc_tb))
    _orig_excepthook(exc_type, exc_val, exc_tb)
_sys.excepthook = _global_excepthook
