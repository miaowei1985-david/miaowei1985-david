#!/usr/bin/env python3
"""
全部店铺仓库需求日报 + AJ工厂发货排程 + 目的地天气 — HTML邮件发送
"""
import sqlite3, json, smtplib, os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.header import Header
from collections import defaultdict
import erp_metrics_tracker as tracker
import erp_config
from erp_config import DB_PATH, EMAIL_FROM, EMAIL_TO_FULL, send_email, setup_logger
setup_logger("warehouse_demand", "/tmp/erp_warehouse_demand.log")

AJ_WAREHOUSE = "金枕榴莲泰国直发AJ工厂"
DAILY_LIMIT_KG = 5000

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")
now = datetime.now()
SEQ_KEY = "/tmp/seq_warehouse_" + now.strftime("%Y%m%d")
if os.path.exists(SEQ_KEY):
    SEQ = int(open(SEQ_KEY).read().strip()) + 1
else:
    SEQ = 1
open(SEQ_KEY, "w").write(str(SEQ))
SEQ_STR = now.strftime("%Y%m%d") + "-" + str(SEQ)

# ===== 数据加载（全部店铺） =====
wait_check = [dict(r) for r in conn.execute('SELECT * FROM erp_wait_check').fetchall()]
wait_send = [dict(r) for r in conn.execute('SELECT * FROM erp_wait_send_self').fetchall()]
finished = [dict(r) for r in conn.execute('SELECT * FROM erp_finished').fetchall()]

# ===== AJ工厂发货排程 =====
def load_weight_map(conn):
    weight_map = {}
    for r in conn.execute('SELECT sku_name, weight FROM product_weight'):
        weight_map[r[0]] = r[1]
    return weight_map

def load_aj_orders(conn):
    orders = []
    for tbl in ["erp_wait_check", "erp_wait_send_self"]:
        exists = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{tbl}'").fetchone()
        if not exists:
            continue
        rows = conn.execute(f"SELECT tid, estimateConsignTime, orderItemList, paid, receiverCityName, receiverProvinceName FROM {tbl} WHERE warehouseName=?", (AJ_WAREHOUSE,)).fetchall()
        for r in rows:
            tid, deadline, raw, paid, city, prov = r
            if not raw:
                continue
            try:
                items = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(items, list):
                    for item in items:
                        sku_name = (item.get("skuName") or "").strip()
                        spu_name = (item.get("spuName") or "").strip()
                        qty = int(item.get("goodsCount", 1) or 1)
                        orders.append({
                            "tid": tid, "deadline": deadline,
                            "sku_name": sku_name, "spu_name": spu_name,
                            "qty": qty, "paid": float(paid or 0),
                            "city": city or prov or "未知",
                        })
            except:
                pass
    return orders

def load_weather_data(conn):
    """从city_weather表加载天气数据到内存，返回 {city_short: weather_rows}"""
    c = conn.cursor()
    c.execute("SELECT city FROM city_weather")
    known = set(r[0] for r in c.fetchall())

    weather_map = {}
    c.execute("SELECT city, daily_data FROM city_weather")
    for city, data in c.fetchall():
        weather_map[city] = json.loads(data)

    return known, weather_map

def extract_city_short(full_name, known):
    for city in known:
        if city in full_name:
            return city
    for keyword in ["市", "州", "盟", "地区"]:
        idx = full_name.find(keyword)
        if idx > 0:
            parts = full_name.replace("省", "|").replace("自治区", "|").split("|")
            city_part = parts[-1] if len(parts) > 1 else full_name
            short = city_part.replace(keyword, "")
            if short in known:
                return short
    return None

def calc_taste_score(order, weather_map, known, target_date):
    """
    计算订单到达日的榴莲最佳品味温度评分
    最佳范围: 25°C ~ 33°C
    到达前一天温度越接近25-33°C范围，评分越高
    返回: 0-100分，100=完美(29°C左右)，<50=不适宜
    """
    city = order.get("city", "")
    if not city:
        return 50
    short = extract_city_short(city, known)
    if not short:
        return 50
    weather_rows = weather_map.get(short)
    if not weather_rows:
        return 50

    # 到达日的前一天温度
    arrive_date_minus_1 = None
    for wr in weather_rows:
        if wr["date"] == target_date:
            arrive_date_minus_1 = wr
            break
    if arrive_date_minus_1 is None:
        return 50

    tmax = arrive_date_minus_1.get("tmax")
    tmin = arrive_date_minus_1.get("tmin")
    if tmax is None:
        return 50

    # 用日均温度作为评判
    avg_temp = (tmax + tmin) / 2 if tmin is not None else tmax

    # 评分: 25-33°C最佳，偏离越多分越低
    if 25 <= avg_temp <= 33:
        # 29°C最理想，线性评分
        score = 100 - abs(avg_temp - 29) * 12.5  # 25°C或33°C时得50分
    elif avg_temp < 25:
        score = max(0, 50 - (25 - avg_temp) * 10)
    else:
        score = max(0, 50 - (avg_temp - 33) * 10)

    return score

def allocate_aj_orders(orders, weight_map, weather_map, known):
    """
    排程算法（含最佳品味温度维度）:
    1. 按 deadline 排序（紧急优先）
    2. 同 deadline 内，按到达前一天品味温度评分排序（最佳25-33°C优先发）
    3. 同类SKU合并
    4. 每天塞满5吨，周六不发货
    """
    for o in orders:
        sn = o["sku_name"]
        spn = o["spu_name"]
        if sn in weight_map:
            o["weight"] = weight_map[sn]
        elif spn in weight_map:
            o["weight"] = weight_map[spn]
        else:
            o["weight"] = 0

    available_dates = []
    for i in range(1, 30):
        d = now + timedelta(days=i)
        if d.weekday() != 5:
            available_dates.append(d)

    # 多轮分配：优先排 deadline + 品味温度评分高的
    for date_idx, d in enumerate(available_dates):
        d_str = d.strftime("%Y-%m-%d")
        arrive_date = (d + timedelta(days=6)).strftime("%Y-%m-%d")

        # 给所有未分配的订单计算到达前一天品味温度评分
        for o in orders:
            if "ship_date" not in o:
                o["_taste_score"] = calc_taste_score(o, weather_map, known, arrive_date)

        # 排序：deadline优先 → 品味温度评分降序(最佳优先) → SKU合并
        unallocated = [o for o in orders if "ship_date" not in o]
        unallocated.sort(key=lambda x: (
            x.get("deadline") or "9999",
            -x.get("_taste_score", 50),  # 评分高的先排（降序）
            x.get("sku_name") or x.get("spu_name") or "",
        ))

        # 按排序顺序塞入当天，直到5吨
        day_weight = 0
        for order in unallocated:
            if order["weight"] == 0:
                # 占位单放到第一天
                if date_idx == 0 or day_weight < DAILY_LIMIT_KG:
                    order["ship_date"] = d_str
                    order["ship_date_dt"] = d
                continue

            remaining = DAILY_LIMIT_KG - day_weight
            if order["weight"] <= remaining:
                day_weight += order["weight"]
                order["ship_date"] = d_str
                order["ship_date_dt"] = d
            # 装不下的留到下一轮

    # 组装结果
    day_allocations = defaultdict(list)
    day_weight_map = defaultdict(float)
    for order in orders:
        sd = order.get("ship_date")
        if sd:
            day_allocations[sd].append(order)
            day_weight_map[sd] += order["weight"]
        else:
            day_allocations["超期"].append(order)

    return day_allocations, day_weight_map, available_dates

aj_weight_map = load_weight_map(conn)
aj_orders = load_aj_orders(conn)

# ===== 目的地天气分析（从city_weather表读取，conn关闭前完成） =====
_weather_known, _weather_map = load_weather_data(conn)

aj_day_alloc, aj_day_weight, aj_avail_dates = allocate_aj_orders(aj_orders, aj_weight_map, _weather_map, _weather_known)

conn.close()

# ===== 指标趋势追踪 =====
prev = tracker.load_prev('warehouse')

# 预加载AJ订单目的地天气到内存，供HTML渲染使用
aj_dest_cities = defaultdict(int)
for o in aj_orders:
    city = o.get("city", "")
    if city:
        aj_dest_cities[city] += 1
def total_paid(rows):
    s = 0
    for r in rows:
        try: s += float(r.get('paid') or 0)
        except: pass
    return s

wc_cnt, wc_paid = len(wait_check), total_paid(wait_check)
ws_cnt, ws_paid = len(wait_send), total_paid(wait_send)

# ===== 复购分析 =====
def calc_repurchase(rows):
    """统计重复购买客户数。返回 (总客户数, 复购客户数, 复购订单数, 复购金额)"""
    cust_orders = defaultdict(lambda: [0, 0.0])  # customerId -> [count, paid]
    for r in rows:
        cid = r.get('customerId', '')
        if not cid:
            continue
        cust_orders[cid][0] += 1
        try: cust_orders[cid][1] += float(r.get('paid') or 0)
        except: pass
    total_cust = len(cust_orders)
    repeat_cust = 0
    repeat_orders = 0
    repeat_paid = 0.0
    for cid, (cnt, paid) in cust_orders.items():
        if cnt >= 2:
            repeat_cust += 1
            repeat_orders += cnt
            repeat_paid += paid
    return total_cust, repeat_cust, repeat_orders, repeat_paid

all_rows = wait_check + wait_send + finished
total_cust, repeat_cust, repeat_orders, repeat_paid = calc_repurchase(all_rows)
repeat_rate = f'{repeat_cust / total_cust * 100:.1f}%' if total_cust > 0 else '0%'

def shop_breakdown(rows):
    shops = defaultdict(lambda: [0, 0.0])
    for r in rows:
        sn = r.get('shopName', '') or '未知'
        shops[sn][0] += 1
        try: shops[sn][1] += float(r.get('paid') or 0)
        except: pass
    return sorted(shops.items(), key=lambda x: -x[1][0])

wc_shops = shop_breakdown(wait_check)
ws_shops = shop_breakdown(wait_send)


def warehouse_summary(rows):
    wh = defaultdict(lambda: [0, 0.0])
    for r in rows:
        wn = r.get('warehouseName', '') or '未知'
        wh[wn][0] += 1
        try: wh[wn][1] += float(r.get('paid') or 0)
        except: pass
    return sorted(wh.items(), key=lambda x: -x[1][0])

wc_wh = warehouse_summary(wait_check)
ws_wh = warehouse_summary(wait_send)

# ===== SKU 汇总 =====

# ===== HTML 生成 =====
ACCENT = '#00d4ff'
GREEN = '#00e676'
AMBER = '#ffc107'
RED = '#ff5252'
DARK_BG = '#0d1117'
CARD_BG = '#161b22'
TABLE_HEAD = '#1c2333'
BORDER = '#30363d'
TEXT = '#c9d1d9'
TEXT_DIM = '#8b949e'
TEXT_BRIGHT = '#ffffff'

def status_tag(text, color):
    return f'<span class="status-tag" style="color:{color}">{text}</span>'

def sec_num(n):
    return f'{n:02d}'

def metric_card(value, label, color=ACCENT, trend=""):
    return (
        f'<div class="metric-card">'
        f'<div class="metric-value" style="color:{color}">{value}{trend}</div>'
        f'<div class="metric-label">{label}</div></div>'
    )

html = f'''<div class="report-wrapper">
<div class="report-header">
<h1>全部店铺 仓库需求日报</h1>
<p class="timestamp">生成时间: {now.strftime("%Y-%m-%d %H:%M:%S")}</p>
</div>

<!-- 01 核心指标 -->
<div class="section">
<h2 class="section-title"><span class="sec-num">{sec_num(1)}</span>核心指标 · KPI OVERVIEW</h2>
<div class="metric-grid">
{metric_card(f'{wc_cnt:,}', '待审核订单', AMBER, tracker.badge(wc_cnt, 'wc_cnt', prev))}
{metric_card(f'{wc_paid:,.0f}元', '待审核金额', ACCENT, tracker.badge(wc_paid, 'wc_paid', prev))}
{metric_card(f'{ws_cnt:,}', '待发货订单', GREEN, tracker.badge(ws_cnt, 'ws_cnt', prev))}
{metric_card(f'{ws_paid:,.0f}元', '待发货金额', ACCENT, tracker.badge(ws_paid, 'ws_paid', prev))}
{metric_card(f'{repeat_cust:,}({repeat_rate})', '复购客户', '#e040fb', tracker.badge(repeat_cust, 'repeat_cust', prev))}
{metric_card(f'{repeat_orders:,}', '复购订单', AMBER, tracker.badge(repeat_orders, 'repeat_orders', prev))}
{metric_card(f'{repeat_paid:,.0f}元', '复购金额', ACCENT, tracker.badge(repeat_paid, 'repeat_paid', prev))}
{metric_card(f'{len(wc_wh):,}', '涉及仓库数', TEXT_BRIGHT)}
</div>
</div>

<!-- 02 店铺分布 -->
<div class="section">
<h2 class="section-title"><span class="sec-num">{sec_num(2)}</span>店铺分布 · SHOP BREAKDOWN</h2>
<h3 class="sub-title">待审核 — 各店铺订单</h3>
<table class="data-table">
<thead><tr><th>店铺</th><th class="r">订单数</th><th class="r">占比</th><th class="r">实收金额</th></tr></thead>
<tbody>
'''

for sn, (cnt, paid) in wc_shops:
    pct = cnt / wc_cnt * 100 if wc_cnt else 0
    html += f'<tr><td><b>{sn}</b></td><td class="r">{cnt:,}</td><td class="r">{pct:.1f}%</td><td class="r">{paid:,.0f}元</td></tr>'
html += f'<tr class="row-total"><td>合计</td><td class="r">{wc_cnt:,}</td><td class="r">100%</td><td class="r">{wc_paid:,.0f}元</td></tr>'
html += '</tbody></table>'

html += '<h3 class="sub-title">待发货 — 各店铺订单</h3>'
html += '<table class="data-table"><thead><tr><th>店铺</th><th class="r">订单数</th><th class="r">占比</th><th class="r">实收金额</th></tr></thead><tbody>'
for sn, (cnt, paid) in ws_shops:
    pct = cnt / ws_cnt * 100 if ws_cnt else 0
    html += f'<tr><td><b>{sn}</b></td><td class="r">{cnt:,}</td><td class="r">{pct:.1f}%</td><td class="r">{paid:,.0f}元</td></tr>'
html += f'<tr class="row-total"><td>合计</td><td class="r">{ws_cnt:,}</td><td class="r">100%</td><td class="r">{ws_paid:,.0f}元</td></tr>'
html += '</tbody></table></div>'

# 03 仓库汇总
html += f'''
<div class="section">
<h2 class="section-title"><span class="sec-num">{sec_num(3)}</span>仓库需求汇总 · WAREHOUSE DEMAND</h2>
<h3 class="sub-title">待审核 — 各仓库汇总</h3>
<table class="data-table compact">
<thead><tr><th>仓库</th><th class="r">订单数</th><th class="r">占比</th><th class="r">金额</th></tr></thead>
<tbody>
'''
wh_total_cnt = 0
wh_total_paid = 0
for wn, (cnt, paid) in wc_wh:
    pct = cnt / wc_cnt * 100 if wc_cnt else 0
    html += f'<tr><td>{wn}</td><td class="r">{cnt:,}</td><td class="r">{pct:.1f}%</td><td class="r">{paid:,.0f}元</td></tr>'
    wh_total_cnt += cnt
    wh_total_paid += paid
html += f'<tr class="row-total"><td>合计</td><td class="r">{wh_total_cnt:,}</td><td class="r">100%</td><td class="r">{wh_total_paid:,.0f}元</td></tr>'
html += '</tbody></table>'

html += '<h3 class="sub-title">待发货 — 各仓库汇总</h3>'
html += '<table class="data-table compact"><thead><tr><th>仓库</th><th class="r">订单数</th><th class="r">占比</th><th class="r">金额</th></tr></thead><tbody>'
wh2_total_cnt = 0
wh2_total_paid = 0
for wn, (cnt, paid) in ws_wh:
    pct = cnt / ws_cnt * 100 if ws_cnt else 0
    html += f'<tr><td>{wn}</td><td class="r">{cnt:,}</td><td class="r">{pct:.1f}%</td><td class="r">{paid:,.0f}元</td></tr>'
    wh2_total_cnt += cnt
    wh2_total_paid += paid
html += f'<tr class="row-total"><td>合计</td><td class="r">{wh2_total_cnt:,}</td><td class="r">100%</td><td class="r">{wh2_total_paid:,.0f}元</td></tr>'
html += '</tbody></table></div>'

# 05 AJ工厂发货排程
aj_total_orders = 0
aj_total_weight = 0
aj_overdue = 0
for d_str, orders in aj_day_alloc.items():
    aj_total_orders += len(orders)
    aj_total_weight += aj_day_weight[d_str]
    if d_str == "超期":
        aj_overdue += len(orders)

aj_days_count = len([d for d in aj_day_alloc if d != "超期"])

# 天气工具：从预加载的aj_city_weather字典读取
def get_day_weather_risky(orders, ship_date_str):
    """
    扫描某日排程所有订单目的地，只显示有风险的天气
    风险条件：到达日温度>30°C 或 降水>5mm
    返回: [(city_short, cnt, weather_rows), ...] 按订单数降序
    """
    if not ship_date_str:
        return []
    try:
        ship_dt = datetime.strptime(ship_date_str, "%Y-%m-%d")
        arrive_dt = ship_dt + timedelta(days=6)
        arrive_str = arrive_dt.strftime("%Y-%m-%d")
    except:
        return []

    city_counts = defaultdict(int)
    city_paid = defaultdict(float)
    for o in orders:
        city = o.get("city", "")
        if city:
            city_counts[city] += 1
            city_paid[city] += o.get("paid", 0)

    results = []
    for full_name, cnt in sorted(city_counts.items(), key=lambda x: -x[1]):
        short = extract_city_short(full_name, _weather_known)
        if not short:
            continue
        weather_rows = _weather_map.get(short)
        if not weather_rows:
            continue

        # 找到到达日的天气
        arrive_weather = None
        for wr in weather_rows:
            if wr["date"] == arrive_str:
                arrive_weather = wr
                break
        if not arrive_weather:
            arrive_weather = weather_rows[-1]

        tmax = arrive_weather.get("tmax")
        precip = arrive_weather.get("precip") or 0

        # 有风险的才显示
        if (tmax is not None and tmax > 30) or precip > 5:
            results.append((short, cnt, city_paid.get(full_name, 0.0), weather_rows))

    return results

# 渲染每日排程的天气条
def render_weather_bar(city_short, cnt, weather_rows, ship_date_str):
    """渲染某日排程的目的地天气条（显示预计到达日天气，即ship_date+6天）"""
    if not weather_rows or not ship_date_str:
        return ""
    # 计算预计到达日（发货+6天）
    try:
        ship_dt = datetime.strptime(ship_date_str, "%Y-%m-%d")
        arrive_dt = ship_dt + timedelta(days=6)
        arrive_str = arrive_dt.strftime("%Y-%m-%d")
    except:
        arrive_str = ""

    # 找到到达日的天气
    arrive_weather = None
    for wr in weather_rows:
        if wr["date"] == arrive_str:
            arrive_weather = wr
            break
    if not arrive_weather:
        arrive_weather = weather_rows[-1]

    tmax = arrive_weather.get("tmax")
    tmin = arrive_weather.get("tmin")
    precip = arrive_weather.get("precip") or 0
    wind = arrive_weather.get("wind") or 0

    # 风险判定：温度>30°C对榴莲是风险，暴雨(降水>15mm)是风险
    risk = "low"
    risk_parts = []
    if tmax is not None and tmax > 30:
        risk = "high" if (risk == "high" or precip > 15) else "medium"
        risk_parts.append(f"🌡 高温{tmax:.0f}°C")
    if precip > 15:
        risk = "high"
        risk_parts.append(f"🌧 暴雨{precip:.0f}mm")
    elif precip > 5:
        if risk == "low":
            risk = "medium"
        risk_parts.append(f"🌧 大雨{precip:.0f}mm")

    if risk == "low":
        risk_label = "✓ 适宜"
    else:
        risk_label = "⚠ " + " + ".join(risk_parts)

    risk_color = RED if risk == "high" else (AMBER if risk == "medium" else GREEN)

    tmax_str = f"{tmax:.0f}°C" if tmax is not None else "?"
    tmin_str = f"{tmin:.0f}°C" if tmin is not None else "?"
    temp_risk_class = "risk-medium" if (tmax is not None and tmax > 30) else ""

    s = f'<div class="day-weather">'
    s += f'<span class="w-city">{city_short} ({cnt}单)</span>'
    s += f'<span class="w-date">预计到达 {arrive_dt.strftime("%m/%d") if arrive_dt else "?"}</span>'
    s += f'<span class="w-desc" style="color:{arrive_weather.get("color", TEXT_DIM)}">{arrive_weather.get("desc", "未知")}</span>'
    s += f'<span class="w-temp {temp_risk_class}">{tmin_str}~{tmax_str}</span>'
    if precip > 0:
        s += f'<span class="w-rain">💧{precip:.0f}mm</span>'
    if wind > 0:
        s += f'<span class="w-wind">💨{wind:.0f}km/h</span>'
    s += f'<span class="w-risk risk-{risk}" style="color:{risk_color}">{risk_label}</span>'
    s += '</div>'
    return s

html += f'''
<div class="section">
<h2 class="section-title"><span class="sec-num">{sec_num(4)}</span>泰国AJ工厂 发货排程 · AJ SHIPPING SCHEDULE</h2>
<p style="color:{TEXT_DIM};font-size:13px;margin:0 0 12px;">每日上限: {DAILY_LIMIT_KG:,}kg | 周六休息 | 同类SKU合并打包 | 附: 高温(>30°C)/降雨(>5mm)风险目的地</p>
<div class="metric-grid">
{metric_card(f'{aj_total_orders:,}', 'AJ总订单', AMBER)}
{metric_card(f'{aj_total_weight:,.0f}kg', 'AJ总重量', ACCENT)}
{metric_card(f'{aj_days_count:,}', '排程天数', GREEN)}
{metric_card(f'{aj_overdue}', '超期订单', RED)}
</div>
'''

for d_str in sorted(aj_day_alloc.keys()):
    if d_str == "超期":
        orders = aj_day_alloc["超期"]
        w = aj_day_weight["超期"]
        html += f'<p class="wh-label" style="color:{RED};">⚠ 超期 ({len(orders)} 单 / {w:,.0f}kg)</p>'
        html += '<table class="data-table compact"><thead><tr><th>SKU</th><th class="r">订单数</th><th class="r">数量</th><th class="r">最晚发货时间</th><th>订单号</th></tr></thead><tbody>'
        sku_grp = defaultdict(list)
        for o in orders:
            sku_grp[o["sku_name"] or o["spu_name"]].append(o)
        for sn, grp in sorted(sku_grp.items()):
            tids = ", ".join(set(o["tid"] for o in grp[:5]))
            html += f'<tr><td>{sn[:60]}</td><td class="r b">{len(grp)}</td><td class="r">{sum(o["qty"] for o in grp)}</td><td class="r" style="color:{RED};">{grp[0].get("deadline","")}</td><td style="font-size:11px;color:{TEXT_DIM};">{tids}</td></tr>'
        html += '</tbody></table>'
        continue

    orders = aj_day_alloc[d_str]
    w = aj_day_weight[d_str]
    pct = w / DAILY_LIMIT_KG * 100 if w else 0
    date_color = GREEN if pct < 80 else (AMBER if pct < 100 else RED)

    # 按SKU分组
    sku_grp = defaultdict(lambda: {"orders": [], "qty": 0, "weight": 0.0})
    for o in orders:
        k = o["sku_name"] or o["spu_name"]
        sku_grp[k]["orders"].append(o)
        sku_grp[k]["qty"] += o["qty"]
        sku_grp[k]["weight"] += o["weight"]

    html += f'<p class="wh-label" style="color:{date_color};">{d_str} ({len(orders)} 单 / {w:,.0f}kg / {pct:.1f}%)</p>'
    html += '<table class="data-table compact"><thead><tr><th>SKU</th><th class="r">订单数</th><th class="r">数量</th><th class="r">重量</th><th class="r">占比</th><th>订单号</th></tr></thead><tbody>'

    for sn in sorted(sku_grp.keys(), key=lambda x: -sku_grp[x]["weight"]):
        g = sku_grp[sn]
        s_pct = g["weight"] / w * 100 if w else 0
        tids = ", ".join(set(o["tid"] for o in g["orders"]))
        if len(tids) > 100:
            tids = tids[:100] + "..."
        html += f'<tr><td>{sn[:60]}</td><td class="r b">{len(g["orders"])}</td><td class="r">{g["qty"]}</td><td class="r">{g["weight"]:,.0f}kg</td><td class="r">{s_pct:.1f}%</td><td style="font-size:11px;color:{TEXT_DIM};">{tids}</td></tr>'

    html += f'<tr class="row-total"><td>合计</td><td class="r">{len(orders)}</td><td class="r">{sum(o["qty"] for o in orders)}</td><td class="r">{w:,.0f}kg</td><td class="r">100%</td><td></td></tr>'
    html += '</tbody></table>'

    # 目的地风险天气（合并相同风险的城市为一行）
    day_risky_weather = get_day_weather_risky(orders, d_str)
    if day_risky_weather:
        html += '<p class="wh-label" style="color:' + AMBER + ';font-size:12px;">⚠ 风险天气目的地</p>'
        # 按风险类型分组（风险组按降水/温度/品味评分排序）
        risk_groups = defaultdict(lambda: {"cities": [], "total_orders": 0, "total_paid": 0.0, "sort_tmax": -999, "sort_precip": 0, "sort_taste": 100})
        for city_short, cnt, paid, weather_rows in day_risky_weather:
            ship_dt2 = datetime.strptime(d_str, "%Y-%m-%d")
            arrive_dt2 = ship_dt2 + timedelta(days=6)
            arrive_str2 = arrive_dt2.strftime("%Y-%m-%d")
            arrive_w = None
            for wr in weather_rows:
                if wr["date"] == arrive_str2:
                    arrive_w = wr
                    break
            if not arrive_w:
                arrive_w = weather_rows[-1]

            # 计算到达前一天品味温度评分
            arrive_date_prev = None
            for wr in weather_rows:
                if wr["date"] == arrive_str2:
                    idx = weather_rows.index(wr)
                    if idx > 0:
                        arrive_date_prev = weather_rows[idx - 1]
            if arrive_date_prev is None:
                arrive_date_prev = weather_rows[0]
            prev_tmax = arrive_date_prev.get("tmax")
            prev_tmin = arrive_date_prev.get("tmin")
            avg_prev = ((prev_tmax or 29) + (prev_tmin or 29)) / 2
            if 25 <= avg_prev <= 33:
                taste = 100 - abs(avg_prev - 29) * 12.5
            elif avg_prev < 25:
                taste = max(0, 50 - (25 - avg_prev) * 10)
            else:
                taste = max(0, 50 - (avg_prev - 33) * 10)

            tmax = arrive_w.get("tmax")
            precip = arrive_w.get("precip") or 0
            risk_parts = []
            if tmax is not None and tmax > 30:
                risk_parts.append(f"🌡高温{tmax:.0f}°C")
            if precip > 15:
                risk_parts.append(f"🌧暴雨{precip:.0f}mm")
            elif precip > 5:
                risk_parts.append(f"🌧大雨{precip:.0f}mm")

            risk_key = " | ".join(risk_parts)
            if not risk_key:
                risk_key = "✓ 正常"

            # 用组内最高的温度和降水、最低的品味评分作为排序基准
            grp = risk_groups[risk_key]
            if (tmax or -999) > grp["sort_tmax"]:
                grp["sort_tmax"] = tmax if tmax is not None else -999
            if precip > grp["sort_precip"]:
                grp["sort_precip"] = precip
            if taste < grp["sort_taste"]:
                grp["sort_taste"] = taste
            grp["cities"].append({
                "name": city_short, "cnt": cnt, "paid": paid, "taste": taste,
                "tmax": tmax if tmax is not None else -999,
                "precip": precip,
            })
            grp["total_orders"] += cnt
            grp["total_paid"] += paid

        # 风险组按降水降序、温度降序、品味评分升序(差的前面)排序
        sorted_groups = sorted(risk_groups.items(),
                               key=lambda x: (-x[1]["sort_precip"], -x[1]["sort_tmax"], x[1]["sort_taste"]))

        for risk_label, grp in sorted_groups:
            # 组内城市按温度降序、降水降序、品味评分升序排列
            sorted_cities = sorted(grp["cities"], key=lambda x: (-x["tmax"], -x["precip"], x["taste"]))
            city_str = " ".join(
                f"{c['name']}({c['cnt']}单/{c['paid']:,.0f}元)"
                for c in sorted_cities
            )
            total_str = f'共{grp["total_orders"]}单/{grp["total_paid"]:,.0f}元'
            html += f'<div class="day-weather"><span class="w-risk risk-high" style="color:{AMBER}">{risk_label}</span><span class="w-date">预计到达 {arrive_dt2.strftime("%m/%d")}</span><span class="w-total">{total_str}</span><span class="w-cities">{city_str}</span></div>'

html += '</div>'  # close section 05

html += '</div>'  # close report-wrapper

# ===== CSS =====
css = f'''<style>
body {{ margin:0; padding:0; background:{DARK_BG}; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; color:{TEXT}; }}
.report-wrapper {{ max-width:95%; margin:0 auto; padding:24px 20px; }}
.report-header {{ border-bottom:2px solid {ACCENT}; padding-bottom:16px; margin-bottom:24px; }}
.report-header h1 {{ margin:0 0 6px; font-size:26px; font-weight:700; color:{TEXT_BRIGHT}; letter-spacing:1px; }}
.timestamp {{ margin:0; font-size:13px; color:{TEXT_DIM}; }}
.section {{ background:{CARD_BG}; border:1px solid {BORDER}; border-radius:10px; padding:20px; margin-bottom:20px; }}
.section-title {{ margin:0 0 16px; font-size:18px; font-weight:700; color:{ACCENT}; border-bottom:1px solid {BORDER}; padding-bottom:8px; }}
.sec-num {{ display:inline-block; background:{ACCENT}; color:{DARK_BG}; border-radius:4px; padding:1px 8px; margin-right:8px; font-size:16px; font-weight:800; }}
.sub-title {{ margin:18px 0 8px; font-size:15px; font-weight:600; color:{TEXT_BRIGHT}; }}
.metric-grid {{ display:grid; grid-template-columns:repeat(8,1fr); gap:10px; }}
.metric-card {{ background:{TABLE_HEAD}; border:1px solid {BORDER}; border-radius:8px; padding:14px 12px; text-align:center; }}
.metric-value {{ font-size:22px; font-weight:800; margin-bottom:4px; }}
.metric-label {{ font-size:12px; color:{TEXT_DIM}; }}
.trend-arrow {{ font-size:11px; font-weight:700; margin-left:4px; padding:1px 5px; border-radius:4px; }}
.trend-up {{ color:#00e676; background:rgba(0,230,118,0.1); }}
.trend-down {{ color:#ff5252; background:rgba(255,82,82,0.1); }}
.data-table {{ width:100%; border-collapse:collapse; font-size:13px; margin-bottom:12px; }}
.data-table thead th {{ background:{TABLE_HEAD}; color:{ACCENT}; font-weight:600; padding:8px 10px; border:1px solid {BORDER}; text-align:left; font-size:12px; text-transform:uppercase; letter-spacing:0.5px; }}
.data-table tbody td {{ padding:6px 10px; border:1px solid {BORDER}; color:{TEXT}; }}
.data-table.compact {{ font-size:12px; }}
.data-table.compact td {{ padding:4px 8px; }}
.data-table.compact th {{ padding:6px 8px; }}
.r {{ text-align:right; }}
.c {{ text-align:center; }}
.b {{ font-weight:700; }}
.row-total {{ background:{TABLE_HEAD}; font-weight:700; }}
.row-total td {{ color:{TEXT_BRIGHT}; }}
.row-subtotal td {{ background:rgba(255,255,255,0.03); font-weight:600; }}
.status-tag {{ font-weight:700; padding:2px 6px; border-radius:3px; background:rgba(255,255,255,0.05); }}
.wh-label {{ font-weight:600; color:{ACCENT}; margin:12px 0 4px; font-size:14px; }}
.day-weather {{ display:flex; align-items:center; gap:10px; background:{TABLE_HEAD}; border:1px solid {BORDER}; border-radius:6px; padding:6px 12px; margin:4px 0 8px; font-size:12px; }}
.day-weather .w-cities {{ font-weight:600; color:{TEXT_BRIGHT}; }}
.day-weather .w-date {{ color:{TEXT_DIM}; }}
.day-weather .w-total {{ font-weight:700; color:{ACCENT}; }}
.day-weather .w-risk {{ font-weight:700; padding:1px 6px; border-radius:3px; background:rgba(255,255,255,0.05); white-space:nowrap; }}
.w-risk.risk-medium {{ border:1px solid {AMBER}; }}
.w-risk.risk-high {{ border:1px solid {RED}; }}
</style>'''

html = css + html

# ===== 发送邮件 =====
import sys
if "--html" in sys.argv:
    print(html)
    sys.exit(0)
import sys
if '--html' in sys.argv:
    print(html)
    sys.exit(0)

# 仅当 --email 参数时发送邮件
if "--email" in sys.argv:
    msg = MIMEText(html, 'html', 'utf-8')
    msg['From'] = Header('88187402@qq.com')
    msg['To'] = ', '.join(EMAIL_TO_FULL)
    msg['Subject'] = Header(f'【服务器】仓库备货需求 {SEQ_STR}', 'utf-8')
    try:
        send_email(f'【服务器】仓库备货需求 {SEQ_STR}', html, EMAIL_TO_FULL)
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

# 保存本轮指标供下次对比
tracker.save('warehouse', {
    'wc_cnt': wc_cnt, 'wc_paid': wc_paid,
    'ws_cnt': ws_cnt, 'ws_paid': ws_paid,
    'repeat_cust': repeat_cust, 'repeat_orders': repeat_orders,
    'repeat_paid': repeat_paid,
})

import sys as _sys
_orig_excepthook = _sys.excepthook
def _global_excepthook(exc_type, exc_val, exc_tb):
    logger.exception("Unhandled exception", exc_info=(exc_type, exc_val, exc_tb))
    _orig_excepthook(exc_type, exc_val, exc_tb)
_sys.excepthook = _global_excepthook
