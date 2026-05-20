#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ERP 邮件内容仪表盘 — 内存缓存 + 预生成
端口: 9999
"""

import os
import sys
import json
import html as html_mod
import cgi
import shutil
import subprocess
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
import logging
logging.basicConfig(filename="/tmp/erp_dashboard_app.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


BASE = os.path.expanduser("~/pdd/Claudecode")
CACHE_TTL = 1800  # 30 分钟

UPLOAD_DIRS = {
    "finance": os.path.expanduser("~/pdd/Claudecode/uploads"),
    "after_sales": os.path.expanduser("~/pdd/Claudecode/uploads"),
    "weight": os.path.expanduser("~/pdd/Claudecode/产品规格"),
    "product_cost": os.path.expanduser("~/pdd/Claudecode/产品成本"),
}
TYPE_LABELS = {
    "finance": "财务对账",
    "after_sales": "售后数据",
    "weight": "规格重量",
    "product_cost": "产品成本",
}
for d in UPLOAD_DIRS.values():
    os.makedirs(d, exist_ok=True)

# ===== HTML 缓存 =====
_cache = {}  # {page_key: (timestamp, html_content)}

def get_cached_html(script_name):
    """有缓存且未过期则返回缓存，否则运行脚本并缓存"""
    now = time.time()
    if script_name in _cache:
        ts, content = _cache[script_name]
        if now - ts < CACHE_TTL:
            return content

    result = subprocess.run(
        [sys.executable, os.path.join(BASE, script_name), '--html'],
        capture_output=True, text=True, timeout=120, cwd=BASE,
    )
    # 过滤掉脚本的 console 输出，只保留 HTML
    content = result.stdout
    for marker in ['<!DOCTYPE', '<style>', '<html>']:
        idx = content.find(marker)
        if idx >= 0:
            content = content[idx:]
            break
    _cache[script_name] = (now, content)
    return content

def clear_cache():
    _cache.clear()
    global _logistics_cache
    _logistics_cache = None

# 物流页面缓存
_logistics_cache = None
def render_logistics_cached():
    global _logistics_cache
    try:
        now = time.time()
        if _logistics_cache and now - _logistics_cache[0] < CACHE_TTL:
            return _logistics_cache[1]
        html = render_logistics()
        if html:
            _logistics_cache = (now, html)
        return html
    except Exception as e:
        import traceback
        print(f"[logistics_cached] Error: {e}")
        traceback.print_exc()
        if _logistics_cache:
            return _logistics_cache[1]
        return f"<h1>物流页面加载失败</h1><pre>{e}</pre>"

# 通用页面缓存装饰器
def cached_page(key):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                now = time.time()
                cache_key = f"page:{key}"
                if cache_key in _cache and now - _cache[cache_key][0] < CACHE_TTL:
                    return _cache[cache_key][1]
                html = fn(*args, **kwargs)
                if html:
                    _cache[cache_key] = (now, html)
                return html
            except Exception as e:
                import traceback
                print(f"[cached_page:{key}] Error: {e}")
                traceback.print_exc()
                # Return stale cache if available, or empty string
                cache_key = f"page:{key}"
                if cache_key in _cache:
                    return _cache[cache_key][1]
                return f"<h1>Page error: {key}</h1><pre>{e}</pre>"
        return wrapper
    return decorator

# ===== 数据鲜度检测 =====

def _get_logistics_data(conn):
    """获取物流状态数据用于柱状图"""
    import sqlite3

    # 统计快递状态
    stats = {}
    for row in conn.execute("""
        SELECT t.operate_desc, COUNT(DISTINCT t.logistics_no)
        FROM logistics_trace t
        INNER JOIN (
            SELECT logistics_no, MAX(operate_time) as max_time
            FROM logistics_trace GROUP BY logistics_no
        ) latest ON t.logistics_no = latest.logistics_no AND t.operate_time = latest.max_time
        GROUP BY t.operate_desc
    """).fetchall():
        stats[row[0]] = row[1]

    signed = stats.get("已签收", 0)
    in_transit = stats.get("运输中", 0)
    delivering = stats.get("派送中", 0)
    problem = stats.get("问题件", 0)

    # 超时预警
    timeout_cnt = conn.execute("""
        SELECT COUNT(DISTINCT l.logistics_no)
        FROM logistics_trace l
        WHERE l.operate_desc = '已揽件'
        AND l.operate_time < datetime('now', '-72 hours')
        AND l.logistics_no NOT IN (
            SELECT DISTINCT logistics_no FROM logistics_trace WHERE operate_desc = '已签收'
        )
    """).fetchone()[0]

    # 今日签收
    today_signed = conn.execute("""
        SELECT COUNT(DISTINCT logistics_no)
        FROM logistics_trace
        WHERE operate_desc = '已签收' AND operate_time >= date('now')
    """).fetchone()[0]

    return {
        "signed": signed,
        "in_transit": in_transit,
        "delivering": delivering,
        "today_signed": today_signed,
        "timeout": timeout_cnt,
        "problem": problem
    }


def get_sync_status():
    """读取 sync_metadata，返回 HTML 状态卡片"""
    db_path = os.path.join(BASE, "erp_all.db")
    try:
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=30.0)
        rows = conn.execute("SELECT sync_time, table_name, row_count FROM sync_metadata ORDER BY sync_time DESC LIMIT 10").fetchall()
        conn.close()
        if not rows:
            return _render_sync_card("red", "从未同步", "无数据", "")
        sync_time = rows[0][0]
        tables = [(r[1], r[2]) for r in rows if r[1] != "__total__"]
        total = next((r[2] for r in rows if r[1] == "__total__"), None)
        from datetime import datetime
        try:
            st = datetime.fromisoformat(sync_time)
            hours = (datetime.now() - st).total_seconds() / 3600
        except Exception:
            hours = 999
        if hours < 3:
            color = "green"
            label = "数据正常"
        elif hours < 6:
            color = "yellow"
            label = "数据可能过期"
        else:
            color = "red"
            label = "数据陈旧"
        time_str = st.strftime("%m-%d %H:%M") if hours < 999 else sync_time
        table_spans = []
        for t, n in tables:
            table_spans.append("<span style=\"color:#8b949e;font-size:12px;\">" + t + ": " + "{:,}".format(n) + "</span>")
        # Add erp_all_orders count
        try:
            ao_conn = sqlite3.connect(db_path, timeout=30.0)
            ao_count = ao_conn.execute('SELECT COUNT(*) FROM erp_all_orders').fetchone()[0]
            ao_conn.close()
            table_spans.append('<span style="color:#8b949e;font-size:12px;">erp_all_orders: ' + '{:,}'.format(ao_count) + '</span>')
        except Exception:
            pass
        table_html = "".join(table_spans)
        return _render_sync_card(color, label, "最后同步: " + time_str, table_html)
    except Exception as e:
        return _render_sync_card("red", "读取失败", str(e), "")

def _render_sync_card(color, label, detail, table_html):
    colors = {"green": "#238636", "yellow": "#d29922", "red": "#f85149"}
    c = colors.get(color, "#f85149")
    parts = []
    parts.append('<div style="background:#161b22;border:1px solid ' + c + ';border-top:3px solid ' + c + ';border-radius:10px;padding:12px 16px;margin-bottom:20px;">')
    parts.append('  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">')
    parts.append('    <span style="color:' + c + ';font-weight:700;font-size:14px;">' + label + '</span>')
    parts.append('    <span style="color:#8b949e;font-size:12px;margin-left:auto;">' + detail + '</span>')
    parts.append("  </div>")
    parts.append('  <div style="display:flex;gap:12px;flex-wrap:wrap;">' + table_html + "</div>")
    parts.append("</div>")
    return "\n".join(parts)

# ===== 数据更新时间 =====
def get_last_sync_time():
    """从数据库读取最后数据同步时间"""
    import sqlite3
    db_path = os.path.join(BASE, "erp_all.db")
    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        row = conn.execute("SELECT sync_time FROM sync_metadata ORDER BY sync_time DESC LIMIT 1").fetchone()
        conn.close()
        if row:
            from datetime import datetime
            st = datetime.fromisoformat(row[0])
            return st.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return "未知"

_sync_time_cache = None
def get_sync_time_cached():
    global _sync_time_cache
    import time
    if _sync_time_cache is None or time.time() - _sync_time_cache[0] > 60:
        _sync_time_cache = (time.time(), get_last_sync_time())
    return _sync_time_cache[1]

# ===== 导航 =====
NAV_CSS = """<style>
body{margin:0;padding:0;background:#0d1117;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#c9d1d9}
.nav{background:#161b22;border-bottom:1px solid #30363d;padding:0 20px;display:flex;align-items:center;position:sticky;top:0;z-index:100;height:52px;backdrop-filter:blur(8px)}
.nav-logo{font-size:16px;font-weight:700;color:#fff;margin-right:28px}
.nav-logo span{color:#00d4ff}
.nav-links{display:flex;gap:2px}
.nav-links a{padding:8px 14px;border-radius:6px;font-size:13px;color:#8b949e;text-decoration:none;transition:all .15s;white-space:nowrap}
.nav-links a:hover,.nav-links a.active{background:#21262d;color:#fff}
.nav-links a.active{background:rgba(0,212,255,0.1);color:#00d4ff}
.nav-time{margin-left:auto;font-size:12px;color:#484f58}
.content{max-width:95%;margin:0 auto;padding:20px}
.drop-zone{border:2px dashed #30363d;border-radius:8px;padding:20px;text-align:center;cursor:pointer;transition:border-color .2s}
.drop-zone:hover,.drop-zone.dragover{border-color:#00d4ff;background:rgba(0,212,255,0.05)}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:#0d1117}
::-webkit-scrollbar-thumb{background:#30363d;border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:#484f58}
</style>"""

def nav(active_page):
    from datetime import datetime
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sync_time = get_sync_time_cached()
    pages = [('/', '首页', 'home'), ('/daily', '运营日报', 'daily'), ('/warehouse', '仓库需求', 'warehouse'), ('/waitcheck', '待审核订单', 'waitcheck'), ('/forecast', '销量预测', 'forecast'), ('/finance', '财务结算', 'finance'), ('/aftersales', '售后专题', 'aftersales'), ('/afterdashboard', '售后看板', 'aftersales_v2'), ('/logistics', '物流报告', 'logistics'), ('/exceptionorders', '异常件', 'exceptionorders'), ('/replaceorder', '换单补单', 'replaceorder'), ('/upload', '文件上传', 'upload')]
    links = ''
    for path, label, key in pages:
        cls = 'active' if key == active_page else ''
        links += f'<a href="{path}" class="{cls}">{label}</a>'
    return f'<div class="nav"><div class="nav-logo">数据中心</div><div class="nav-links">{links}</div><div class="nav-time">数据更新: {sync_time}</div></div>'

def page(title, active, content_html):
    return f'<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>{NAV_CSS}</head><body>{nav(active)}<div class="content">{content_html}</div></body></html>'

# ===== 页面渲染 =====
@cached_page('home')
def render_home():
    import sqlite3
    from datetime import datetime
    from collections import defaultdict as _dd
    DB_PATH = os.path.join(BASE, "erp_all.db")
    now = datetime.now()
    shop = "榴愿时刻工厂店"
    sync_card = get_sync_status()

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    
    # === 物流状态数据 ===
    logistics_data = _get_logistics_data(conn)

    # === Chart 1: 30-day order trend (line chart) ===
    daily_orders = {}
    for dt, cnt, amt in conn.execute(
        "SELECT SUBSTR(payTime,1,10), COUNT(*), SUM(CAST(COALESCE(realAmount,'0') AS REAL)) "
        "FROM erp_all_orders WHERE shopName = ? AND payTime IS NOT NULL GROUP BY 1",
        (shop,)).fetchall():
        daily_orders[dt] = {"count": cnt, "amount": amt}

    daily_shipped = {}
    for dt, cnt in conn.execute(
        "SELECT SUBSTR(consignTime,1,10), COUNT(*) "
        "FROM erp_all_orders WHERE shopName = ? AND consignTime IS NOT NULL GROUP BY 1",
        (shop,)).fetchall():
        daily_shipped[dt] = cnt

    daily_signed = {}
    for dt, cnt in conn.execute(
        "SELECT SUBSTR(consignTime,1,10), COUNT(*) "
        "FROM erp_all_orders WHERE shopName = ? AND consignTime IS NOT NULL AND traceStatusMsg LIKE '%已签收%' GROUP BY 1",
        (shop,)).fetchall():
        daily_signed[dt] = cnt

    all_30 = sorted(set(list(daily_orders.keys()) + list(daily_shipped.keys())))
    last_30 = all_30[-30:]

    chart_labels = [d[5:] for d in last_30]  # MM-DD
    chart_total = [daily_orders.get(d, {}).get("count", 0) for d in last_30]
    chart_shipped = [daily_shipped.get(d, 0) for d in last_30]
    chart_signed = [daily_signed.get(d, 0) for d in last_30]
    chart_amount = [daily_orders.get(d, {}).get("amount", 0) for d in last_30]

    # === Chart 2: SKU spec distribution (bar chart) ===
    sku_data = []
    for spu, sku, cnt in conn.execute(
        "SELECT COALESCE(spu_name,''), sku_name, COUNT(DISTINCT order_id) FROM order_line_items "
        "WHERE shop_name = ? AND sku_name != '' GROUP BY spu_name, sku_name ORDER BY COUNT(*) DESC LIMIT 10",
        (shop,)).fetchall():
        sku_data.append((spu, sku, cnt))

    sku_labels = []
    sku_counts = []
    for spu, sku, cnt in sku_data:
        raw = sku.strip()
        if ";" in raw:
            parts = raw.split(";", 1)
            label = parts[1].strip()
        elif "|" in raw:
            label = raw.replace("|", " | ")
        else:
            label = raw
        sku_labels.append(label)
        sku_counts.append(cnt)

    # === Chart 3: After-sales reasons (horizontal bar) ===
    as_reasons = []
    for reason, cnt in conn.execute(
        "SELECT primary_reason, COUNT(*) FROM after_sales "
        "WHERE shop_name = ? AND outbound_status = '已出库' AND primary_reason != '' "
        "GROUP BY primary_reason ORDER BY COUNT(*) DESC LIMIT 8",
        (shop,)).fetchall():
        as_reasons.append((reason, cnt))

    as_labels = [r[0] for r in as_reasons]
    as_counts = [r[1] for r in as_reasons]

    # === Chart 4: Order status (doughnut) ===
    statuses = []
    for st, cnt in conn.execute(
        "SELECT tradeStatusFrontText, COUNT(*) FROM erp_all_orders "
        "WHERE shopName = ? GROUP BY tradeStatusFrontText",
        (shop,)).fetchall():
        statuses.append((st, cnt))

    status_labels = [s[0] for s in statuses]
    status_counts = [s[1] for s in statuses]

    # Province heatmap
    prov_data = conn.execute(
        "SELECT receiverProvinceName, COUNT(*) FROM erp_all_orders "
        "WHERE shopName = ? AND receiverProvinceName IS NOT NULL AND receiverProvinceName != '' "
        "GROUP BY 1 ORDER BY 2 DESC",
        (shop,)).fetchall()
    prov_total = sum(x[1] for x in prov_data)
    prov_max = prov_data[0][1] if prov_data else 1
    prov_rows = []
    for prov, cnt in prov_data[:20]:
        pct = cnt / prov_total * 100 if prov_total > 0 else 0
        bar_w = int(cnt / prov_max * 100)
        intensity = cnt / prov_max
        g = int(212 * intensity)
        b = int(255 * intensity)
        prov_rows.append(
            '<tr style="border-bottom:1px solid #21262d;">'
            '<td style="padding:8px 12px;color:#c9d1d9;">' + prov + '</td>'
            '<td style="padding:8px 12px;text-align:right;color:#fff;font-weight:600;">' + format(cnt, ",") + '</td>'
            '<td style="padding:8px 12px;text-align:right;color:#8b949e;">' + '{:.1f}%'.format(pct) + '</td>'
            '<td style="padding:8px 12px;"><span style="display:inline-block;width:' + str(bar_w) + 'px;height:14px;background:rgb(0,' + str(g) + ',' + str(b) + ');border-radius:3px;"></span></td>'
            '</tr>'
        )
    prov_html_str = '\n'.join(prov_rows)

    conn.close()

    # Build chart HTML with Chart.js
    # 提取省份数据用于柱状图
    prov_labels = [p[0] for p in prov_data[:10]]
    prov_counts = [p[1] for p in prov_data[:10]]

    chart_html = _build_chart_html(
        labels=chart_labels,
        total=chart_total,
        shipped=chart_shipped,
        signed=chart_signed,
        amount=chart_amount,
        sku_labels=sku_labels,
        sku_counts=sku_counts,
        as_labels=as_labels,
        as_counts=as_counts,
        status_labels=status_labels,
        status_counts=status_counts,
        logistics_data=logistics_data,
        prov_labels=prov_labels,
        prov_counts=prov_counts,
    )

    nav_html = _build_home_nav()

    return page('数据中心', 'home', sync_card + chart_html + nav_html)

def _build_home_nav():
    return """
<style>
  .nav-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:16px; margin-bottom:32px; }
  .nav-card { background:#161b22; border:1px solid #30363d; border-top:3px solid var(--accent); border-radius:10px; padding:20px; text-decoration:none; color:#c9d1d9; display:block; transition:all .2s ease; }
  .nav-card:hover { transform:translateY(-3px); border-color:var(--accent); box-shadow:0 4px 16px rgba(0,0,0,0.3); }
  .nav-card h3 { color:var(--accent); margin:0 0 8px; font-size:15px; }
  .nav-card p { font-size:13px; color:#8b949e; margin:0; line-height:1.5; }
</style>
<h2 style="color:#00d4ff;font-size:16px;margin:0 0 16px;padding-bottom:8px;border-bottom:1px solid #30363d;">快速导航</h2>
<div class="nav-grid">
  <a href="/daily" class="nav-card" style="--accent:#00d4ff;">
    <h3>\U0001f4ca 运营日报</h3>
    <p>核心指标、订单状态、仓库需求、SKU汇总、商品明细、物流状态、财务结算、售后分析</p>
  </a>
  <a href="/warehouse" class="nav-card" style="--accent:#00e676;">
    <h3>\U0001f3ed 仓库需求</h3>
    <p>全部店铺仓库需求、AJ工厂发货排程、目的地天气风险</p>
  </a>
  <a href="/waitcheck" class="nav-card" style="--accent:#d29922;">
    <h3>\u23f3 待审核订单</h3>
    <p>按仓库和发货时效明细，24h/48h/72h分类</p>
  </a>
  <a href="/forecast" class="nav-card" style="--accent:#a855f7;">
    <h3>\U0001f4c8 AI 销量预测</h3>
    <p>未来7天逐日预测 + 6月峰值前瞻 + 全国进口季节系数</p>
  </a>
  <a href="/finance" class="nav-card" style="--accent:#3b82f6;">
    <h3>\U0001f4b0 财务结算</h3>
    <p>标准成本 vs 实际费用、每日利润表、售后成本分析</p>
  </a>
  <a href="/aftersales" class="nav-card" style="--accent:#f85149;">
    <h3>\U0001f525 售后专题</h3>
    <p>售后原因分析、理赔进度、退款金额统计</p>
  </a>
  <a href="/logistics" class="nav-card" style="--accent:#f97316;">
    <h3>\U0001f69a 物流报告</h3>
    <p>节点时效、滞留件追踪、问题件、签收进度</p>
  </a>
  <a href="/exceptionorders" class="nav-card" style="--accent:#ff6b6b;">
    <h3>⚠️ 异常件</h3>
    <p>物流问题件 + 售后异常，AI逐条分析分类：正常换单/需沟通/无法查询</p>
  </a>
  <a href="/replaceorder" class="nav-card" style="--accent:#ef4444;">
    <div class="nav-card-icon">\U0001f504</div>
    <div class="nav-card-title">换单补单</div>
    <div class="nav-card-desc">已发货退单未签收</div>
  </a>
  <a href="/upload" class="nav-card" style="--accent:#6b7280;">
    <h3>\U0001f4c1 文件上传</h3>
    <p>上传财务对账CSV、售后数据Excel、规格重量Excel</p>
  </a>
</div>
"""

def _build_chart_html(labels, total, shipped, signed, amount,
                       sku_labels, sku_counts,
                       as_labels, as_counts,
                       status_labels, status_counts,
                       logistics_data, prov_labels, prov_counts):
    import json

    # 物流数据准备
    log_labels = ['已签收', '运输中', '派送中', '今日签收', '超时预警', '问题件']
    log_values = [
        logistics_data.get('signed', 0),
        logistics_data.get('in_transit', 0),
        logistics_data.get('delivering', 0),
        logistics_data.get('today_signed', 0),
        logistics_data.get('timeout', 0),
        logistics_data.get('problem', 0)
    ]
    log_colors = ['#00e676', '#00d4ff', '#3b82f6', '#22c55e', '#f85149', '#f97316']

    # 省份数据颜色
    prov_colors = ['rgba(0, 212, 255, 0.8)', 'rgba(0, 192, 240, 0.8)', 'rgba(0, 172, 225, 0.8)',
                   'rgba(0, 152, 210, 0.8)', 'rgba(0, 132, 195, 0.8)', 'rgba(0, 112, 180, 0.8)',
                   'rgba(0, 92, 165, 0.8)', 'rgba(0, 72, 150, 0.8)', 'rgba(0, 52, 135, 0.8)',
                   'rgba(0, 32, 120, 0.8)']

    return """
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
  .chart-grid { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:24px; }
  .chart-card { background:#161b22; border:1px solid #30363d; border-top:3px solid var(--accent,#30363d); border-radius:10px; padding:16px; position:relative; transition:all .2s ease; }
  .chart-card:hover { border-color:var(--accent,#30363d); box-shadow:0 4px 16px rgba(0,0,0,0.3); transform:translateY(-2px); }
  .chart-card h3 { color:var(--accent,#00d4ff); font-size:14px; margin:0 0 12px; font-weight:600; }
  .chart-card canvas { max-height:280px; }
  .chart-full { grid-column:1 / -1; }
  .section-title { color:#00d4ff; font-size:16px; margin:0 0 16px; padding-bottom:8px; border-bottom:1px solid #30363d; }
  @media(max-width:900px){ .chart-grid{grid-template-columns:1fr; } }
</style>

<h2 class="section-title">数据看板</h2>

<div class="chart-grid">
  <div class="chart-card chart-full" style="--accent:#00d4ff;">
    <h3>近30天订单趋势</h3>
    <canvas id="chart_trend"></canvas>
  </div>
  <div class="chart-card" style="--accent:#22c55e;">
    <h3>规格销量 Top 10</h3>
    <canvas id="chart_sku"></canvas>
  </div>
  <div class="chart-card" style="--accent:#f97316;">
    <h3>已出库售后原因 Top 8</h3>
    <canvas id="chart_as"></canvas>
  </div>
  <div class="chart-card" style="--accent:#a855f7;">
    <h3>订单状态分布</h3>
    <canvas id="chart_status"></canvas>
  </div>
  <div class="chart-card" style="--accent:#00e676;">
    <h3>近30天订单金额趋势</h3>
    <canvas id="chart_amount"></canvas>
  </div>
  <div class="chart-card" style="--accent:#f85149; position:relative;">
    <h3>物流状态概览 <a href="/logistics" style="position:absolute;right:16px;top:16px;font-size:12px;color:#8b949e;text-decoration:none;">详细报告 →</a></h3>
    <canvas id="chart_logistics"></canvas>
  </div>
  <div class="chart-card" style="--accent:#3b82f6;">
    <h3>地区购买热力 TOP 10</h3>
    <canvas id="chart_province"></canvas>
  </div>
</div>

<script>
Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#21262d';
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, sans-serif';
Chart.defaults.font.size = 11;

var _trend = new Chart(document.getElementById('chart_trend'), {
  type: 'bar',
  data: {
    labels: """ + json.dumps(labels) + """,
    datasets: [
      { label:'订单金额(¥)', type:'bar', data:""" + json.dumps([round(a,0) for a in amount]) + """, backgroundColor:'rgba(0,212,255,0.25)', borderRadius:4, yAxisID:'yAmount', order:2 },
      { label:'总订单', type:'line', data:""" + json.dumps(total) + """, borderColor:'#00d4ff', backgroundColor:'rgba(0,212,255,0.1)', fill:true, tension:0.3, pointRadius:2, yAxisID:'y', order:1 },
      { label:'已发货', type:'line', data:""" + json.dumps(shipped) + """, borderColor:'#00e676', backgroundColor:'rgba(0,230,118,0.1)', fill:true, tension:0.3, pointRadius:2, yAxisID:'y', order:1 },
      { label:'已签收', type:'line', data:""" + json.dumps(signed) + """, borderColor:'#3b82f6', fill:false, tension:0.3, pointRadius:2, borderDash:[4,2], yAxisID:'y', order:1 }
    ]
  },
  options: {
    responsive:true,
    plugins:{ legend:{ position:'top' } },
    scales:{
      y:{ beginAtZero:true, position:'left', title:{display:true,text:'订单数'} },
      yAmount:{ beginAtZero:true, position:'right', grid:{drawOnChartArea:false}, title:{display:true,text:'金额(¥)'} }
    }
  }
});

var _sku = new Chart(document.getElementById('chart_sku'), {
  type: 'bar',
  data: {
    labels: """ + json.dumps(sku_labels) + """,
    datasets: [{ label:'订单数', data:""" + json.dumps(sku_counts) + """,
      backgroundColor:['#00d4ff','#00e676','#d29922','#f85149','#f97316','#3b82f6','#a855f7','#22c55e','#eab308','#6366f1'],
      borderRadius:6
    }]
  },
  options: { responsive:true, indexAxis:'y', plugins:{ legend:{display:false} }, scales:{ x:{beginAtZero:true} } }
});

var _as = new Chart(document.getElementById('chart_as'), {
  type: 'bar',
  data: {
    labels: """ + json.dumps(as_labels) + """,
    datasets: [{ label:'售后单数', data:""" + json.dumps(as_counts) + """,
      backgroundColor:'#f97316', borderRadius:6
    }]
  },
  options: { responsive:true, indexAxis:'y', plugins:{ legend:{display:false} }, scales:{ x:{beginAtZero:true} } }
});

var _st = new Chart(document.getElementById('chart_status'), {
  type: 'doughnut',
  data: {
    labels: """ + json.dumps(status_labels) + """,
    datasets: [{ data:""" + json.dumps(status_counts) + """,
      backgroundColor:['#00d4ff','#d29922','#00e676','#f85149','#a855f7','#6b7280'],
      borderWidth:0
    }]
  },
  options: { responsive:true, plugins:{ legend:{ position:'bottom', labels:{padding:12} } } }
});

var _amt = new Chart(document.getElementById('chart_amount'), {
  type: 'bar',
  data: {
    labels: """ + json.dumps(labels) + """,
    datasets: [{ label:'订单金额(¥)', data:""" + json.dumps([round(a,0) for a in amount]) + """,
      backgroundColor:'rgba(0,212,255,0.6)', borderRadius:4
    }]
  },
  options: { responsive:true, plugins:{ legend:{display:false} }, scales:{ y:{beginAtZero:true} } }
});

// 物流状态柱状图
var _log = new Chart(document.getElementById('chart_logistics'), {
  type: 'bar',
  data: {
    labels: """ + json.dumps(log_labels) + """,
    datasets: [{
      data: """ + json.dumps(log_values) + """,
      backgroundColor: """ + json.dumps(log_colors) + """,
      borderRadius: 4,
      barThickness: 35
    }]
  },
  options: {
    responsive: true,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false }, ticks: { color: '#8b949e', font: { size: 10 } } },
      y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' }, beginAtZero: true }
    },
    animation: { duration: 800, easing: 'easeOutQuart' }
  }
});

// 地区购买热力柱状图
var _prov = new Chart(document.getElementById('chart_province'), {
  type: 'bar',
  data: {
    labels: """ + json.dumps(prov_labels) + """,
    datasets: [{
      data: """ + json.dumps(prov_counts) + """,
      backgroundColor: """ + json.dumps(prov_colors) + """,
      borderRadius: 4
    }]
  },
  options: {
    responsive: true,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false }, ticks: { color: '#8b949e', font: { size: 11 }, maxRotation: 45 } },
      y: { grid: { color: '#21262d' }, ticks: { color: '#8b949e' }, beginAtZero: true }
    },
    animation: { duration: 800, easing: 'easeOutQuart' }
  }
});
</script>
"""



# ===== 店铺二级页面 =====
SHOP_LIST = ['榴愿时刻工厂店', '觅糖生鲜国际', '冕宁国源优品', '青邮生鲜三店', '榴愿食刻', 'pdd54247437212', 'pdd92663579562']
SHOP_COLORS = ['#58a6ff', '#3fb950', '#d29922', '#f0883e', '#bc8cff', '#f778ba', '#79c0ff']

def _get_shop_overview(conn):
    """获取所有店铺的核心数据 — 发货状态以 consignTime 为准"""
    cur = conn.execute("""
        SELECT
          ao.shopName,
          ao.cnt as total_orders,
          COALESCE(ao.paid,0) as total_paid,
          COALESCE(ao.unshipped,0) as unshipped,
          COALESCE(ao.shipped,0) as shipped,
          COALESCE(as_ref.as_cnt,0) as after_sales,
          COALESCE(as_ref.refund,0) as refund_amount
        FROM (SELECT shopName, COUNT(*) cnt, COALESCE(SUM(paid),0) paid,
                     COUNT(CASE WHEN consignTime IS NULL OR consignTime='' THEN 1 END) unshipped,
                     COUNT(CASE WHEN consignTime IS NOT NULL AND consignTime!='' THEN 1 END) shipped
              FROM erp_all_orders GROUP BY 1) ao
        LEFT JOIN (SELECT shop_name, COUNT(*) as_cnt, SUM(refund_amount) refund FROM after_sales GROUP BY 1) as_ref ON ao.shopName=as_ref.shop_name
        ORDER BY ao.cnt DESC
    """)
    return cur.fetchall()


def render_daily_landing(conn):
    """店铺总览页 — 长方形卡片网格"""
    shops = _get_shop_overview(conn)
    from urllib.parse import quote

    def fmt(n):
        return f'{int(n):,}' if n else '0'
    def fmt_amt(n):
        return f'¥{int(n):,}' if n else '¥0'

    cards_html = ''
    for idx, row in enumerate(shops):
        name, total, paid, unshipped, shipped, as_cnt, refund = row
        color = SHOP_COLORS[idx % len(SHOP_COLORS)]
        url = f'/daily?shop={quote(name, safe="")}'

        cards_html += f'''
<a href="{url}" style="text-decoration:none;color:inherit;display:block;">
<div style="background:#161b22;border:1px solid #30363d;border-left:4px solid {color};border-radius:10px;padding:20px 24px;transition:background .15s,box-shadow .15s;cursor:pointer;" onmouseover="this.style.background='#1c2333';this.style.boxShadow='0 0 12px rgba(0,0,0,.3)'" onmouseout="this.style.background='#161b22';this.style.boxShadow='none'">
    <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:14px;">
        <h3 style="margin:0;font-size:18px;font-weight:700;color:#fff;">{name}</h3>
        <span style="font-size:12px;color:#484f58;">点击进入详情 &rarr;</span>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px 16px;">
        <div><div style="font-size:11px;color:#484f58;">总订单</div><div style="font-size:20px;font-weight:700;color:{color};">{fmt(total)}</div></div>
        <div><div style="font-size:11px;color:#484f58;">总金额</div><div style="font-size:20px;font-weight:700;color:#8b949e;">{fmt_amt(paid)}</div></div>
        <div><div style="font-size:11px;color:#484f58;">未发货</div><div style="font-size:20px;font-weight:700;color:#ffa657;">{fmt(unshipped)}</div></div>
        <div><div style="font-size:11px;color:#484f58;">已发货</div><div style="font-size:20px;font-weight:700;color:#3fb950;">{fmt(shipped)}</div></div>
        <div><div style="font-size:11px;color:#484f58;">售后数</div><div style="font-size:20px;font-weight:700;color:#f85149;">{fmt(as_cnt)}</div></div>
        <div><div style="font-size:11px;color:#484f58;">退款金额</div><div style="font-size:20px;font-weight:700;color:#ff7b72;">{fmt_amt(refund)}</div></div>
    </div>
</div>
</a>'''

    return f'''
<div style="margin-bottom:20px;">
    <h2 style="color:#c9d1d9;font-size:16px;font-weight:700;margin:0 0 4px;">全部店铺运营总览</h2>
    <p style="color:#484f58;font-size:13px;margin:0;">点击店铺卡片查看详细运营日报</p>
</div>
<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(500px,1fr));gap:16px;">
    {cards_html}
</div>
'''


def _build_shop_full_report(conn, shop_name):
    """为任意店铺生成类似邮件格式的详细运营日报 — 以 consignTime 为准"""
    cur = conn.cursor()

    # 发货状态聚合（以 consignTime 为准）
    shipped = cur.execute("SELECT COUNT(*), COALESCE(SUM(paid),0) FROM erp_all_orders WHERE shopName=? AND consignTime IS NOT NULL AND consignTime!=''", (shop_name,)).fetchone()
    unshipped = cur.execute("SELECT COUNT(*), COALESCE(SUM(paid),0) FROM erp_all_orders WHERE shopName=? AND (consignTime IS NULL OR consignTime='')", (shop_name,)).fetchone()
    total_cnt = shipped[0] + unshipped[0]

    # 仓库分布
    wh_rows = cur.execute("SELECT warehouseName, COUNT(*), COALESCE(SUM(paid),0) FROM erp_all_orders WHERE shopName=? AND warehouseName!='' GROUP BY warehouseName ORDER BY COUNT(*) DESC", (shop_name,)).fetchall()

    # 物流分布
    log_rows = cur.execute("SELECT logisticsName, COUNT(*) FROM erp_all_orders WHERE shopName=? AND logisticsName!='' GROUP BY logisticsName ORDER BY COUNT(*) DESC LIMIT 10", (shop_name,)).fetchall()

    as_rows = cur.execute("SELECT * FROM after_sales WHERE shop_name=?", (shop_name,)).fetchall()

    from datetime import datetime
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def fmt(n): return f'{int(n or 0):,}'
    def fmt_amt(n): return f'¥{int(n or 0):,}'

    # 核心指标
    metrics = [
        (f'总订单 ({fmt(total_cnt)})', total_cnt, '#58a6ff'),
        (f'已发货 ({fmt(shipped[0])})', shipped[0], '#3fb950'),
        (f'未发货 ({fmt(unshipped[0])})', unshipped[0], '#ffa657'),
        (f'已发货金额', shipped[1], '#3fb950', True),
        (f'未发货金额', unshipped[1], '#ffa657', True),
    ]
    metrics_html = ''
    for item in metrics:
        label, val, color = item[0], item[1], item[2]
        is_money = len(item) > 3 and item[3]
        display = fmt_amt(val) if is_money else fmt(val)
        metrics_html += f'<div class="metric-card"><div class="metric-value" style="color:{color};">{display}</div><div class="metric-label">{label}</div></div>'

    # 仓库表格
    wh_html = ''
    if wh_rows:
        wh_html = '<h3 class="sub-title">仓库分布</h3><table class="data-table"><thead><tr><th>仓库</th><th>订单数</th><th>金额</th></tr></thead><tbody>'
        for wname, wcnt, wpaid in wh_rows:
            wh_html += f'<tr><td>{wname}</td><td>{wcnt:,}</td><td>¥{wpaid:,.0f}</td></tr>'
        wh_html += '</tbody></table>'

    # 物流表格
    log_html = ''
    if log_rows:
        log_html = '<h3 class="sub-title">物流分布</h3><table class="data-table"><thead><tr><th>物流公司</th><th>订单数</th></tr></thead><tbody>'
        for lname, lcnt in log_rows:
            log_html += f'<tr><td>{lname}</td><td>{lcnt:,}</td></tr>'
        log_html += '</tbody></table>'

    # 售后
    as_html = ''
    if as_rows:
        as_total_cnt = len(as_rows)
        as_total_amt = sum(r[8] or 0 for r in as_rows if len(r) > 8)  # refund_amount
        # outbound_status 位置：查表结构为第5列
        as_unshipped_cnt = sum(1 for r in as_rows if len(r) > 4 and r[4] == '未出库')
        as_shipped_cnt = sum(1 for r in as_rows if len(r) > 4 and r[4] == '已出库')
        # primary_reason 位置：第8列
        reasons = {}
        for r in as_rows:
            reason = r[7] if len(r) > 7 and r[7] else ''
            if reason: reasons[reason] = reasons.get(reason, 0) + 1
        reason_sorted = sorted(reasons.items(), key=lambda x: x[1], reverse=True)[:10]

        as_html = f'''
<div class="section">
<h2 class="section-title">售后情况</h2>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px;">
    <div class="metric-card"><div class="metric-value" style="color:#f85149;">{as_total_cnt:,}</div><div class="metric-label">售后总数</div></div>
    <div class="metric-card"><div class="metric-value" style="color:#ff7b72;">¥{as_total_amt:,.0f}</div><div class="metric-label">退款金额</div></div>
    <div class="metric-card"><div class="metric-value" style="color:#ffa657;">{as_unshipped_cnt:,}</div><div class="metric-label">未出库</div></div>
    <div class="metric-card"><div class="metric-value" style="color:#58a6ff;">{as_shipped_cnt:,}</div><div class="metric-label">已出库</div></div>
</div>
<h3 class="sub-title">售后原因 TOP 10</h3>
<table class="data-table compact"><thead><tr><th>原因</th><th>数量</th><th>占比</th></tr></thead><tbody>
'''
        for reason, rcnt in reason_sorted:
            as_html += f'<tr><td>{reason}</td><td>{rcnt:,}</td><td>{rcnt/as_total_cnt*100:.1f}%</td></tr>'
        as_html += '</tbody></table></div>'

    html = f'''
<style>
body {{ margin:0; padding:0; background:#0d1117; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; color:#c9d1d9; }}
.report-wrapper {{ max-width:95%; margin:0 auto; padding:24px 20px; }}
.report-header {{ border-bottom:2px solid #00d4ff; padding-bottom:16px; margin-bottom:24px; }}
.report-header h1 {{ margin:0 0 6px; font-size:26px; font-weight:700; color:#ffffff; letter-spacing:1px; }}
.timestamp {{ margin:0; font-size:13px; color:#8b949e; }}
.section {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:20px; margin-bottom:20px; }}
.section-title {{ margin:0 0 16px; font-size:18px; font-weight:700; color:#00d4ff; border-bottom:1px solid #30363d; padding-bottom:8px; }}
.sub-title {{ margin:18px 0 8px; font-size:15px; font-weight:600; color:#ffffff; }}
.metric-card {{ background:#1c2333; border:1px solid #30363d; border-radius:8px; padding:14px 12px; text-align:center; }}
.metric-value {{ font-size:22px; font-weight:800; margin-bottom:4px; }}
.metric-label {{ font-size:12px; color:#8b949e; }}
.data-table {{ width:100%; border-collapse:collapse; font-size:13px; margin-bottom:12px; }}
.data-table thead th {{ background:#1c2333; color:#00d4ff; font-weight:600; padding:8px 10px; border:1px solid #30363d; text-align:left; font-size:12px; }}
.data-table tbody td {{ padding:6px 10px; border:1px solid #30363d; color:#c9d1d9; }}
.data-table.compact {{ font-size:12px; }}
.back-link {{ display:inline-block;color:#58a6ff;font-size:13px;text-decoration:none;margin-bottom:16px;padding:4px 8px;border-radius:4px;background:rgba(88,166,255,0.1); }}
.back-link:hover {{ background:rgba(88,166,255,0.2); }}
</style>

<div class="report-wrapper">
<div class="report-header">
    <a href="/daily" class="back-link">&larr; 返回店铺总览</a>
    <h1>{shop_name} 运营日报</h1>
    <p class="timestamp">{now}</p>
</div>
<div class="section">
    <h2 class="section-title">发货状态（consignTime）</h2>
    <div class="metric-grid">{metrics_html}</div>
</div>
<div class="section">
    <h2 class="section-title">订单明细</h2>
    {wh_html}{log_html}
</div>
{as_html}
</div>
'''
    return html


def render_daily(shop_name=None):
    import sqlite3
    DB_PATH = os.path.join(BASE, "erp_all.db")

    if shop_name is None:
        # 店铺总览页（有缓存）
        return render_daily_landing_cached()
    elif shop_name == SHOP_LIST[0]:
        # 榴愿时刻工厂店：使用预生成的邮件缓存 HTML
        html = get_cached_html('erp_full_report_email.py')
        back_link = '<div style="max-width:95%;margin:0 auto;padding:12px 20px 0;"><a href="/daily" class="back-link" style="display:inline-block;color:#58a6ff;font-size:13px;text-decoration:none;padding:4px 8px;border-radius:4px;background:rgba(88,166,255,0.1);margin-bottom:8px;">&larr; 返回店铺总览</a></div>\n'
        html = back_link + html
        return page('运营日报 - 榴愿时刻工厂店', 'daily', html)
    else:
        # 其他店铺：动态生成
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            html = _build_shop_full_report(conn, shop_name)
            conn.close()
        except Exception as e:
            html = f'<p style="color:#f85149;">店铺数据加载失败: {e}</p>'
        return page(f'运营日报 - {shop_name}', 'daily', html)


@cached_page('daily_landing')
def render_daily_landing_cached():
    import sqlite3
    DB_PATH = os.path.join(BASE, "erp_all.db")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        html = render_daily_landing(conn)
        conn.close()
    except Exception as e:
        html = f'<p style="color:#f85149;">数据加载失败: {e}</p>'
    return page('店铺总览', 'daily', html)


@cached_page('warehouse')
def render_warehouse():
    html = get_cached_html('erp_warehouse_demand_email.py')
    return page('仓库需求', 'warehouse', html)

@cached_page('waitcheck')
def render_waitcheck():
    import sqlite3
    DB_PATH = os.path.join(BASE, "erp_all.db")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        html = _build_waitcheck_html(conn)
        conn.close()
    except Exception as e:
        html = '<p style="color:#f85149;">数据加载失败: ' + str(e) + '</p>'
    return page('待审核订单', 'waitcheck', html)

def _build_waitcheck_html(conn):
    """Wait check dark theme UI"""
    from datetime import datetime
    from collections import defaultdict
    import json as _json
    now = datetime.now()

    # Deadline classification
    deadline_sql = """
        CASE
            WHEN julianday(estimateConsignTime) - julianday('now') <= 1 THEN '24小时内'
            WHEN julianday(estimateConsignTime) - julianday('now') <= 2 THEN '24-48小时'
            WHEN julianday(estimateConsignTime) - julianday('now') <= 3 THEN '48-72小时'
            WHEN julianday(estimateConsignTime) - julianday('now') <= 4 THEN '72-96小时'
            WHEN julianday(estimateConsignTime) - julianday('now') <= 5 THEN '96-120小时'
            ELSE '>120小时'
        END
    """

    # Summary by deadline + warehouse
    cur = conn.execute(f"""
        SELECT {deadline_sql} as deadline, warehouseName,
            COUNT(*) as order_count,
            ROUND(SUM(CAST(COALESCE(realAmount, '0') AS REAL))) as total_amount
        FROM erp_wait_check
        GROUP BY deadline, warehouseName
        ORDER BY
            CASE {deadline_sql}
                WHEN '24小时内' THEN 1 WHEN '24-48小时' THEN 2 WHEN '48-72小时' THEN 3
                WHEN '72-96小时' THEN 4 WHEN '96-120小时' THEN 5 ELSE 6 END,
            order_count DESC
    """)

    deadline_data = {}
    warehouse_data = {}
    for deadline, warehouse, count, amount in cur.fetchall():
        if deadline not in deadline_data:
            deadline_data[deadline] = {"count": 0, "amount": 0}
        deadline_data[deadline]["count"] += count
        deadline_data[deadline]["amount"] += amount
        key = (deadline, warehouse)
        warehouse_data[key] = {"count": count, "amount": amount or 0}

    # SKU breakdown
    sku_data = {}
    cur = conn.execute(f"""
        SELECT {deadline_sql} as deadline, warehouseName, orderItemList
        FROM erp_wait_check
    """)
    for deadline, warehouse, item_list in cur.fetchall():
        key = (deadline, warehouse)
        if key not in sku_data:
            sku_data[key] = defaultdict(int)
        try:
            items = _json.loads(item_list) if item_list else []
        except Exception:
            items = []
        for item in items:
            name = item.get("skuName", "") or item.get("spuName", "") or "未知商品"
            sku_data[key][name] += item.get("quantity", 1)

    deadline_order = ["24小时内", "24-48小时", "48-72小时", "72-96小时", "96-120小时", ">120小时"]
    deadline_colors = {
        "24小时内": "#f85149", "24-48小时": "#f97316", "48-72小时": "#d29922",
        "72-96小时": "#3b82f6", "96-120小时": "#a855f7", ">120小时": "#6b7280",
    }

    parts = []
    parts.append('<h1 style="color:#fff;font-size:22px;margin-bottom:6px;">待审核订单</h1>')
    parts.append('<p style="color:#8b949e;margin-bottom:16px;font-size:13px;">按仓库 & 发货时效分类 | 生成于 ' + now.strftime("%m/%d %H:%M") + '</p>')

    # Summary cards
    total_orders = sum(d["count"] for d in deadline_data.values())
    total_amount = sum(d["amount"] for d in deadline_data.values())
    parts.append('<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:24px;">')
    parts.append('<div style="background:#161b22;border:1px solid #00d4ff;border-radius:10px;padding:16px;">')
    parts.append('  <div style="color:#8b949e;font-size:12px;margin-bottom:4px;">总待审核订单</div>')
    parts.append('  <div style="color:#fff;font-size:32px;font-weight:700;">' + "{:,}".format(total_orders) + '</div>')
    parts.append('  <div style="color:#8b949e;font-size:12px;">单</div>')
    parts.append('</div>')
    parts.append('<div style="background:#161b22;border:1px solid #00e676;border-radius:10px;padding:16px;">')
    parts.append('  <div style="color:#8b949e;font-size:12px;margin-bottom:4px;">总实收金额</div>')
    parts.append('  <div style="color:#00e676;font-size:28px;font-weight:700;">¥ ' + "{:,.0f}".format(total_amount) + '</div>')
    parts.append('  <div style="color:#8b949e;font-size:12px;">元</div>')
    parts.append('</div>')
    parts.append('</div>')

    # Deadline sections
    for dl in deadline_order:
        if dl not in deadline_data:
            continue
        info = deadline_data[dl]
        color = deadline_colors.get(dl, "#8b949e")
        parts.append('<h2 style="color:' + color + ';font-size:16px;margin-bottom:12px;">' + dl + ' — 合计 ' + "{:,}".format(info["count"]) + ' 单 | 实收 ¥ ' + "{:,.0f}".format(info["amount"]) + '</h2>')

        # Warehouses within this deadline
        warehouses = {}
        for (dd_dl, wh), cnt_amt in warehouse_data.items():
            if dd_dl == dl:
                warehouses[wh] = cnt_amt

        for wh, wh_info in warehouses.items():
            parts.append('<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;margin-bottom:12px;">')
            parts.append('  <div style="color:#8b949e;font-size:13px;margin-bottom:8px;">')
            parts.append('    <span style="color:#fff;font-weight:600;">' + wh + '</span>')
            parts.append('    <span style="margin-left:12px;">' + "{:,}".format(wh_info["count"]) + ' 单</span>')
            parts.append('    <span style="margin-left:8px;color:#00e676;">¥ ' + "{:,.0f}".format(wh_info["amount"]) + '</span>')
            parts.append('  </div>')

            # SKU table
            sk_map = sku_data.get((dl, wh), {})
            if sk_map:
                total_qty = sum(sk_map.values())
                parts.append('  <table style="width:100%;border-collapse:collapse;font-size:12px;">')
                parts.append('    <tr style="border-bottom:1px solid #30363d;">')
                parts.append('      <th style="padding:6px;text-align:left;color:#8b949e;">品名规格</th>')
                parts.append('      <th style="padding:6px;text-align:right;color:#8b949e;width:70px;">数量</th>')
                parts.append('    </tr>')
                for name, qty in sorted(sk_map.items(), key=lambda x: -x[1]):
                    display = name[:80] + "..." if len(name) > 80 else name
                    parts.append('    <tr style="border-bottom:1px solid #21262d;">')
                    parts.append('      <td style="padding:4px 6px;color:#c9d1d9;">' + display + '</td>')
                    parts.append('      <td style="padding:4px 6px;text-align:right;color:#fff;">' + "{:,}".format(qty) + '</td>')
                    parts.append('    </tr>')
                parts.append('    <tr style="background:#1c2333;font-weight:bold;">')
                parts.append('      <td style="padding:4px 6px;text-align:right;color:#8b949e;">合计</td>')
                parts.append('      <td style="padding:4px 6px;text-align:right;color:#00e676;">' + "{:,}".format(total_qty) + '</td>')
                parts.append('    </tr>')
                parts.append('  </table>')
            else:
                parts.append('  <div style="color:#484f58;font-size:12px;">无商品明细</div>')

            parts.append('</div>')

    return "\n".join(parts)


def fmt_time(ts):
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')

def fmt_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024: return f'{size:.0f}{unit}'
        size /= 1024
    return f'{size:.0f}TB'

def upload_files_list():
    files = []
    for dtype, dpath in UPLOAD_DIRS.items():
        if os.path.isdir(dpath):
            for fn in sorted(os.listdir(dpath), reverse=True):
                if fn.startswith('._'): continue
                fp = os.path.join(dpath, fn)
                if os.path.isfile(fp):
                    st = os.stat(fp)
                    files.append({'name': fn, 'type': TYPE_LABELS.get(dtype, dtype), 'time': fmt_time(st.st_mtime), 'size': fmt_size(st.st_size)})
    return files[:50]

@cached_page('forecast')
def render_forecast():
    """AI销量预测页面"""
    import sqlite3
    from datetime import datetime, timedelta
    DB_PATH = os.path.join(BASE, "erp_all.db")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        html = _build_forecast_html(conn)
        conn.close()
    except Exception as e:
        html = '<p style="color:#f85149;">预测数据加载失败: ' + str(e) + '</p>'
    return page('销量预测', 'forecast', html)

MONTHLY_COEFF = {
    1: 0.35, 2: 0.55, 3: 0.85, 4: 1.65, 5: 2.80,
    6: 3.50, 7: 2.20, 8: 1.20, 9: 0.90, 10: 0.70,
    11: 0.40, 12: 0.30,
}

def _build_forecast_html(conn):
    """AI销量预测页面 — 含天气影响"""
    import sqlite3
    from datetime import datetime, timedelta
    import json as _json

    now = datetime.now()
    current_month = now.month
    days_cn = ["周日","周一","周二","周三","周四","周五","周六"]

    target_skus = [
        ("260418269071002", "金枕8-10斤"),
        ("260407269071202", "5-6斤/1个"),
        ("260426269071002", "巨无霸12-15斤"),
    ]

    # ===== Load Chanthaburi weather =====
    weather_map = {}
    try:
        cur = conn.execute("SELECT daily_data FROM city_weather WHERE city = ? LIMIT 1", ("尖竹汶",))
        r = cur.fetchone()
        if r and r[0]:
            for wd in _json.loads(r[0]):
                weather_map[wd["date"]] = {"precip": wd.get("precip", 0) or 0, "desc": wd.get("desc", "")}
    except Exception:
        pass

    def get_forecast(sku_no, dow):
        """Get forecast qty and revenue for a SKU on a given day-of-week."""
        cur = conn.execute("""
            SELECT daily_qty, daily_revenue FROM daily_sales_features
            WHERE sku_no = ? AND day_of_week = ? AND sale_date >= date('now', '-7 days')
        """, (sku_no, dow))
        r = cur.fetchone()
        recent_avg = r[0] if (r and r[0]) else 0
        recent_rev = r[1] if (r and r[1]) else 0
        cur = conn.execute("""
            SELECT daily_qty, daily_revenue FROM daily_sales_features
            WHERE sku_no = ? AND day_of_week = ? AND daily_qty > 0
        """, (sku_no, dow))
        vals = sorted([r2[0] for r2 in cur])
        revs = [r2[1] for r2 in cur.fetchall() if r2[1] and r2[1] > 0]
        if vals:
            n = len(vals)
            median = vals[n // 2] if n % 2 else (vals[n//2 - 1] + vals[n//2]) / 2
            med_rev = sum(revs) / len(revs) if revs else 0
        else:
            median = 0
            med_rev = 0
        return (recent_avg if recent_avg > 0 else median,
                recent_rev if recent_rev > 0 else med_rev)

    parts = []
    parts.append('<h1 style="color:#fff;font-size:22px;margin-bottom:6px;">AI 销量预测</h1>')
    parts.append('<p style="color:#8b949e;margin-bottom:16px;font-size:13px;">基于历史中位数 + 近期均值 + 全国进口季节系数 + 尖竹汶天气影响 | 生成于 ' + now.strftime("%m/%d %H:%M") + '</p>')

    # ===== 14天总量卡片 =====
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">未来 14 天总量预测</h2>')
    parts.append('<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;margin-bottom:24px;">')
    all_forecasts = []
    for sku_no, sku_label in target_skus:
        total_qty = 0
        total_rev = 0
        for i in range(1, 15):
            d = now + timedelta(days=i)
            dow = (d.weekday() + 1) % 7
            qty, rev = get_forecast(sku_no, dow)
            # Weather impact for Thai products (only this SKU ships from Thailand)
            weather_d = d - timedelta(days=2)
            wd_info = weather_map.get(weather_d.strftime("%Y-%m-%d"), {})
            precip = wd_info.get("precip", 0)
            if precip > 0 and precip <= 5:
                qty *= 0.85; rev *= 0.85
            elif precip > 5:
                qty *= 0.70; rev *= 0.70
            total_qty += qty
            total_rev += rev
        all_forecasts.append((sku_no, sku_label, total_qty, total_rev))
        colors = ["#238636", "#d29922", "#f85149"]
        ci = target_skus.index((sku_no, sku_label))
        parts.append('<div style="background:#161b22;border:1px solid ' + colors[ci] + ';border-radius:10px;padding:16px;">')
        parts.append('  <div style="color:#8b949e;font-size:12px;margin-bottom:4px;">' + sku_label + '</div>')
        parts.append('  <div style="display:flex;align-items:baseline;gap:8px;">')
        parts.append('    <span style="color:#fff;font-size:32px;font-weight:700;">' + '{:,.0f}'.format(total_qty) + '</span>')
        parts.append('    <span style="color:#8b949e;font-size:12px;">件</span>')
        parts.append('  </div>')
        parts.append('  <div style="color:#00e676;font-size:16px;font-weight:600;">¥ ' + '{:,.0f}'.format(total_rev) + '</div>')
        parts.append('  <div style="color:#8b949e;font-size:12px;">14天总计</div>')
        parts.append('</div>')
    parts.append('</div>')

    # ===== 按平台拆分 =====
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">按平台拆分（未来14天）</h2>')
    platforms = ["京东", "拼多多", "放心购(抖音小店)", "淘宝"]
    platform_colors = {"京东": "#00d4ff", "拼多多": "#f97316", "放心购(抖音小店)": "#a855f7", "淘宝": "#ef4444"}
    parts.append('<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:24px;">')
    for pf in platforms:
        pf_qty = 0
        pf_rev = 0
        for sku_no, sku_label in target_skus:
            for i in range(1, 15):
                d = now + timedelta(days=i)
                dow = (d.weekday() + 1) % 7
                cur = conn.execute("""
                    SELECT daily_qty, daily_revenue FROM daily_sales_features
                    WHERE sku_no = ? AND day_of_week = ? AND platform_name = ? AND sale_date >= date('now', '-7 days')
                """, (sku_no, dow, pf))
                r = cur.fetchone()
                recent_avg = r[0] if (r and r[0]) else 0
                recent_rev = r[1] if (r and r[1]) else 0
                cur = conn.execute("""
                    SELECT daily_qty, daily_revenue FROM daily_sales_features
                    WHERE sku_no = ? AND day_of_week = ? AND platform_name = ? AND daily_qty > 0
                """, (sku_no, dow, pf))
                vals = sorted([r2[0] for r2 in cur])
                revs = [r2[1] for r2 in cur.fetchall() if r2[1] and r2[1] > 0]
                if vals:
                    n = len(vals)
                    median = vals[n // 2] if n % 2 else (vals[n//2 - 1] + vals[n//2]) / 2
                    med_rev = sum(revs) / len(revs) if revs else 0
                else:
                    median = 0
                    med_rev = 0
                if recent_avg > 0:
                    pf_qty += recent_avg; pf_rev += recent_rev
                else:
                    pf_qty += median; pf_rev += med_rev
        if pf_qty > 0:
            pc = platform_colors.get(pf, "#8b949e")
            parts.append('<div style="background:#161b22;border:1px solid ' + pc + ';border-radius:10px;padding:16px;">')
            parts.append('  <div style="color:#8b949e;font-size:12px;margin-bottom:4px;">' + pf + '</div>')
            parts.append('  <div style="display:flex;align-items:baseline;gap:8px;">')
            parts.append('    <span style="color:#fff;font-size:28px;font-weight:700;">' + '{:,.0f}'.format(pf_qty) + '</span>')
            parts.append('    <span style="color:#8b949e;font-size:12px;">件</span>')
            parts.append('  </div>')
            parts.append('  <div style="color:#00e676;font-size:14px;font-weight:600;">¥ ' + '{:,.0f}'.format(pf_rev) + '</div>')
            parts.append('  <div style="color:#8b949e;font-size:12px;">14天总计</div>')
            parts.append('</div>')
    parts.append('</div>')

    # ===== 每日预测明细（含天气） =====
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">每日预测明细（含尖竹汶天气影响）</h2>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:13px;">')
    parts.append('<thead><tr style="border-bottom:1px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">日期</th>')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">星期</th>')
    for sku_no, sku_label in target_skus:
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">' + sku_label + '</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">(¥)</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">合计</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">(¥)</th>')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">天气</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">mm</th>')
    parts.append('<th style="padding:8px;text-align:center;color:#8b949e;">影响</th>')
    parts.append('</tr></thead><tbody>')

    for i in range(1, 15):
        d = now + timedelta(days=i)
        dow = (d.weekday() + 1) % 7
        date_str = d.strftime("%m/%d")
        # Weather 2 days prior
        weather_d = d - timedelta(days=2)
        wd_key = weather_d.strftime("%Y-%m-%d")
        wd_info = weather_map.get(wd_key, {})
        precip = wd_info.get("precip", 0)
        wd_desc = wd_info.get("desc", "-")
        if precip <= 0:
            impact_pct = 0; impact_color = "#238636"; impact_label = "正常"
        elif precip <= 5:
            impact_pct = 15; impact_color = "#d29922"; impact_label = "-15%"
        else:
            impact_pct = 30; impact_color = "#f85149"; impact_label = "-30%"

        day_qty_total = 0
        day_rev_total = 0
        row_parts = []
        for sku_no, sku_label in target_skus:
            base_qty, base_rev = get_forecast(sku_no, dow)
            # Apply weather impact
            if impact_pct > 0:
                adj_qty = base_qty * (100 - impact_pct) / 100
                adj_rev = base_rev * (100 - impact_pct) / 100
            else:
                adj_qty = base_qty
                adj_rev = base_rev
            day_qty_total += adj_qty
            day_rev_total += adj_rev
            row_parts.append('<td style="padding:8px;text-align:right;color:#fff;">' + '{:,.0f}'.format(adj_qty) + '</td>')
            row_parts.append('<td style="padding:8px;text-align:right;color:#8b949e;font-size:11px;">' + '{:,.0f}'.format(adj_rev) + '</td>')

        precip_display = str(precip) if precip else "0"
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:8px;color:#00d4ff;font-weight:600;">' + date_str + '</td>')
        parts.append('<td style="padding:8px;color:#8b949e;">' + days_cn[dow] + '</td>')
        parts.extend(row_parts)
        parts.append('<td style="padding:8px;text-align:right;color:#00e676;font-weight:700;">' + '{:,.0f}'.format(day_qty_total) + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#8b949e;font-weight:600;">' + '{:,.0f}'.format(day_rev_total) + '</td>')
        parts.append('<td style="padding:8px;color:#fff;font-size:12px;">' + wd_desc + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#fff;font-size:12px;">' + precip_display + '</td>')
        parts.append('<td style="padding:8px;text-align:center;color:' + impact_color + ';font-weight:600;">' + impact_label + '</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')

    # ===== 6月前瞻 =====
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">6月前瞻（全年峰值）</h2>')
    june_coeff = MONTHLY_COEFF.get(6, 1.0)
    may_coeff = MONTHLY_COEFF.get(5, 1.0)
    growth = (june_coeff / may_coeff - 1) * 100
    parts.append('<div style="background:rgba(210,153,34,0.1);border:1px solid #d29922;border-radius:10px;padding:16px;margin-bottom:16px;">')
    parts.append('<div style="color:#d29922;font-size:13px;margin-bottom:8px;">')
    parts.append('全国榴莲进口季节系数: 5月 ' + '{:.2f}x'.format(may_coeff) + ' → 6月 ' + '{:.2f}x'.format(june_coeff) + ' (+' + '{:.0f}%'.format(growth) + ')')
    parts.append('</div>')
    parts.append('</div>')
    parts.append('<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:24px;">')
    parts.append('<thead><tr style="border-bottom:1px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">SKU</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">当前日均(近7天)</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">6月日均预测</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">6月全月预测</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">6月营收预测</th>')
    parts.append('</tr></thead><tbody>')
    for sku_no, sku_label in target_skus:
        cur = conn.execute("""
            SELECT AVG(daily_qty), AVG(daily_revenue) FROM daily_sales_features
            WHERE sku_no = ? AND sale_date >= date('now', '-7 days')
        """, (sku_no,))
        r = cur.fetchone()
        recent_qty = r[0] if (r and r[0]) else 0
        recent_rev = r[1] if (r and r[1]) else 0
        if recent_qty > 0:
            june_daily = recent_qty * (june_coeff / may_coeff)
            june_monthly = june_daily * 30
            june_rev = recent_rev * (june_coeff / may_coeff)
            june_rev_monthly = june_rev * 30
        else:
            june_daily = 0
            june_monthly = 0
            june_rev_monthly = 0
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:8px;color:#fff;">' + sku_label + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#8b949e;">' + '{:,.0f}'.format(recent_qty) + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#fff;">' + '{:,.0f}'.format(june_daily) + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#d29922;font-weight:600;">' + '{:,.0f}'.format(june_monthly) + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#00e676;font-weight:600;">¥ ' + '{:,.0f}'.format(june_rev_monthly) + '</td>')
        parts.append('</tr>')
    parts.append('</tbody></table>')

    # ===== 历史趋势 =====
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">近30天销量趋势</h2>')
    parts.append('<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;overflow-x:auto;">')
    for sku_no, sku_label in target_skus:
        parts.append('<div style="margin-bottom:12px;"><span style="color:#8b949e;font-size:12px;">' + sku_label + '</span>')
        cur = conn.execute("""
            SELECT sale_date, SUM(daily_qty) as total
            FROM daily_sales_features
            WHERE sale_date >= date('now', '-30 days')
            GROUP BY sale_date ORDER BY sale_date
        """)
        rows = cur.fetchall()
        max_val = max([r[1] for r in rows], default=1)
        for r in rows:
            pct = r[1] / max_val * 100
            bar = '<span style="display:inline-block;width:' + str(int(pct)) + 'px;height:10px;background:#00d4ff;border-radius:2px;vertical-align:middle;"></span>'
            parts.append('<div style="font-size:11px;color:#8b949e;margin:1px 0;">')
            parts.append(r[0][5:] + ' ' + bar + ' ' + '{:,}'.format(r[1]))
            parts.append('</div>')
        parts.append('</div>')
    parts.append('</div>')

    return '\n'.join(parts)



@cached_page('finance')
def render_finance():
    import sqlite3
    DB_PATH = os.path.join(BASE, "erp_all.db")
    key = 'finance'
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
        return page('财务结算', 'finance', _cache[key][1])
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        html = _build_finance_html(conn)
        conn.close()
    except Exception as e:
        html = '<p style="color:#f85149;">数据加载失败: ' + str(e) + '</p>'
    _cache[key] = (now, html)
    return page('财务结算', 'finance', html)

def _build_finance_html(conn):
    from datetime import datetime
    from collections import defaultdict as _dd
    now = datetime.now()
    shop = '榴愿时刻工厂店'

    STANDARD_COSTS = {
        "2-3斤/1个": 98.00,
        "3-4斤/1个": 111.03,
        "4-5斤/1个": 126.83,
        "5-6斤/1个": 148.43,
        "6-7斤/1个": 170.16,
        "7-8斤/1个": 196.77,
        "8-9斤/1个": 212.57,
        "9斤以上/1个": 228.38,
    }

    def lookup_cost(sku_name):
        if sku_name in STANDARD_COSTS:
            return STANDARD_COSTS[sku_name]
        for key, val in STANDARD_COSTS.items():
            if key in str(sku_name):
                return val
        return 0

    def fmt(n):
        try:
            return '{:,.0f}'.format(n)
        except (ValueError, TypeError):
            return '0'
    def fmt_amt(n):
        try:
            return '{:,.0f}'.format(n)
        except (ValueError, TypeError):
            return '0'
    def pct(a, b):
        try:
            return '{:.2f}%'.format(a / b * 100) if b > 0 else '-'
        except:
            return '-'

    # ===== Fast SQL Aggregations =====
    # 1. Shipped orders (shop filter)
    shipped_cnt = conn.execute(
        'SELECT COUNT(*) FROM erp_all_orders WHERE shopName = ? AND consignTime IS NOT NULL AND consignTime != ""',
        (shop,)).fetchone()[0]

    # 2. Finance matched summary
    fin_total_income, fin_total_expense, fin_total_net = conn.execute(
        'SELECT COALESCE(SUM(总收入),0), COALESCE(SUM(总支出),0), COALESCE(SUM(净额),0) FROM finance_matched').fetchone()
    fin_cnt = conn.execute('SELECT COUNT(*) FROM finance_matched').fetchone()[0]

    # Finance unmatched summary
    fin_unmatched_cnt = conn.execute('SELECT COUNT(*) FROM finance_unmatched').fetchone()[0]

    # 3. After-sales
    as_total_cnt, as_total_amt = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ?', (shop,)).fetchone()
    as_shipped_cnt, as_shipped_amt = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ? AND outbound_status = ?',
        (shop, '已出库')).fetchone()

    # 4. Spec costs from order_line_items (shop filter)
    spec_data = []
    for sku, cnt in conn.execute(
        'SELECT sku_name, COUNT(DISTINCT order_id) FROM order_line_items WHERE shop_name = ? GROUP BY sku_name',
        (shop,)).fetchall():
        cost = lookup_cost(sku)
        spec_data.append({'sku': sku, 'count': cnt, 'cost': cost, 'total_cost': cost * cnt})
    spec_data.sort(key=lambda x: -x['total_cost'])
    std_total_cost = sum(s['total_cost'] for s in spec_data)

    # 5. Spec-level actual financial data via SQL join (shop filter)
    spec_actuals = {}
    for sku, inc, exp in conn.execute(
        "SELECT oli.sku_name, COALESCE(SUM(fm.总收入),0), COALESCE(SUM(fm.总支出),0) "
        "FROM order_line_items oli LEFT JOIN finance_matched fm ON fm.tids = oli.order_id "
        "WHERE oli.shop_name = ? GROUP BY oli.sku_name",
        (shop,)).fetchall():
        spec_actuals[sku] = {'income': inc, 'expense': exp}

    # 6. Spec-level refund data via SQL join (shop filter)
    spec_refunds = {}
    for sku, ref_amt in conn.execute(
        "SELECT oli.sku_name, COALESCE(SUM(astotal.refund_amount),0) "
        "FROM order_line_items oli LEFT JOIN "
        "(SELECT tid, refund_amount FROM after_sales WHERE shop_name = ? GROUP BY tid) astotal "
        "ON astotal.tid = oli.order_id "
        "WHERE oli.shop_name = ? GROUP BY oli.sku_name",
        (shop, shop)).fetchall():
        spec_refunds[sku] = ref_amt

    # 7. After-sales reasons (已出库 only)
    as_reasons = conn.execute(
        "SELECT primary_reason, COUNT(*), COALESCE(SUM(refund_amount),0) "
        "FROM after_sales WHERE shop_name = ? AND outbound_status = '已出库' AND primary_reason != '' GROUP BY primary_reason ORDER BY COUNT(*) DESC",
        (shop,)).fetchall()

    # 8. Daily aggregates - ALL keyed by order payTime
    # Build tid -> payTime mapping (fast, indexed)
    tid_to_paydate = {}
    for tid, dt in conn.execute(
        "SELECT srcTids, SUBSTR(payTime,1,10) FROM erp_all_orders "
        "WHERE shopName = ? AND payTime IS NOT NULL",
        (shop,)).fetchall():
        tid_to_paydate[tid] = dt

    daily_orders = {}
    for dt, cnt, amt in conn.execute(
        "SELECT SUBSTR(payTime,1,10), COUNT(*), SUM(CAST(COALESCE(realAmount,'0') AS REAL)) "
        "FROM erp_all_orders WHERE shopName = ? AND payTime IS NOT NULL GROUP BY 1",
        (shop,)).fetchall():
        daily_orders[dt] = {'count': cnt, 'amount': amt}

    # Shipped & signed: group by payTime via Python lookup
    daily_shipped = _dd(int)
    daily_signed = _dd(int)
    for row in conn.execute(
        "SELECT srcTids, consignTime, traceStatusMsg FROM erp_all_orders "
        "WHERE shopName = ? AND consignTime IS NOT NULL AND consignTime != ''",
        (shop,)).fetchall():
        tid, ct, ts = row
        pd = tid_to_paydate.get(tid)
        if pd is None:
            continue
        daily_shipped[pd] += 1
        if ts and "已签收" in str(ts):
            daily_signed[pd] += 1

    # After-sales unshipped: group by payTime via Python lookup
    as_unshipped_date = _dd(lambda: {'count': 0, 'amount': 0})
    for row in conn.execute(
        "SELECT tid, refund_amount FROM after_sales "
        "WHERE shop_name = ? AND outbound_status = '未出库'",
        (shop,)).fetchall():
        tid, amt = row
        pd = tid_to_paydate.get(tid)
        if pd is None:
            continue
        as_unshipped_date[pd]['count'] += 1
        as_unshipped_date[pd]['amount'] += (amt or 0)

    # After-sales shipped: group by payTime via Python lookup
    as_shipped_date = _dd(lambda: {'count': 0, 'amount': 0})
    for row in conn.execute(
        "SELECT tid, refund_amount FROM after_sales "
        "WHERE shop_name = ? AND outbound_status = '已出库'",
        (shop,)).fetchall():
        tid, amt = row
        pd = tid_to_paydate.get(tid)
        if pd is None:
            continue
        as_shipped_date[pd]['count'] += 1
        as_shipped_date[pd]['amount'] += (amt or 0)

    # Finance: join via payTime
    fin_by_date = _dd(lambda: {'income': 0, 'expense': 0})
    for dt, inc, exp in conn.execute(
        "SELECT SUBSTR(o.payTime,1,10), COALESCE(SUM(fm.总收入),0), COALESCE(SUM(fm.总支出),0) "
        "FROM finance_matched fm JOIN erp_all_orders o ON o.srcTids = fm.tids "
        "WHERE o.payTime IS NOT NULL AND o.shopName = ? GROUP BY 1",
        (shop,)).fetchall():
        fin_by_date[dt] = {'income': inc, 'expense': exp}

    # ===== HTML Generation =====
    parts = []
    parts.append('<h1 style="color:#fff;font-size:22px;margin-bottom:6px;">标准成本 vs 实际成本对比</h1>')
    parts.append('<p style="color:#8b949e;margin-bottom:16px;font-size:13px;">数据截止：' + now.strftime("%Y-%m-%d %H:%M") + '</p>')

    # Section I: Overview
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">一、总览</h2>')
    cards = [
        ('已发货订单', fmt(shipped_cnt), '#00d4ff'),
        ('标准总成本', '¥' + fmt_amt(std_total_cost), '#d29922'),
        ('实际总收入', '¥' + fmt_amt(fin_total_income), '#00e676'),
        ('实际平台费', '¥' + fmt_amt(fin_total_expense), '#f85149'),
        ('实际退款总额', '¥' + fmt_amt(as_total_amt), '#f85149'),
        ('已出库售后', fmt(as_shipped_cnt) + '单', '#f97316'),
    ]
    parts.append('<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px;">')
    for label, value, color in cards:
        parts.append('<div style="background:#161b22;border:1px solid {};border-radius:10px;padding:16px;text-align:center;">'.format(color))
        parts.append('  <div style="color:#8b949e;font-size:11px;margin-bottom:4px;">' + label + '</div>')
        parts.append('  <div style="color:{};font-size:22px;font-weight:700;">'.format(color) + value + '</div>')
        parts.append('</div>')
    parts.append('</div>')

    # Section II: Spec Cost Detail
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">二、规格成本明细</h2>')
    parts.append('<div style="color:#8b949e;font-size:12px;margin-bottom:8px;">来自官方成本表（成本.xlsx）</div>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">规格</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#d29922;">成本(¥)</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">订单数</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">总成本</th>')
    parts.append('</tr></thead><tbody>')
    for s in spec_data:
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:6px;color:#c9d1d9;font-size:12px;">' + str(s['sku']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#d29922;font-weight:600;">¥' + '{:.2f}'.format(s['cost']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(s['count']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#fff;font-weight:600;">¥' + fmt_amt(s['total_cost']) + '</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')

    # Section III: Estimated vs Actual Deviation
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">三、预估 vs 实际 偏差</h2>')
    est_platform_rate = 2.0
    est_as_rate = 10.0
    act_platform_rate = (fin_total_expense / fin_total_income * 100) if fin_total_income > 0 else 0
    act_as_rate = (as_shipped_cnt / shipped_cnt * 100) if shipped_cnt > 0 else 0

    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:13px;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">指标</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">预估</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">实际</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">偏差</th>')
    parts.append('</tr></thead><tbody>')

    for label, est, act in [('平台费率', est_platform_rate, act_platform_rate), ('售后率', est_as_rate, act_as_rate)]:
        dev = act - est
        dev_color = '#00e676' if dev <= 0 else '#f85149'
        dev_sign = '+' if dev > 0 else ''
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:8px;color:#fff;">' + label + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#c9d1d9;">{:.1f}%</td>'.format(est))
        parts.append('<td style="padding:8px;text-align:right;color:#c9d1d9;">{:.2f}%</td>'.format(act))
        parts.append('<td style="padding:8px;text-align:right;color:' + dev_color + ';font-weight:600;">{}{:.1f}个百分点</td>'.format(dev_sign, dev))
        parts.append('</tr>')

    avg_ref = as_total_amt / as_total_cnt if as_total_cnt > 0 else 0
    parts.append('<tr style="border-bottom:1px solid #21262d;">')
    parts.append('<td style="padding:8px;color:#fff;">退款总额</td>')
    parts.append('<td style="padding:8px;text-align:right;color:#c9d1d9;">-</td>')
    parts.append('<td style="padding:8px;text-align:right;color:#f85149;">¥' + fmt_amt(as_total_amt) + '</td>')
    parts.append('<td style="padding:8px;text-align:right;color:#8b949e;">均摊 ¥{:.0f}/单</td>'.format(avg_ref))
    parts.append('</tr>')
    parts.append('</tbody></table></div>')

    # Section IV: Per-Spec Cost vs Actual
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">四、按规格 成本 vs 实际费用</h2>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;">')
    for h, c in [('规格', 'left'), ('订单数', 'right'), ('成本/件', 'right'), ('总成本', 'right'), ('实际收入', 'right'), ('实际平台费', 'right'), ('实际退款', 'right')]:
        parts.append('<th style="padding:8px;text-align:{};color:#8b949e;">{}</th>'.format(c, h))
    parts.append('</tr></thead><tbody>')
    for s in spec_data:
        sku = s['sku']
        act = spec_actuals.get(sku, {'income': 0, 'expense': 0})
        ref = spec_refunds.get(sku, 0)
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:6px;color:#c9d1d9;font-size:12px;">' + str(sku) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(s['count']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#d29922;">¥' + '{:.2f}'.format(s['cost']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#d29922;">¥' + fmt_amt(s['total_cost']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#00e676;">¥' + fmt_amt(act['income']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#f85149;">¥' + fmt_amt(act['expense']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#f85149;">¥' + fmt_amt(ref) + '</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')

    # Section V: After-sales Cost by Reason
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">五、实际售后成本（已出库）</h2>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:13px;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">退单理由</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">单数</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款总额</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">单笔均摊</th>')
    parts.append('</tr></thead><tbody>')
    quality_kw = ['重量不足', '腐烂', '变质', '发霉', '死包']
    for reason, cnt, amt in as_reasons:
        is_q = any(kw in str(reason) for kw in quality_kw)
        rc = '#f85149' if is_q else '#d29922'
        avg_r = amt / cnt if cnt > 0 else 0
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:6px;color:' + rc + ';">' + str(reason) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(cnt) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#f85149;">¥' + fmt_amt(amt) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#8b949e;">¥' + '{:.0f}'.format(avg_r) + '</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')

    # Section VI: Platform Fees
    plat_rate = fin_total_expense / fin_total_income * 100 if fin_total_income > 0 else 0
    avg_inc = fin_total_income / fin_cnt if fin_cnt > 0 else 0
    avg_exp = fin_total_expense / fin_cnt if fin_cnt > 0 else 0

    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">六、按平台 实际平台费</h2>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:13px;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;">')
    for h in ['平台', '订单数', '平均收入', '平均平台费', '实际费率', '总平台费']:
        parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">' + h + '</th>')
    parts.append('</tr></thead><tbody>')
    parts.append('<tr style="border-bottom:1px solid #21262d;">')
    parts.append('<td style="padding:8px;color:#fff;">京东</td>')
    parts.append('<td style="padding:8px;text-align:right;color:#fff;">' + fmt(fin_cnt) + '</td>')
    parts.append('<td style="padding:8px;text-align:right;color:#c9d1d9;">¥' + fmt_amt(avg_inc) + '</td>')
    parts.append('<td style="padding:8px;text-align:right;color:#f85149;">¥' + fmt_amt(avg_exp) + '</td>')
    parts.append('<td style="padding:8px;text-align:right;color:#d29922;">{:.2f}%</td>'.format(plat_rate))
    parts.append('<td style="padding:8px;text-align:right;color:#f85149;">¥' + fmt_amt(fin_total_expense) + '</td>')
    parts.append('</tr>')
    parts.append('</tbody></table></div>')

    # Section VII: Daily Profit Table (30 days)
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">七、每日订单利润表</h2>')
    parts.append('<div style="color:#8b949e;font-size:12px;margin-bottom:8px;">30天滚动利润分析</div>')
    parts.append('<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;">')
    src_tags = ['', 'ERP', 'ERP', 'ERP', 'ERP', '计算', '计算', '计算', '售后', '售后', '计算', '计算', '供应商', '财务', '财务', '售后', '计算']
    src_colors = {'ERP': '#64b5f6', '计算': '#bdbdbd', '售后': '#ffb74d', '供应商': '#81c784', '财务': '#ce93d8', '': ''}
    for (h, al), src in zip([
        ('日期', 'left'), ('总订单', 'right'), ('订单金额', 'right'),
        ('已发货', 'right'), ('已签收', 'right'), ('发货率', 'right'),
        ('未发货', 'right'), ('未发货金额', 'right'),
        ('未发货退单', 'right'), ('未发货退款', 'right'),
        ('剩余发货量', 'right'), ('剩余发货金额', 'right'),
        ('标准成本', 'right'), ('总收入', 'right'),
        ('平台费', 'right'), ('已发货退款', 'right'), ('利润', 'right')
    ], src_tags):
        sc = src_colors.get(src, '#8b949e')
        parts.append('<th style="padding:6px;text-align:{};color:#8b949e;">{}<br><span style="color:{};font-size:9px;">{}</span></th>'.format(al, h, sc, src))
    parts.append('</tr></thead><tbody>')

    all_dates = sorted(set(list(daily_orders.keys()) + list(daily_shipped.keys())))[:30]
    avg_cost_per_order = std_total_cost / shipped_cnt if shipped_cnt > 0 else 0

    profit_rows = []
    for dt in all_dates:
        to = daily_orders.get(dt, {}).get("count", 0)
        ta = daily_orders.get(dt, {}).get("amount", 0)
        sh = daily_shipped.get(dt, 0)
        si = daily_signed.get(dt, 0)
        ship_rate = pct(sh, to) if to > 0 else "-"
        unsh = max(0, to - sh)
        unsh_amt = ta * (unsh / to) if to > 0 else 0

        au = as_unshipped_date.get(dt, {"count": 0, "amount": 0})
        ash = as_shipped_date.get(dt, {"count": 0, "amount": 0})
        fi = fin_by_date.get(dt, {"income": 0, "expense": 0})
        fi_inc = fi["income"]
        fi_exp = fi["expense"]
        date_std_cost = avg_cost_per_order * sh
        profit = fi_inc - fi_exp - ash["amount"] - date_std_cost
        pc = "#00e676" if profit >= 0 else "#f85149"

        rem_ship = max(0, to - au["count"])
        rem_ship_amt = max(0, ta - au["amount"])

        profit_rows.append((dt, to, ta, sh, si, ship_rate, unsh, unsh_amt,
                            au, ash, fi_inc, fi_exp, date_std_cost, profit, pc,
                            rem_ship, rem_ship_amt))

    for row in reversed(profit_rows):
        dt, to, ta, sh, si, ship_rate, unsh, unsh_amt, \
            au, ash, fi_inc, fi_exp, date_std_cost, profit, pc, \
            rem_ship, rem_ship_amt = row
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:4px 6px;color:#00d4ff;font-weight:600;">' + str(dt) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#c9d1d9;">' + fmt(to) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#c9d1d9;">' + chr(165) + fmt_amt(ta) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#fff;">' + fmt(sh) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#c9d1d9;">' + fmt(si) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#c9d1d9;">' + ship_rate + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#c9d1d9;">' + fmt(unsh) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#c9d1d9;">' + chr(165) + fmt_amt(unsh_amt) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#f85149;">' + fmt(au["count"]) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#f85149;">' + chr(165) + fmt_amt(au["amount"]) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#c9d1d9;">' + fmt(rem_ship) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#c9d1d9;">' + chr(165) + fmt_amt(rem_ship_amt) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#d29922;">' + chr(165) + fmt_amt(date_std_cost) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#00e676;">' + chr(165) + fmt_amt(fi_inc) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#f85149;">' + chr(165) + fmt_amt(fi_exp) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:#f85149;">' + chr(165) + fmt_amt(ash["amount"]) + '</td>')
        parts.append('<td style="padding:4px 6px;text-align:right;color:' + pc + ';font-weight:700;">' + chr(165) + fmt_amt(profit) + '</td>')
        parts.append('</tr>')


    parts.append('</tbody></table></div>')

    return chr(10).join(parts)


@cached_page('aftersales')
def render_aftersales():
    import sqlite3
    DB_PATH = os.path.join(BASE, "erp_all.db")
    key = 'aftersales'
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
        return page('售后专题', 'aftersales', _cache[key][1])
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        html = _build_aftersales_html(conn)
        conn.close()
    except Exception as e:
        html = '<p style="color:#f85149;">数据加载失败: ' + str(e) + '</p>'
    _cache[key] = (now, html)
    return page('售后专题', 'aftersales', html)

def _build_aftersales_html(conn):
    from datetime import datetime
    from collections import defaultdict as _dd
    import json as _json
    now = datetime.now()
    shop = '榴愿时刻工厂店'

    # ===== 1. Overall =====
    as_total_cnt = conn.execute('SELECT COUNT(*) FROM after_sales WHERE shop_name = ?', (shop,)).fetchone()[0]
    unshipped_cnt, unshipped_amt = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ? AND outbound_status = ?',
        (shop, '未出库')).fetchone()
    shipped_cnt, shipped_amt = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ? AND outbound_status = ?',
        (shop, '已出库')).fetchone()

    # ===== 2. Status breakdown =====
    unshipped_status = conn.execute(
        'SELECT service_status, COUNT(*) as cnt FROM after_sales WHERE shop_name = ? AND outbound_status = ? GROUP BY service_status ORDER BY cnt DESC',
        (shop, '未出库')).fetchall()
    shipped_status = conn.execute(
        'SELECT service_status, COUNT(*) as cnt FROM after_sales WHERE shop_name = ? AND outbound_status = ? GROUP BY service_status ORDER BY cnt DESC',
        (shop, '已出库')).fetchall()

    # ===== 3. Refund reasons =====
    unshipped_reasons = conn.execute(
        'SELECT primary_reason, COUNT(*), COALESCE(SUM(refund_amount),0) '
        'FROM after_sales WHERE shop_name = ? AND outbound_status = ? AND primary_reason != "" GROUP BY primary_reason ORDER BY COUNT(*) DESC',
        (shop, '未出库')).fetchall()
    shipped_reasons = conn.execute(
        'SELECT primary_reason, COUNT(*), COALESCE(SUM(refund_amount),0) '
        'FROM after_sales WHERE shop_name = ? AND outbound_status = ? AND primary_reason != "" GROUP BY primary_reason ORDER BY COUNT(*) DESC',
        (shop, '已出库')).fetchall()

    # ===== 4. Daily orders by payDate =====
    # 用 order_line_items.share_amount 统计订单金额
    daily_orders = {}
    for dt, cnt, amt in conn.execute(
        "SELECT SUBSTR(pay_time,1,10), COUNT(DISTINCT order_id), COALESCE(SUM(share_amount),0) "
        "FROM order_line_items WHERE shop_name = ? AND pay_time IS NOT NULL GROUP BY 1",
        (shop,)).fetchall():
        daily_orders[dt] = {"count": cnt, "amount": amt}

    # ===== 5. Daily shipped by consignDate =====
    # 用 order_line_items.share_amount 统计发货金额
    daily_shipped = {}
    for dt, cnt, amt in conn.execute(
        "SELECT SUBSTR(actual_ship_time,1,10), COUNT(DISTINCT order_id), COALESCE(SUM(share_amount),0) "
        "FROM order_line_items WHERE shop_name = ? AND actual_ship_time IS NOT NULL GROUP BY 1",
        (shop,)).fetchall():
        daily_shipped[dt] = {"count": cnt, "amount": amt}

    # ===== 6. Daily signed (已发货 status) by consignDate =====
    daily_signed = _dd(int)
    for dt, cnt in conn.execute(
        "SELECT SUBSTR(consignTime,1,10), COUNT(*) "
        "FROM erp_all_orders WHERE shopName = ? AND consignTime IS NOT NULL AND traceStatusMsg LIKE '%已签收%' GROUP BY 1",
        (shop,)).fetchall():
        daily_signed[dt] = cnt

    # ===== 7. Daily shipped by warehouse =====
    daily_shipped_wh = _dd(lambda: _dd(lambda: {'count': 0, 'amount': 0}))
    for wh, dt, cnt, amt in conn.execute(
        "SELECT warehouseName, SUBSTR(consignTime,1,10), COUNT(*), SUM(CAST(COALESCE(realAmount,'0') AS REAL)) "
        "FROM erp_all_orders WHERE shopName = ? AND consignTime IS NOT NULL GROUP BY 1, 2",
        (shop,)).fetchall():
        daily_shipped_wh[wh][dt] = {'count': cnt, 'amount': amt}

    # ===== 7b. Daily signed by warehouse =====
    daily_signed_wh = _dd(lambda: _dd(int))
    for wh, dt, cnt in conn.execute(
        "SELECT warehouseName, SUBSTR(consignTime,1,10), COUNT(*) "
        "FROM erp_all_orders WHERE shopName = ? AND consignTime IS NOT NULL AND traceStatusMsg LIKE '%已签收%' GROUP BY 1, 2",
        (shop,)).fetchall():
        daily_signed_wh[wh][dt] = cnt

    # ===== 8. Aftersales by order payTime =====
    # 售后按订单下单日期统计
    as_by_date = _dd(lambda: _dd(lambda: {"count": 0, "amount": 0}))
    for dt, ob, cnt, amt in conn.execute(
        "SELECT SUBSTR(o.payTime,1,10), a.outbound_status, COUNT(*), COALESCE(SUM(a.refund_amount),0) FROM after_sales a INNER JOIN erp_all_orders o ON a.tid = o.tid WHERE a.shop_name = ? AND o.payTime IS NOT NULL GROUP BY 1, 2",
        (shop,)).fetchall():
        as_by_date[dt][ob] = {"count": cnt, "amount": amt}

    # ===== 9. Aftersales reasons by date (top 3) =====
    as_reasons_by_date = _dd(lambda: [])
    for dt, ob, reason, cnt, amt in conn.execute(
        "SELECT SUBSTR(apply_time,1,10), outbound_status, primary_reason, COUNT(*), COALESCE(SUM(refund_amount),0) "
        "FROM after_sales WHERE shop_name = ? AND primary_reason != '' GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2, COUNT(*) DESC",
        (shop,)).fetchall():
        key = (dt, ob)
        if len(as_reasons_by_date[key]) < 3:
            as_reasons_by_date[key].append((reason, cnt, amt))

    # ===== 10-12. Aftersales by ship date via Python-side lookup (SQL JOIN too slow) =====
    # Build tid -> consignTime date mapping
    tid_to_shipdate = {}
    for tid, dt in conn.execute(
        "SELECT srcTids, SUBSTR(consignTime,1,10) FROM erp_all_orders "
        "WHERE consignTime IS NOT NULL AND consignTime != ''").fetchall():
        tid_to_shipdate[tid] = dt

    # Fetch all 已出库 after-sales rows and group by ship date via Python lookup
    as_by_shipdate = _dd(lambda: _dd(lambda: {'count': 0, 'amount': 0}))
    as_count_by_shipdate_wh = _dd(lambda: _dd(lambda: {'count': 0, 'amount': 0}))
    as_reasons_by_shipdate = _dd(list)  # [(date, reason, amount)]

    for row in conn.execute(
        "SELECT tid, outbound_status, primary_reason, refund_amount, warehouse_name "
        "FROM after_sales WHERE shop_name = ? AND outbound_status = '已出库'",
        (shop,)).fetchall():
        tid, ob, reason, amt, wh = row
        dt = tid_to_shipdate.get(tid)
        if dt is None:
            continue
        as_by_shipdate[dt][ob]['count'] += 1
        as_by_shipdate[dt][ob]['amount'] += (amt or 0)
        if reason:
            as_reasons_by_shipdate[dt].append((reason, amt or 0))
        if wh:
            as_count_by_shipdate_wh[wh][dt]['count'] += 1
            as_count_by_shipdate_wh[wh][dt]['amount'] += (amt or 0)

    # Aggregate top 3 reasons per ship date
    as_reasons_shipdate = _dd(lambda: [])
    for dt, entries in as_reasons_by_shipdate.items():
        from collections import Counter
        reason_counts = Counter()
        reason_amounts = _dd(float)
        for reason, amount in entries:
            reason_counts[reason] += 1
            reason_amounts[reason] += amount
        top3 = sorted(reason_counts.keys(), key=lambda r: -reason_counts[r])[:3]
        as_reasons_shipdate[dt] = [(r, reason_counts[r], reason_amounts[r]) for r in top3]

    # Aggregate top 3 reasons per warehouse per ship date
    as_reasons_shipdate_wh = _dd(lambda: _dd(list))
    as_reasons_wh_raw = _dd(lambda: _dd(list))
    for row in conn.execute(
        "SELECT tid, warehouse_name, primary_reason, refund_amount "
        "FROM after_sales WHERE shop_name = ? AND outbound_status = '已出库' "
        "AND warehouse_name != '' AND primary_reason != ''",
        (shop,)).fetchall():
        tid, wh, reason, amt = row
        dt = tid_to_shipdate.get(tid)
        if dt is None:
            continue
        as_reasons_wh_raw[wh][dt].append((reason, amt or 0))

    for wh, dates in as_reasons_wh_raw.items():
        for dt, entries in dates.items():
            from collections import Counter
            reason_counts = Counter()
            reason_amounts = _dd(float)
            for reason, amount in entries:
                reason_counts[reason] += 1
                reason_amounts[reason] += amount
            top3 = sorted(reason_counts.keys(), key=lambda r: -reason_counts[r])[:3]
            as_reasons_shipdate_wh[wh][dt] = [(r, reason_counts[r], reason_amounts[r]) for r in top3]

    # ===== 13. 7-day trend =====
    trend_7d = []
    for dt, ob, cnt, amt in conn.execute(
        "SELECT SUBSTR(apply_time,1,10), outbound_status, COUNT(*), COALESCE(SUM(refund_amount),0) "
        "FROM after_sales WHERE shop_name = ? AND apply_time >= date('now', '-7 days') GROUP BY 1, 2 ORDER BY 1 DESC",
        (shop,)).fetchall():
        trend_7d.append((dt, ob, cnt, amt))

    # ===== 14. Rejected =====
    reject_cnt, reject_paid, reject_net = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(erp_paid),0), COALESCE(SUM(finance_net),0) '
        'FROM after_sales WHERE shop_name = ? AND service_status = ?',
        (shop, '审核不通过')).fetchone()
    reject_reasons = conn.execute(
        'SELECT primary_reason, COUNT(*), COALESCE(SUM(erp_paid),0) '
        'FROM after_sales WHERE shop_name = ? AND service_status = ? '
        'GROUP BY primary_reason ORDER BY COUNT(*) DESC',
        (shop, '审核不通过')).fetchall()

    quality_keywords = ['重量不足', '腐烂', '变质', '发霉', '死包']

    def fmt(n):
        try:
            return '{:,.0f}'.format(n)
        except (ValueError, TypeError):
            return '0'
    def fmt_amt(n):
        try:
            return '{:,.0f}'.format(n)
        except (ValueError, TypeError):
            return '0'
    def pct(a, b):
        try:
            return '{:.1f}%'.format(a / b * 100) if b > 0 else '-'
        except:
            return '-'
    def top3_reasons(reasons_list):
        return ' | '.join(f'{r[0]}({r[1]})' for r in reasons_list[:3])

    parts = []
    # ===== Header =====
    parts.append('<h1 style="color:#fff;font-size:22px;margin-bottom:6px;">售后专题报告</h1>')
    parts.append('<p style="color:#8b949e;margin-bottom:16px;font-size:13px;">数据截止：' + now.strftime("%Y年%-m月%-d日") + ' | 售后总计 {:,} 单，退款金额 ¥{:,.0f}</p>'.format(as_total_cnt, unshipped_amt + shipped_amt))

    # ===== Section I: Overview =====
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">一、总览</h2>')
    parts.append('<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;margin-bottom:16px;">')
    parts.append('<div style="background:#161b22;border:1px solid #f85149;border-radius:10px;padding:16px;">')
    parts.append('  <div style="color:#8b949e;font-size:12px;margin-bottom:4px;">未出库退款</div>')
    parts.append('  <div style="color:#fff;font-size:28px;font-weight:700;">' + fmt(unshipped_cnt) + '</div>')
    parts.append('  <div style="color:#f85149;font-size:14px;">¥' + fmt_amt(unshipped_amt) + '</div>')
    parts.append('</div>')
    parts.append('<div style="background:#161b22;border:1px solid #f97316;border-radius:10px;padding:16px;">')
    parts.append('  <div style="color:#8b949e;font-size:12px;margin-bottom:4px;">已出库退款</div>')
    parts.append('  <div style="color:#fff;font-size:28px;font-weight:700;">' + fmt(shipped_cnt) + '</div>')
    parts.append('  <div style="color:#f97316;font-size:14px;">¥' + fmt_amt(shipped_amt) + '</div>')
    parts.append('</div>')
    parts.append('<div style="background:#161b22;border:1px solid #3b82f6;border-radius:10px;padding:16px;">')
    parts.append('  <div style="color:#8b949e;font-size:12px;margin-bottom:4px;">售后总申请</div>')
    parts.append('  <div style="color:#fff;font-size:28px;font-weight:700;">' + fmt(as_total_cnt) + '</div>')
    parts.append('  <div style="color:#3b82f6;font-size:14px;">¥' + fmt_amt(unshipped_amt + shipped_amt) + '</div>')
    parts.append('</div>')
    parts.append('</div>')
    parts.append('<div style="color:#8b949e;font-size:12px;margin-bottom:24px;padding:8px 0;border-bottom:1px solid #30363d;">未出库退款无实物损失；已出库退款涉及货物损失、物流费用、快递成本等真实损耗。</div>')

    # ===== Section II: Unshipped reasons =====
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">二、未出库退单（' + fmt(unshipped_cnt) + ' 单，¥' + fmt_amt(unshipped_amt) + '）</h2>')
    parts.append('<div style="color:#8b949e;font-size:12px;margin-bottom:8px;">商品尚未发出</div>')
    # Status table
    parts.append('<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:12px;">')
    parts.append('<thead><tr style="border-bottom:1px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">状态</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">单数</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th>')
    parts.append('</tr></thead><tbody>')
    for st, cnt in unshipped_status:
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:8px;color:#fff;">' + str(st) + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#c9d1d9;">' + fmt(cnt) + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#f85149;">¥' + fmt_amt(unshipped_amt if len(unshipped_status)==1 else cnt * unshipped_amt / unshipped_cnt) + '</td>')
        parts.append('</tr>')
    parts.append('</tbody></table>')

    # Reasons with bar chart
    parts.append('<h3 style="color:#c9d1d9;font-size:14px;margin-bottom:8px;">全部退单理由</h3>')
    parts.append('<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:24px;">')
    parts.append('<thead><tr style="border-bottom:1px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">理由</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">单数</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">占比</th>')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;width:200px;">分布</th>')
    parts.append('</tr></thead><tbody>')
    max_un = max([r[1] for r in unshipped_reasons], default=1)
    for reason, cnt, amt in unshipped_reasons:
        bar_w = cnt / max_un * 100
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:6px;color:#c9d1d9;">' + str(reason) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(cnt) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#f85149;">¥' + fmt_amt(amt) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#8b949e;">' + pct(cnt, unshipped_cnt) + '</td>')
        parts.append('<td style="padding:6px;"><span style="display:inline-block;width:' + str(int(bar_w)) + 'px;height:8px;background:#f85149;border-radius:2px;"></span></td>')
        parts.append('</tr>')
    parts.append('</tbody></table>')

    # ===== Section III: Shipped reasons =====
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">三、已出库退单（' + fmt(shipped_cnt) + ' 单，¥' + fmt_amt(shipped_amt) + '）</h2>')
    parts.append('<div style="color:#8b949e;font-size:12px;margin-bottom:8px;">已产生物流费用，涉及真实货物损失</div>')
    # Status table
    parts.append('<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:12px;">')
    parts.append('<thead><tr style="border-bottom:1px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">状态</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">单数</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th>')
    parts.append('</tr></thead><tbody>')
    for st, cnt in shipped_status:
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:8px;color:#fff;">' + str(st) + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#c9d1d9;">' + fmt(cnt) + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#f97316;">¥' + fmt_amt(shipped_amt if len(shipped_status)==1 else cnt * shipped_amt / shipped_cnt) + '</td>')
        parts.append('</tr>')
    parts.append('</tbody></table>')

    # Reasons
    parts.append('<h3 style="color:#c9d1d9;font-size:14px;margin-bottom:8px;">全部退单理由</h3>')
    parts.append('<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:24px;">')
    parts.append('<thead><tr style="border-bottom:1px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">理由</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">单数</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">占比</th>')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;width:200px;">分布</th>')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">标签</th>')
    parts.append('</tr></thead><tbody>')
    max_sh = max([r[1] for r in shipped_reasons], default=1)
    for reason, cnt, amt in shipped_reasons:
        is_q = any(kw in str(reason) for kw in quality_keywords)
        bar_w = cnt / max_sh * 100
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:6px;color:' + ('#f85149' if is_q else '#c9d1d9') + ';">' + str(reason) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(cnt) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#f97316;">¥' + fmt_amt(amt) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#8b949e;">' + pct(cnt, shipped_cnt) + '</td>')
        parts.append('<td style="padding:6px;"><span style="display:inline-block;width:' + str(int(bar_w)) + 'px;height:8px;background:' + ('#f85149' if is_q else '#f97316') + ';border-radius:2px;"></span></td>')
        parts.append('<td style="padding:6px;">' + ('<span style="color:#f85149;font-size:11px;">质量相关</span>' if is_q else '') + '</td>')
        parts.append('</tr>')
    parts.append('</tbody></table>')
    parts.append('<div style="color:#f85149;font-size:12px;margin-bottom:24px;">红色条为质量相关原因，属于真实品质损耗。</div>')

    # ===== Section IV: Unshipped by order date =====
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">四、未出库售后 — 按下单日期统计</h2>')
    parts.append('<div style="color:#8b949e;font-size:12px;margin-bottom:8px;">未出库售后率 = 未出库售后单数 / 当日下单总单数</div>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">下单日期</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">当日下单</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">订单金额</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">未出库售后</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">未出库售后率</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款/订单</th>')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">分布</th>')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">Top退单理由</th>')
    parts.append('</tr></thead><tbody>')
    all_dates_ordered = sorted(set(list(daily_orders.keys()) + list(as_by_date.keys())), reverse=True)
    max_as_un = max([as_by_date[dt]['未出库']['count'] for dt in as_by_date if '未出库' in as_by_date[dt]], default=1)
    for dt in all_dates_ordered:
        oinfo = daily_orders.get(dt, {'count': 0, 'amount': 0})
        ainfo = as_by_date[dt].get('未出库', {'count': 0, 'amount': 0})
        if ainfo['count'] == 0 and oinfo['count'] == 0:
            continue
        rate = pct(ainfo['count'], oinfo['count']) if oinfo['count'] > 0 else '-'
        refund_rate = pct(ainfo['amount'], oinfo['amount']) if oinfo['amount'] > 0 else '-'
        bar_w = ainfo['count'] / max_as_un * 100 if max_as_un > 0 else 0
        reasons = as_reasons_by_date.get((dt, '未出库'), [])
        reason_str = top3_reasons(reasons)
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:6px;color:#00d4ff;">' + str(dt) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(oinfo['count']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#00e676;">¥' + fmt_amt(oinfo['amount']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(ainfo['count']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#f97316;">' + rate + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#f85149;">¥' + fmt_amt(ainfo['amount']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#8b949e;">' + refund_rate + '</td>')
        parts.append('<td style="padding:6px;"><span style="display:inline-block;width:' + str(int(bar_w)) + 'px;height:8px;background:#f97316;border-radius:2px;"></span></td>')
        parts.append('<td style="padding:6px;color:#8b949e;font-size:11px;">' + reason_str + '</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')

    # ===== Section V: Shipped by ship date =====
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">五、已出库售后 — 按发货日期统计</h2>')
    parts.append('<div style="color:#8b949e;font-size:12px;margin-bottom:8px;">已出库售后率 = 已出库售后单数 / 当日发货总单数</div>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">发货日期</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">当日发货</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">发货金额</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已签收</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已签收/发货</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已出库售后</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已出库售后率</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款/发货</th>')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">分布</th>')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">Top退单理由</th>')
    parts.append('</tr></thead><tbody>')
    all_ship_dates = sorted(set(list(daily_shipped.keys()) + list(as_by_shipdate.keys())), reverse=True)
    max_as_sh = max([as_by_shipdate[dt]['已出库']['count'] for dt in as_by_shipdate if '已出库' in as_by_shipdate[dt]], default=1)
    for dt in all_ship_dates:
        sinfo = daily_shipped.get(dt, {'count': 0, 'amount': 0})
        ainfo2 = as_by_shipdate[dt].get('已出库', {'count': 0, 'amount': 0})
        signed = daily_signed.get(dt, 0)
        if ainfo2['count'] == 0 and sinfo['count'] == 0:
            continue
        sign_rate = pct(signed, sinfo['count']) if sinfo['count'] > 0 else '-'
        as_rate = pct(ainfo2['count'], sinfo['count']) if sinfo['count'] > 0 else '-'
        refund_r = pct(ainfo2['amount'], sinfo['amount']) if sinfo['amount'] > 0 else '-'
        bar_w2 = ainfo2['count'] / max_as_sh * 100 if max_as_sh > 0 else 0
        reasons2 = as_reasons_shipdate.get(dt, [])
        reason_str2 = ' | '.join(f'{r[0]}({r[1]}单 · ¥{fmt_amt(r[2])})' for r in reasons2[:3])
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:6px;color:#00d4ff;">' + str(dt) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(sinfo['count']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#00e676;">¥' + fmt_amt(sinfo['amount']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(signed) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#3b82f6;">' + sign_rate + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(ainfo2['count']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#f97316;">' + as_rate + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#f85149;">¥' + fmt_amt(ainfo2['amount']) + '</td>')
        parts.append('<td style="padding:6px;text-align:right;color:#8b949e;">' + refund_r + '</td>')
        parts.append('<td style="padding:6px;"><span style="display:inline-block;width:' + str(int(bar_w2)) + 'px;height:8px;background:#f97316;border-radius:2px;"></span></td>')
        parts.append('<td style="padding:6px;color:#8b949e;font-size:11px;">' + reason_str2 + '</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')

    # ===== Section V-1: Yunnan warehouse =====
    yunnan_dates = set(daily_shipped_wh.get('云南榴莲1号仓库', {}).keys()) | set(as_count_by_shipdate_wh.get('云南榴莲1号仓库', {}).keys())
    if yunnan_dates:
        parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">五-1、已出库售后 — 按发货日期统计（云南仓）</h2>')
        parts.append('<div style="color:#8b949e;font-size:12px;margin-bottom:8px;">已出库售后率 = 已出库售后单数 / 当日发货总单数</div>')
        parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap;">')
        parts.append('<thead><tr style="border-bottom:2px solid #30363d;">')
        parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">发货日期</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">当日发货</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">发货金额</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已签收</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已签收/发货</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已出库售后</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已出库售后率</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款/发货</th>')
        parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">分布</th>')
        parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">Top退单理由</th>')
        parts.append('</tr></thead><tbody>')
        wh_ship = daily_shipped_wh.get('云南榴莲1号仓库', {})
        wh_as = as_count_by_shipdate_wh.get('云南榴莲1号仓库', {})
        wh_asr = as_reasons_shipdate_wh.get('云南榴莲1号仓库', {})
        max_as_yn = max([wh_as[dt]['count'] for dt in wh_as], default=1)
        for dt in sorted(yunnan_dates, reverse=True):
            sinfo2 = wh_ship.get(dt, {'count': 0, 'amount': 0})
            ainfo3 = wh_as.get(dt, {'count': 0, 'amount': 0})
            signed2 = daily_signed_wh.get('云南榴莲1号仓库', {}).get(dt, 0)
            if ainfo3['count'] == 0 and sinfo2['count'] == 0:
                continue
            sign_rate2 = pct(signed2, sinfo2['count']) if sinfo2['count'] > 0 else '-'
            as_rate2 = pct(ainfo3['count'], sinfo2['count']) if sinfo2['count'] > 0 else '-'
            refund_r2 = pct(ainfo3['amount'], sinfo2['amount']) if sinfo2['amount'] > 0 else '-'
            bar_w3 = ainfo3['count'] / max_as_yn * 100 if max_as_yn > 0 else 0
            reasons3 = wh_asr.get(dt, [])
            reason_str3 = ' | '.join(f'{r[0]}({r[1]}单 · ¥{fmt_amt(r[2])})' for r in reasons3[:3])
            parts.append('<tr style="border-bottom:1px solid #21262d;">')
            parts.append('<td style="padding:6px;color:#00d4ff;">' + str(dt) + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(sinfo2['count']) + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#00e676;">¥' + fmt_amt(sinfo2['amount']) + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(signed2) + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#3b82f6;">' + sign_rate2 + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(ainfo3['count']) + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#f97316;">' + as_rate2 + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#f85149;">¥' + fmt_amt(ainfo3['amount']) + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#8b949e;">' + refund_r2 + '</td>')
            parts.append('<td style="padding:6px;"><span style="display:inline-block;width:' + str(int(bar_w3)) + 'px;height:8px;background:#f97316;border-radius:2px;"></span></td>')
            parts.append('<td style="padding:6px;color:#8b949e;font-size:11px;">' + reason_str3 + '</td>')
            parts.append('</tr>')
        parts.append('</tbody></table></div>')

    # ===== Section V-2: AJ factory =====
    aj_dates = set(daily_shipped_wh.get('金枕榴莲泰国直发AJ工厂', {}).keys()) | set(as_count_by_shipdate_wh.get('金枕榴莲泰国直发AJ工厂', {}).keys())
    if aj_dates:
        parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">五-2、已出库售后 — 按发货日期统计（AJ工厂）</h2>')
        parts.append('<div style="color:#8b949e;font-size:12px;margin-bottom:8px;">已出库售后率 = 已出库售后单数 / 当日发货总单数</div>')
        parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap;">')
        parts.append('<thead><tr style="border-bottom:2px solid #30363d;">')
        parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">发货日期</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">当日发货</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">发货金额</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已签收</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已签收/发货</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已出库售后</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已出库售后率</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th>')
        parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款/发货</th>')
        parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">分布</th>')
        parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">Top退单理由</th>')
        parts.append('</tr></thead><tbody>')
        wh_ship2 = daily_shipped_wh.get('金枕榴莲泰国直发AJ工厂', {})
        wh_as2 = as_count_by_shipdate_wh.get('金枕榴莲泰国直发AJ工厂', {})
        wh_asr2 = as_reasons_shipdate_wh.get('金枕榴莲泰国直发AJ工厂', {})
        max_as_aj = max([wh_as2[dt]['count'] for dt in wh_as2], default=1)
        for dt in sorted(aj_dates, reverse=True):
            sinfo3 = wh_ship2.get(dt, {'count': 0, 'amount': 0})
            ainfo4 = wh_as2.get(dt, {'count': 0, 'amount': 0})
            signed3 = daily_signed_wh.get('金枕榴莲泰国直发AJ工厂', {}).get(dt, 0)
            if ainfo4['count'] == 0 and sinfo3['count'] == 0:
                continue
            as_rate3 = pct(ainfo4['count'], sinfo3['count']) if sinfo3['count'] > 0 else '-'
            refund_r3 = pct(ainfo4['amount'], sinfo3['amount']) if sinfo3['amount'] > 0 else '-'
            bar_w4 = ainfo4['count'] / max_as_aj * 100 if max_as_aj > 0 else 0
            reasons4 = wh_asr2.get(dt, [])
            reason_str4 = ' | '.join(f'{r[0]}({r[1]}单 · ¥{fmt_amt(r[2])})' for r in reasons4[:3])
            parts.append('<tr style="border-bottom:1px solid #21262d;">')
            parts.append('<td style="padding:6px;color:#00d4ff;">' + str(dt) + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(sinfo3['count']) + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#00e676;">¥' + fmt_amt(sinfo3['amount']) + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(signed3) + '</td>')
            sign_rate3 = pct(signed3, sinfo3['count']) if sinfo3['count'] > 0 else '-'
            parts.append('<td style="padding:6px;text-align:right;color:#3b82f6;">' + sign_rate3 + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#fff;">' + fmt(ainfo4['count']) + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#f97316;">' + as_rate3 + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#f85149;">¥' + fmt_amt(ainfo4['amount']) + '</td>')
            parts.append('<td style="padding:6px;text-align:right;color:#8b949e;">' + refund_r3 + '</td>')
            parts.append('<td style="padding:6px;"><span style="display:inline-block;width:' + str(int(bar_w4)) + 'px;height:8px;background:#f97316;border-radius:2px;"></span></td>')
            parts.append('<td style="padding:6px;color:#8b949e;font-size:11px;">' + reason_str4 + '</td>')
            parts.append('</tr>')
        parts.append('</tbody></table></div>')

    # ===== Section VI: 7-day trend =====
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">六、近7天售后趋势（按申请时间）</h2>')
    parts.append('<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:24px;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;">')
    parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">日期</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">未出库</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">已出库</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">合计</th>')
    parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th>')
    parts.append('</tr></thead><tbody>')
    trend_agg = _dd(lambda: {'un': 0, 'sh': 0, 'amt': 0})
    for dt, ob, cnt, amt in trend_7d:
        if ob == '未出库':
            trend_agg[dt]['un'] = cnt
        else:
            trend_agg[dt]['sh'] = cnt
        trend_agg[dt]['amt'] += amt
    prev_total = None
    for dt in sorted(trend_agg.keys(), reverse=True):
        d = trend_agg[dt]
        total = d['un'] + d['sh']
        arrow = ''
        if prev_total is not None:
            if total > prev_total:
                arrow = ' ↑'
            elif total < prev_total:
                arrow = ' ↓'
        parts.append('<tr style="border-bottom:1px solid #21262d;">')
        parts.append('<td style="padding:8px;color:#00d4ff;font-weight:600;">' + str(dt) + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#fff;">' + fmt(d['un']) + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#fff;">' + fmt(d['sh']) + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#fff;font-weight:600;">' + fmt(total) + arrow + '</td>')
        parts.append('<td style="padding:8px;text-align:right;color:#00e676;">¥' + fmt_amt(d['amt']) + '</td>')
        parts.append('</tr>')
        prev_total = total
    parts.append('</tbody></table>')

    # ===== Section VII: Audit rejected =====
    if reject_cnt and reject_cnt > 0:
        parts.append('<h2 style="color:#f85149;font-size:16px;margin-bottom:12px;">七、审核不通过（已发货但售后被拒）</h2>')
        parts.append('<div style="background:rgba(248,81,73,0.1);border:1px solid #f85149;border-radius:10px;padding:16px;margin-bottom:12px;">')
        parts.append('  <div style="display:flex;gap:24px;">')
        parts.append('    <span style="color:#fff;font-size:24px;font-weight:700;">' + fmt(reject_cnt) + '</span>')
        parts.append('    <span style="color:#f85149;font-size:14px;">ERP已付: ¥' + fmt_amt(reject_paid) + '</span>')
        parts.append('    <span style="color:#8b949e;font-size:14px;">财务净额: ¥' + fmt_amt(reject_net) + '</span>')
        parts.append('  </div>')
        parts.append('</div>')
        if reject_reasons:
            parts.append('<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:24px;">')
            parts.append('<thead><tr style="border-bottom:1px solid #30363d;">')
            parts.append('<th style="padding:8px;text-align:left;color:#8b949e;">原因</th>')
            parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">单数</th>')
            parts.append('<th style="padding:8px;text-align:right;color:#8b949e;">ERP已付(¥)</th>')
            parts.append('</tr></thead><tbody>')
            for reason, cnt, paid in reject_reasons:
                parts.append('<tr style="border-bottom:1px solid #21262d;">')
                parts.append('<td style="padding:8px;color:#c9d1d9;">' + str(reason) + '</td>')
                parts.append('<td style="padding:8px;text-align:right;color:#fff;">' + fmt(cnt) + '</td>')
                parts.append('<td style="padding:8px;text-align:right;color:#f85149;">' + fmt_amt(paid) + '</td>')
                parts.append('</tr>')
            parts.append('</tbody></table>')

    return chr(10).join(parts)




@cached_page('afterdashboard')
def render_aftersales_v2():
    import sqlite3
    DB_PATH = os.path.join(BASE, "erp_all.db")
    key = 'aftersales_v2'
    now = time.time()
    if key in _cache and now - _cache[key][0] < CACHE_TTL:
        return page('售后看板', 'aftersales_v2', _cache[key][1])
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        html = _build_aftersales_v2_html(conn)
        conn.close()
    except Exception as e:
        html = '<p style="color:#f85149;">数据加载失败: ' + str(e) + '</p>'
    _cache[key] = (now, html)
    return page('售后看板', 'aftersales_v2', html)


def _build_aftersales_v2_html(conn):
    from datetime import datetime
    from collections import defaultdict as _dd, Counter
    import json as _json
    now = datetime.now()
    shop = '榴愿时刻工厂店'

    def fmt(n):
        try: return '{:,.0f}'.format(n)
        except: return '0'
    def fmt_amt(n):
        try: return '{:,.0f}'.format(n)
        except: return '0'
    def pct(a, b):
        try: return '{:.1f}%'.format(a / b * 100) if b > 0 else '-'
        except: return '-'
    def top3(reasons_list):
        return ' | '.join(f'{r[0]}({r[1]})' for r in reasons_list[:3])

    quality_kw = ['重量不足', '腐烂', '变质', '发霉', '死包']
    def is_quality(r):
        return any(kw in str(r) for kw in quality_kw)

    # ===== DATA QUERIES =====
    as_total_cnt = conn.execute('SELECT COUNT(*) FROM after_sales WHERE shop_name = ?', (shop,)).fetchone()[0]
    unshipped_cnt, unshipped_amt = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ? AND outbound_status = ?',
        (shop, '未出库')).fetchone()
    shipped_cnt, shipped_amt = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ? AND outbound_status = ?',
        (shop, '已出库')).fetchone()
    total_refund = unshipped_amt + shipped_amt

    fin_aj_comp = conn.execute(
        'SELECT COALESCE(SUM(售后卖家赔付费),0) FROM finance_matched WHERE shop_name = ?',
        (shop,)).fetchone()[0]
    fin_platform_loss = conn.execute(
        'SELECT COALESCE(SUM(交易服务费支出),0) FROM finance_matched WHERE shop_name = ?',
        (shop,)).fetchone()[0]
    net_loss = total_refund - fin_aj_comp

    total_orders = conn.execute(
        "SELECT COUNT(*) FROM erp_all_orders WHERE shopName = ?",
        (shop,)).fetchone()[0]
    normal_cnt = total_orders - as_total_cnt
    normal_rate = pct(normal_cnt, total_orders) if total_orders > 0 else '-'

    unshipped_status = conn.execute(
        'SELECT service_status, COUNT(*) FROM after_sales WHERE shop_name = ? AND outbound_status = ? GROUP BY 1 ORDER BY 2 DESC',
        (shop, '未出库')).fetchall()
    shipped_status = conn.execute(
        'SELECT service_status, COUNT(*) FROM after_sales WHERE shop_name = ? AND outbound_status = ? GROUP BY 1 ORDER BY 2 DESC',
        (shop, '已出库')).fetchall()

    unshipped_reasons = conn.execute(
        'SELECT primary_reason, COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ? AND outbound_status = ? AND primary_reason != "" GROUP BY 1 ORDER BY 2 DESC',
        (shop, '未出库')).fetchall()
    shipped_reasons = conn.execute(
        'SELECT primary_reason, COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ? AND outbound_status = ? AND primary_reason != "" GROUP BY 1 ORDER BY 2 DESC',
        (shop, '已出库')).fetchall()

    daily_orders = {}
    for dt, cnt, amt in conn.execute(
        "SELECT SUBSTR(payTime,1,10), COUNT(*), SUM(CAST(COALESCE(realAmount,'0') AS REAL)) FROM erp_all_orders WHERE shopName = ? AND payTime IS NOT NULL GROUP BY 1",
        (shop,)).fetchall():
        daily_orders[dt] = {'count': cnt, 'amount': amt}

    daily_shipped = {}
    for dt, cnt, amt in conn.execute(
        "SELECT SUBSTR(consignTime,1,10), COUNT(*), SUM(CAST(COALESCE(realAmount,'0') AS REAL)) FROM erp_all_orders WHERE shopName = ? AND consignTime IS NOT NULL GROUP BY 1",
        (shop,)).fetchall():
        daily_shipped[dt] = {'count': cnt, 'amount': amt}

    daily_signed = _dd(int)
    for dt, cnt in conn.execute(
        "SELECT SUBSTR(consignTime,1,10), COUNT(*) FROM erp_all_orders WHERE shopName = ? AND consignTime IS NOT NULL AND traceStatusMsg LIKE '%已签收%' GROUP BY 1",
        (shop,)).fetchall():
        daily_signed[dt] = cnt

    daily_shipped_wh = _dd(lambda: _dd(lambda: {'count': 0, 'amount': 0}))
    daily_signed_wh = _dd(lambda: _dd(int))
    for wh, dt, cnt, amt in conn.execute(
        "SELECT warehouseName, SUBSTR(consignTime,1,10), COUNT(*), SUM(CAST(COALESCE(realAmount,'0') AS REAL)) FROM erp_all_orders WHERE shopName = ? AND consignTime IS NOT NULL GROUP BY 1, 2",
        (shop,)).fetchall():
        daily_shipped_wh[wh][dt] = {'count': cnt, 'amount': amt}
    for wh, dt, cnt in conn.execute(
        "SELECT warehouseName, SUBSTR(consignTime,1,10), COUNT(*) FROM erp_all_orders WHERE shopName = ? AND consignTime IS NOT NULL AND traceStatusMsg LIKE '%已签收%' GROUP BY 1, 2",
        (shop,)).fetchall():
        daily_signed_wh[wh][dt] = cnt

    tid_to_paydate = {}
    for tid, dt in conn.execute(
        "SELECT srcTids, SUBSTR(payTime,1,10) FROM erp_all_orders WHERE shopName = ? AND payTime IS NOT NULL",
        (shop,)).fetchall():
        tid_to_paydate[tid] = dt
    tid_to_shipdate = {}
    for tid, dt in conn.execute(
        "SELECT srcTids, SUBSTR(consignTime,1,10) FROM erp_all_orders WHERE shopName = ? AND consignTime IS NOT NULL AND consignTime != ''", (shop,)).fetchall():
        tid_to_shipdate[tid] = dt

    as_by_date = _dd(lambda: _dd(lambda: {'count': 0, 'amount': 0}))
    for row in conn.execute(
        "SELECT tid, outbound_status, COALESCE(refund_amount,0), SUBSTR(apply_time,1,10) FROM after_sales WHERE shop_name = ?",
        (shop,)).fetchall():
        tid, ob, amt, apply_dt = row
        pd = tid_to_paydate.get(tid) or apply_dt
        if pd is not None:
            as_by_date[pd][ob]['count'] += 1
            as_by_date[pd][ob]['amount'] += amt

    as_reasons_temp = _dd(int)
    as_amounts_temp = _dd(float)
    for row in conn.execute(
        "SELECT tid, outbound_status, primary_reason, COALESCE(refund_amount,0), SUBSTR(apply_time,1,10) FROM after_sales WHERE shop_name = ? AND primary_reason != ''",
        (shop,)).fetchall():
        tid, ob, reason, amt, apply_dt = row
        pd = tid_to_paydate.get(tid) or apply_dt
        if pd is not None:
            as_reasons_temp[(pd, ob, reason)] += 1
            as_amounts_temp[(pd, ob, reason)] += amt
    as_reasons_by_date = _dd(lambda: [])
    grouped = _dd(list)
    for (pd, ob, reason), cnt in as_reasons_temp.items():
        grouped[(pd, ob)].append((reason, cnt, as_amounts_temp[(pd, ob, reason)]))
    for key, items in grouped.items():
        items.sort(key=lambda x: -x[1])
        as_reasons_by_date[key] = items[:3]

    as_by_shipdate = _dd(lambda: _dd(lambda: {'count': 0, 'amount': 0}))
    as_count_by_shipdate_wh = _dd(lambda: _dd(lambda: {'count': 0, 'amount': 0}))
    as_reasons_by_shipdate = _dd(list)
    for row in conn.execute(
        "SELECT tid, outbound_status, primary_reason, refund_amount, warehouse_name FROM after_sales WHERE shop_name = ? AND outbound_status = '已出库'",
        (shop,)).fetchall():
        tid, ob, reason, amt, wh = row
        dt = tid_to_shipdate.get(tid)
        if dt is None:
            continue
        as_by_shipdate[dt][ob]['count'] += 1
        as_by_shipdate[dt][ob]['amount'] += (amt or 0)
        if reason:
            as_reasons_by_shipdate[dt].append((reason, amt or 0))
        if wh:
            as_count_by_shipdate_wh[wh][dt]['count'] += 1
            as_count_by_shipdate_wh[wh][dt]['amount'] += (amt or 0)
    as_reasons_shipdate = _dd(lambda: [])
    for dt, entries in as_reasons_by_shipdate.items():
        rc = Counter()
        ra = _dd(float)
        for reason, amount in entries:
            rc[reason] += 1
            ra[reason] += amount
        top3r = sorted(rc.keys(), key=lambda r: -rc[r])[:3]
        as_reasons_shipdate[dt] = [(r, rc[r], ra[r]) for r in top3r]
    as_reasons_shipdate_wh = _dd(lambda: _dd(list))
    as_reasons_wh_raw = _dd(lambda: _dd(list))
    for row in conn.execute(
        "SELECT tid, warehouse_name, primary_reason, refund_amount FROM after_sales WHERE shop_name = ? AND outbound_status = '已出库' AND warehouse_name != '' AND primary_reason != ''",
        (shop,)).fetchall():
        tid, wh, reason, amt = row
        dt = tid_to_shipdate.get(tid)
        if dt is None:
            continue
        as_reasons_wh_raw[wh][dt].append((reason, amt or 0))
    for wh, dates in as_reasons_wh_raw.items():
        for dt, entries in dates.items():
            rc = Counter()
            ra = _dd(float)
            for reason, amount in entries:
                rc[reason] += 1
                ra[reason] += amount
            top3r = sorted(rc.keys(), key=lambda r: -rc[r])[:3]
            as_reasons_shipdate_wh[wh][dt] = [(r, rc[r], ra[r]) for r in top3r]

    # ===== NEW QUERIES =====
    # SKU after-sales rate
    sku_as_cnt = _dd(int)
    sku_to_spu = {}
    for spu_name, sku_name, cnt in conn.execute(
        "SELECT oli.spu_name, oli.sku_name, COUNT(*) FROM after_sales a JOIN order_line_items oli ON a.tid = oli.order_id WHERE a.shop_name = ? AND oli.sku_name IS NOT NULL GROUP BY 1, 2 ORDER BY 3 DESC",
        (shop,)).fetchall():
        sku_as_cnt[sku_name] += cnt
        if sku_name not in sku_to_spu:
            sku_to_spu[sku_name] = spu_name
    sku_order_cnt = {}
    for spu, sku, cnt in conn.execute(
        "SELECT spu_name, sku_name, COUNT(DISTINCT order_id) FROM order_line_items WHERE shop_name = ? GROUP BY 1, 2",
        (shop,)).fetchall():
        sku_order_cnt[sku] = cnt
        if sku not in sku_to_spu:
            sku_to_spu[sku] = spu
    sku_data = []
    for sku in sorted(sku_as_cnt.keys(), key=lambda s: -sku_as_cnt[s]):
        oc = sku_order_cnt.get(sku, 0)
        ac = sku_as_cnt[sku]
        spu = sku_to_spu.get(sku, "")
        sku_data.append((spu, sku, oc, ac))

    sku_top_reasons = _dd(lambda: _dd(int))
    for sku_name, reason, cnt in conn.execute(
        "SELECT oli.sku_name, a.primary_reason, COUNT(*) FROM after_sales a JOIN order_line_items oli ON a.tid = oli.order_id WHERE a.shop_name = ? AND a.primary_reason != '' AND oli.sku_name IS NOT NULL GROUP BY 1, 2 ORDER BY 3 DESC",
        (shop,)).fetchall():
        sku_top_reasons[sku_name][reason] += cnt
        if sku_name not in sku_to_spu:
            sku_to_spu[sku_name] = sku_to_spu.get(sku_name, "")
    sku_top3 = {}
    for sku, reasons in sku_top_reasons.items():
        sku_top3[sku] = sorted(reasons.items(), key=lambda x: -x[1])[:3]

    # Logistics comparison
    logistics_data = []
    for logi in conn.execute(
        "SELECT DISTINCT logisticsName FROM erp_all_orders WHERE shopName = ? AND logisticsName != '' AND logisticsName IS NOT NULL",
        (shop,)).fetchall():
        logi_name = logi[0]
        order_cnt = conn.execute(
            "SELECT COUNT(*) FROM erp_all_orders WHERE shopName = ? AND logisticsName = ?",
            (shop, logi_name)).fetchone()[0]
        as_cnt = conn.execute(
            "SELECT COUNT(DISTINCT a.service_no) FROM after_sales a JOIN erp_all_orders o ON a.tid = o.srcTids WHERE o.logisticsName = ? AND a.shop_name = ?",
            (logi_name, shop)).fetchone()[0]
        as_amt = conn.execute(
            "SELECT COALESCE(SUM(a.refund_amount),0) FROM after_sales a JOIN erp_all_orders o ON a.tid = o.srcTids WHERE o.logisticsName = ? AND a.shop_name = ?",
            (logi_name, shop)).fetchone()[0]
        signed_cnt = conn.execute(
            "SELECT COUNT(*) FROM erp_all_orders WHERE shopName = ? AND logisticsName = ? AND traceStatusMsg LIKE '%已签收%'",
            (shop, logi_name)).fetchone()[0]
        logistics_data.append((logi_name, order_cnt, as_cnt, as_amt, signed_cnt))

    # Warehouse comparison summary
    wh_summary = {}
    for wh_name in ['云南榴莲1号仓库', '金枕榴莲泰国直发AJ工厂']:
        wh_ship_total = sum(d['count'] for d in daily_shipped_wh.get(wh_name, {}).values())
        wh_ship_amt = sum(d['amount'] for d in daily_shipped_wh.get(wh_name, {}).values())
        wh_as_total = sum(d['count'] for d in as_count_by_shipdate_wh.get(wh_name, {}).values())
        wh_as_amt = sum(d['amount'] for d in as_count_by_shipdate_wh.get(wh_name, {}).values())
        wh_all_reasons = _dd(int)
        for row in conn.execute(
            "SELECT primary_reason FROM after_sales WHERE shop_name = ? AND warehouse_name = ? AND primary_reason != ''",
            (shop, wh_name)).fetchall():
            wh_all_reasons[row[0]] += 1
        wh_top3 = sorted(wh_all_reasons.items(), key=lambda x: -x[1])[:3]
        avg_dur = conn.execute(
            "SELECT AVG(duration_hours) FROM after_sales WHERE shop_name = ? AND warehouse_name = ? AND duration_hours IS NOT NULL",
            (shop, wh_name)).fetchone()[0]
        wh_summary[wh_name] = {
            'ship': wh_ship_total, 'ship_amt': wh_ship_amt,
            'as_cnt': wh_as_total, 'as_amt': wh_as_amt,
            'as_rate': pct(wh_as_total, wh_ship_total) if wh_ship_total > 0 else '-',
            'top3': wh_top3, 'avg_dur': avg_dur or 0
        }

    # Claim reconciliation
    claim_data = []
    for row in conn.execute(
        "SELECT service_no, apply_time, audit_time, duration_hours, refund_amount, service_status, finance_income, finance_net FROM after_sales WHERE shop_name = ? AND apply_time >= date('now', '-30 days') ORDER BY apply_time DESC",
        (shop,)).fetchall():
        claim_data.append(row)
    total_claims = len(claim_data)
    rejected_claims = sum(1 for r in claim_data if r[5] == '审核不通过')
    reject_rate = pct(rejected_claims, total_claims) if total_claims > 0 else '-'
    pending_48h = sum(1 for r in claim_data if r[5] not in ('审核不通过', '已关闭') and (r[2] is None))

    # 30-day trend
    # 30天售后趋势 - 按发货日期统计（当天发货订单产生的售后）
    trend_agg = _dd(lambda: {'un': 0, 'sh': 0, 'amt': 0})
    for row in conn.execute(
        "SELECT tid, outbound_status, COALESCE(refund_amount,0) FROM after_sales WHERE shop_name = ?",
        (shop,)).fetchall():
        tid, ob, amt = row
        dt = tid_to_shipdate.get(tid)  # 按发货日期统计
        if dt is None:
            continue
        if ob == '未出库': trend_agg[dt]['un'] += 1
        else: trend_agg[dt]['sh'] += 1
        trend_agg[dt]['amt'] += amt
    trend_dates = sorted(trend_agg.keys())
    trend_vals = [(trend_agg[d]['un'] + trend_agg[d]['sh']) for d in trend_dates]
    trend_mean = sum(trend_vals) / len(trend_vals) if trend_vals else 0
    trend_std = (sum((v - trend_mean)**2 for v in trend_vals) / len(trend_vals))**0.5 if len(trend_vals) > 1 else 0

    # Anomaly detection
    anomaly_dates = []
    for dt in trend_dates:
        val = trend_agg[dt]['un'] + trend_agg[dt]['sh']
        deviation = (val - trend_mean) / trend_std if trend_std > 0 else 0
        if deviation > 1.5:
            anomaly_dates.append((dt, val, round(trend_mean, 1), round(deviation, 1)))
    anomaly_dates.sort(key=lambda x: -x[1])

    # Rejected
    reject_cnt_total, reject_paid, reject_net = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(erp_paid),0), COALESCE(SUM(finance_net),0) FROM after_sales WHERE shop_name = ? AND service_status = ?',
        (shop, '审核不通过')).fetchone()
    reject_reasons = conn.execute(
        'SELECT primary_reason, COUNT(*), COALESCE(SUM(erp_paid),0) FROM after_sales WHERE shop_name = ? AND service_status = ? GROUP BY 1 ORDER BY 2 DESC',
        (shop, '审核不通过')).fetchall()

    # All reasons combined for bubble
    all_reasons = _dd(int)
    all_reasons_amt = _dd(float)
    for row in conn.execute(
        "SELECT primary_reason, COALESCE(refund_amount,0) FROM after_sales WHERE shop_name = ? AND primary_reason != ''",
        (shop,)).fetchall():
        all_reasons[row[0]] += 1
        all_reasons_amt[row[0]] += row[1]
    bubble_data = []
    for reason, cnt in sorted(all_reasons.items(), key=lambda x: -x[1]):
        bubble_data.append((reason, cnt, all_reasons_amt[reason], is_quality(reason)))

    # ===== HTML GENERATION =====
    parts = []
    parts.append('<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>')
    parts.append('<h1 style="color:#fff;font-size:22px;margin-bottom:6px;">售后专题报告</h1>')
    parts.append('<p style="color:#8b949e;margin-bottom:16px;font-size:13px;">数据截止：' + now.strftime("%Y年%-m月%-d日") + ' | 售后总计 {:,} 单，退款金额 ¥{:,.0f}</p>'.format(as_total_cnt, total_refund))

    # TOP: 6 KPI cards
    parts.append('<div style="display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:24px;">')
    kpis = [
        ('售后总申请', fmt(as_total_cnt), '¥' + fmt_amt(total_refund), '#3b82f6'),
        ('未出库退款', fmt(unshipped_cnt), '¥' + fmt_amt(unshipped_amt), '#f85149'),
        ('已出库退款', fmt(shipped_cnt), '¥' + fmt_amt(shipped_amt), '#f97316'),
        ('净损失', fmt(as_total_cnt), '¥' + fmt_amt(net_loss), '#dc2626'),
        ('平台费损失', '-', '¥' + fmt_amt(fin_platform_loss), '#a855f7'),
        ('正常完结率', normal_rate, fmt(normal_cnt) + '/' + fmt(total_orders), '#22c55e'),
    ]
    for label, val, sub, color in kpis:
        parts.append('<div style="background:#161b22;border:1px solid ' + color + ';border-radius:10px;padding:16px;text-align:center;">')
        parts.append('  <div style="color:#8b949e;font-size:12px;margin-bottom:4px;">' + label + '</div>')
        parts.append('  <div style="color:#fff;font-size:28px;font-weight:700;">' + val + '</div>')
        parts.append('  <div style="color:' + color + ';font-size:13px;">' + sub + '</div>')
        parts.append('</div>')
    parts.append('</div>')

    # 一、订单流程漏斗
    funnel_json = _json.dumps([
        {"name": "总订单", "value": max(total_orders, 1)},
        {"name": "未发货退款", "value": max(unshipped_cnt, 1)},
        {"name": "已发货退款", "value": max(shipped_cnt, 1)},
        {"name": "正常完结", "value": max(normal_cnt, 1)},
    ], ensure_ascii=False)
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">一、订单流程漏斗</h2>')
    parts.append('<div id="as_funnel" style="width:100%;height:320px;background:#161b22;border:1px solid #30363d;border-radius:10px;margin-bottom:16px;"></div>')
    parts.append('<script>(function(){')
    parts.append('var c=echarts.init(document.getElementById("as_funnel"));')
    parts.append('c.setOption({backgroundColor:"transparent",tooltip:{trigger:"item",formatter:"{b}<br/>{c} 单"},series:[{type:"funnel",left:"10%",top:20,bottom:20,width:"80%",min:0,max:' + str(total_orders) + ',minSize:"0%",maxSize:"100%",sort:"descending",gap:4,label:{show:true,color:"#c9d1d9",fontSize:13},itemStyle:{borderColor:"#0d1117",borderWidth:1},data:' + funnel_json + '}]});')
    parts.append('window.addEventListener("resize",function(){c.resize();});')
    parts.append('})();</script>')
    # Funnel table
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:13px;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;"><th style="padding:8px;text-align:left;color:#8b949e;">阶段</th><th style="padding:8px;text-align:right;color:#8b949e;">单数</th><th style="padding:8px;text-align:right;color:#8b949e;">退款数</th><th style="padding:8px;text-align:right;color:#8b949e;">退款率</th><th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th><th style="padding:8px;text-align:right;color:#8b949e;">净损失</th></tr></thead><tbody>')
    parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#00d4ff;">总订单</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(total_orders) + '</td><td style="padding:8px;text-align:right;color:#8b949e;">-</td><td style="padding:8px;text-align:right;color:#8b949e;">-</td><td style="padding:8px;text-align:right;color:#8b949e;">-</td><td style="padding:8px;text-align:right;color:#8b949e;">-</td></tr>')
    parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#f85149;">未发货退款</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(total_orders) + '</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(unshipped_cnt) + '</td><td style="padding:8px;text-align:right;color:#f97316;">' + pct(unshipped_cnt, total_orders) + '</td><td style="padding:8px;text-align:right;color:#f85149;">¥' + fmt_amt(unshipped_amt) + '</td><td style="padding:8px;text-align:right;color:#22c55e;">≈0</td></tr>')
    parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#f97316;">已发货退款</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(total_orders) + '</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(shipped_cnt) + '</td><td style="padding:8px;text-align:right;color:#f97316;">' + pct(shipped_cnt, total_orders) + '</td><td style="padding:8px;text-align:right;color:#f85149;">¥' + fmt_amt(shipped_amt) + '</td><td style="padding:8px;text-align:right;color:#f85149;">¥' + fmt_amt(shipped_amt) + '</td></tr>')
    parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#22c55e;">正常完结</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(total_orders) + '</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(normal_cnt) + '</td><td style="padding:8px;text-align:right;color:#22c55e;">' + normal_rate + '</td><td style="padding:8px;text-align:right;color:#8b949e;">-</td><td style="padding:8px;text-align:right;color:#8b949e;">-</td></tr>')
    parts.append('</tbody></table></div>')

    # 二、各规格售后率
    sku_as_rate_json = _json.dumps([
        {"name": s[1], "value": round(s[3]/s[2]*100, 1) if s[2] > 0 else 0, "spu": s[0]}
        for s in sorted(sku_data, key=lambda x: -(x[3]/x[2]*100 if x[2]>0 else 0))[:10]
    ], ensure_ascii=False)
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">二、各规格售后率 TOP 10</h2>')
    parts.append('<div id="as_sku_bar" style="width:100%;height:360px;background:#161b22;border:1px solid #30363d;border-radius:10px;margin-bottom:16px;"></div>')
    parts.append('<script>(function(){')
    parts.append('var c=echarts.init(document.getElementById("as_sku_bar"));')
    parts.append('c.setOption({backgroundColor:"transparent",tooltip:{trigger:"axis",formatter:function(p){var d=sku_as_rate_json[p[0].dataIndex];return d.spu+"<br/>"+d.name+"<br/>售后率: "+d.value+"%";}},grid:{left:"20%",right:"10%",top:"5%",bottom:"5%"},xAxis:{type:"value",axisLabel:{color:"#8b949e",formatter:"{value}%"},splitLine:{lineStyle:{color:"#21262d"}}},yAxis:{type:"category",data:' + sku_as_rate_json + '.map(function(x){return x.name}),axisLabel:{color:"#c9d1d9",fontSize:11}},series:[{type:"bar",data:' + sku_as_rate_json + '.map(function(x){return x.value}),itemStyle:{color:function(p){var colors=["#f85149","#f97316","#eab308","#22c55e","#00d4ff"];return colors[Math.floor(p.value/2)%colors.length];}},barWidth:20,label:{show:true,position:"right",color:"#c9d1d9",formatter:"{c}%"},animationDuration:1000}]});')
    parts.append('window.addEventListener("resize",function(){c.resize();});')
    parts.append('})();</script>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:12px;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;"><th style="padding:8px;text-align:left;color:#8b949e;">品名</th><th style="padding:8px;text-align:left;color:#8b949e;">规格</th><th style="padding:8px;text-align:right;color:#8b949e;">总订单</th><th style="padding:8px;text-align:right;color:#8b949e;">售后数</th><th style="padding:8px;text-align:right;color:#8b949e;">售后率</th><th style="padding:8px;text-align:left;color:#8b949e;">Top理由</th></tr></thead><tbody>')
    for spu, sku, oc, ac in sku_data[:20]:
        tr3 = ' | '.join(r[0] + '(' + str(r[1]) + ')' for r in sku_top3.get(sku, [])[:3])
        parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:6px;color:#c9d1d9;">' + spu + '</td><td style="padding:6px;color:#c9d1d9;">' + sku + '</td><td style="padding:6px;text-align:right;color:#fff;">' + fmt(oc) + '</td><td style="padding:6px;text-align:right;color:#fff;">' + fmt(ac) + '</td><td style="padding:6px;text-align:right;color:#f97316;">' + pct(ac, oc) + '</td><td style="padding:6px;color:#8b949e;font-size:11px;">' + tr3 + '</td></tr>')
    parts.append('</tbody></table></div>')

    # 三、物流对比
    logi_radar_json = _json.dumps([{
        "name": r[0],
        "value": [
            round(r[2]/r[1]*100, 2) if r[1]>0 else 0,
            round(r[4]/r[1]*100, 1) if r[1]>0 else 0,
            r[2],
            round(r[3], 0)
        ]
    } for r in logistics_data], ensure_ascii=False)
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">三、物流对比</h2>')
    parts.append('<div id="as_logi" style="width:100%;height:350px;background:#161b22;border:1px solid #30363d;border-radius:10px;margin-bottom:16px;"></div>')
    parts.append('<script>(function(){')
    parts.append('var c=echarts.init(document.getElementById("as_logi"));')
    parts.append('c.setOption({backgroundColor:"transparent",tooltip:{},legend:{data:' + logi_radar_json + '.map(function(x){return x.name}),bottom:0,textStyle:{color:"#c9d1d9"}},radar:{indicator:[{name:"售后率(%)",max:20},{name:"签收率(%)",max:100},{name:"售后数",max:500},{name:"退款金额",max:50000}],shape:"polygon",splitNumber:4,axisName:{color:"#8b949e"},splitLine:{lineStyle:{color:"#21262d"}},splitArea:{areaStyle:{color:["#161b22","#0d1117"]}},axisLine:{lineStyle:{color:"#30363d"}}},series:[{type:"radar",data:' + logi_radar_json + '}]});')
    parts.append('window.addEventListener("resize",function(){c.resize();});')
    parts.append('})();</script>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:13px;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;"><th style="padding:8px;text-align:left;color:#8b949e;">快递公司</th><th style="padding:8px;text-align:right;color:#8b949e;">总订单</th><th style="padding:8px;text-align:right;color:#8b949e;">售后数</th><th style="padding:8px;text-align:right;color:#8b949e;">售后率</th><th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th><th style="padding:8px;text-align:right;color:#8b949e;">签收率</th></tr></thead><tbody>')
    for logi_name, oc, ac, amt, sc in logistics_data:
        parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#c9d1d9;">' + logi_name + '</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(oc) + '</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(ac) + '</td><td style="padding:8px;text-align:right;color:#f97316;">' + pct(ac, oc) + '</td><td style="padding:8px;text-align:right;color:#f85149;">¥' + fmt_amt(amt) + '</td><td style="padding:8px;text-align:right;color:#3b82f6;">' + pct(sc, oc) + '</td></tr>')
    parts.append('</tbody></table></div>')

    # 四、仓库对比
    wh_bar_json = _json.dumps([
        {"name": k.replace('云南榴莲1号仓库','云南仓').replace('金枕榴莲泰国直发AJ工厂','AJ工厂'),
         "ship": v['ship'], "as_cnt": v['as_cnt'], "as_amt": v['as_amt']}
        for k, v in wh_summary.items()
    ], ensure_ascii=False)
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">四、仓库对比</h2>')
    parts.append('<div id="as_wh" style="width:100%;height:320px;background:#161b22;border:1px solid #30363d;border-radius:10px;margin-bottom:16px;"></div>')
    parts.append('<script>(function(){')
    parts.append('var c=echarts.init(document.getElementById("as_wh"));')
    parts.append('var d=' + wh_bar_json + ';')
    parts.append('c.setOption({backgroundColor:"transparent",tooltip:{trigger:"axis"},legend:{data:["发货量","售后数"],bottom:0,textStyle:{color:"#c9d1d9"}},xAxis:{type:"category",data:d.map(function(x){return x.name}),axisLabel:{color:"#c9d1d9"},axisTick:{alignWithLabel:true},axisLine:{lineStyle:{color:"#30363d"}}},yAxis:{type:"value",axisLabel:{color:"#8b949e"},splitLine:{lineStyle:{color:"#21262d"}}},series:[{name:"发货量",type:"bar",data:d.map(function(x){return x.ship}),itemStyle:{color:"#3b82f6"},label:{show:true,color:"#c9d1d9",position:"top"}},{name:"售后数",type:"bar",data:d.map(function(x){return x.as_cnt}),itemStyle:{color:"#f85149"},label:{show:true,color:"#c9d1d9",position:"top"}}]});')
    parts.append('window.addEventListener("resize",function(){c.resize();});')
    parts.append('})();</script>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:13px;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;"><th style="padding:8px;text-align:left;color:#8b949e;">指标</th><th style="padding:8px;text-align:right;color:#8b949e;">云南仓</th><th style="padding:8px;text-align:right;color:#8b949e;">AJ工厂</th></tr></thead><tbody>')
    wh_items = [
        ('发货量', 'ship', fmt), ('发货金额', 'ship_amt', lambda n: '¥' + fmt_amt(n)),
        ('售后数', 'as_cnt', fmt), ('售后金额', 'as_amt', lambda n: '¥' + fmt_amt(n)),
        ('售后率', 'as_rate', lambda n: n), ('平均处理时效', 'avg_dur', lambda n: '{:.1f}h'.format(n)),
    ]
    for label, key, fmtfn in wh_items:
        v1 = fmtfn(wh_summary.get('云南榴莲1号仓库', {}).get(key, 0))
        v2 = fmtfn(wh_summary.get('金枕榴莲泰国直发AJ工厂', {}).get(key, 0))
        parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#c9d1d9;">' + label + '</td><td style="padding:8px;text-align:right;color:#fff;">' + v1 + '</td><td style="padding:8px;text-align:right;color:#fff;">' + v2 + '</td></tr>')
    tr3_yn = ' | '.join(r[0] + '(' + str(r[1]) + ')' for r in wh_summary.get('云南榴莲1号仓库', {}).get('top3', [])[:3])
    tr3_aj = ' | '.join(r[0] + '(' + str(r[1]) + ')' for r in wh_summary.get('金枕榴莲泰国直发AJ工厂', {}).get('top3', [])[:3])
    parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#c9d1d9;">Top理由</td><td style="padding:8px;text-align:right;color:#8b949e;font-size:11px;">' + tr3_yn + '</td><td style="padding:8px;text-align:right;color:#8b949e;font-size:11px;">' + tr3_aj + '</td></tr>')
    parts.append('</tbody></table></div>')

    # 五、净损失拆解
    wf_json = _json.dumps([
        {"name": "总退款", "value": round(total_refund)},
        {"name": "AJ理赔", "value": round(-fin_aj_comp)},
        {"name": "净损失", "value": round(net_loss)}
    ], ensure_ascii=False)
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">五、净损失拆解</h2>')
    parts.append('<div id="as_waterfall" style="width:100%;height:300px;background:#161b22;border:1px solid #30363d;border-radius:10px;margin-bottom:16px;"></div>')
    parts.append('<script>(function(){')
    parts.append('var c=echarts.init(document.getElementById("as_waterfall"));')
    parts.append('var d=' + wf_json + ';')
    parts.append('c.setOption({backgroundColor:"transparent",tooltip:{trigger:"axis",formatter:function(p){return p.name+"<br/>¥"+p.value.toLocaleString()}},xAxis:{type:"category",data:d.map(function(x){return x.name}),axisLabel:{color:"#c9d1d9",fontSize:12},axisLine:{lineStyle:{color:"#30363d"}}},yAxis:{type:"value",axisLabel:{color:"#8b949e",formatter:"¥{value}"},splitLine:{lineStyle:{color:"#21262d"}}},series:[{type:"bar",data:d.map(function(x){return {value:x.value,itemStyle:{color:x.value<0?"#22c55e":(x.name=="净损失"?"#f85149":"#3b82f6")}}}),label:{show:true,color:"#c9d1d9",position:"top",formatter:"¥{c:,.0f}"},barWidth:80}]});')
    parts.append('window.addEventListener("resize",function(){c.resize();});')
    parts.append('})();</script>')
    parts.append('<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;margin-bottom:24px;">')
    parts.append('<div style="display:flex;justify-content:space-between;gap:16px;">')
    parts.append('<div><div style="color:#8b949e;font-size:12px;">总退款</div><div style="color:#fff;font-size:24px;font-weight:700;">¥' + fmt_amt(total_refund) + '</div></div>')
    parts.append('<div style="color:#8b949e;font-size:24px;">−</div>')
    parts.append('<div><div style="color:#8b949e;font-size:12px;">AJ 理赔到账</div><div style="color:#22c55e;font-size:24px;font-weight:700;">¥' + fmt_amt(fin_aj_comp) + '</div></div>')
    parts.append('<div style="color:#8b949e;font-size:24px;">=</div>')
    parts.append('<div><div style="color:#8b949e;font-size:12px;">净损失</div><div style="color:#f85149;font-size:24px;font-weight:700;">¥' + fmt_amt(net_loss) + '</div></div>')
    parts.append('</div></div>')

    # 六、理赔对账
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">六、理赔对账（近 30 天）</h2>')
    parts.append('<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:16px;">')
    parts.append('<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;text-align:center;"><div style="color:#8b949e;font-size:12px;">理赔笔数</div><div style="color:#fff;font-size:22px;font-weight:700;">' + fmt(total_claims) + '</div></div>')
    parts.append('<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;text-align:center;"><div style="color:#8b949e;font-size:12px;">驳回率</div><div style="color:#f97316;font-size:22px;font-weight:700;">' + reject_rate + '</div></div>')
    if pending_48h > 0:
        parts.append('<div style="background:rgba(248,81,73,0.1);border:1px solid #f85149;border-radius:10px;padding:12px;text-align:center;"><div style="color:#f85149;font-size:12px;">待处理 >48h</div><div style="color:#f85149;font-size:22px;font-weight:700;">' + fmt(pending_48h) + ' ⚠</div></div>')
    else:
        parts.append('<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px;text-align:center;"><div style="color:#8b949e;font-size:12px;">待处理 >48h</div><div style="color:#22c55e;font-size:22px;font-weight:700;">0</div></div>')
    parts.append('</div>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:12px;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;"><th style="padding:6px;text-align:left;color:#8b949e;">售后编号</th><th style="padding:6px;text-align:left;color:#8b949e;">申请时间</th><th style="padding:6px;text-align:left;color:#8b949e;">审核时间</th><th style="padding:6px;text-align:right;color:#8b949e;">时长(h)</th><th style="padding:6px;text-align:right;color:#8b949e;">退款金额</th><th style="padding:6px;text-align:left;color:#8b949e;">状态</th><th style="padding:6px;text-align:right;color:#8b949e;">财务净额</th></tr></thead><tbody>')
    for svc, at, aut, dur, amt, st, fi, fn in claim_data[:50]:
        sc = '#22c55e' if st == '已关闭' else ('#f85149' if '不通过' in str(st) else '#f97316')
        parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:4px;color:#8b949e;font-size:11px;">' + str(svc) + '</td><td style="padding:4px;color:#c9d1d9;font-size:11px;">' + str(at or '-') + '</td><td style="padding:4px;color:#c9d1d9;font-size:11px;">' + str(aut or '-') + '</td><td style="padding:4px;text-align:right;color:#fff;font-size:11px;">' + str(dur or '-') + '</td><td style="padding:4px;text-align:right;color:#f85149;font-size:11px;">¥' + fmt_amt(amt) + '</td><td style="padding:4px;color:' + sc + ';font-size:11px;">' + str(st) + '</td><td style="padding:4px;text-align:right;color:#8b949e;font-size:11px;">¥' + fmt_amt(fn or 0) + '</td></tr>')
    parts.append('</tbody></table></div>')

    # 七、30 天售后趋势
    trend_line_json = _json.dumps({
        "dates": [d[5:] for d in trend_dates],
        "un": [trend_agg[d]['un'] for d in trend_dates],
        "sh": [trend_agg[d]['sh'] for d in trend_dates],
        "total": [trend_agg[d]['un'] + trend_agg[d]['sh'] for d in trend_dates],
        "mean": round(trend_mean, 1),
        "threshold": round(trend_mean + 2 * trend_std, 1),
        "rate": [round((trend_agg[d]['un'] + trend_agg[d]['sh']) / max(daily_orders.get(d, {}).get('count', 1), 1) * 100, 1) for d in trend_dates]
    }, ensure_ascii=False)
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">七、30 天售后趋势</h2>')
    parts.append('<div id="as_trend" style="width:100%;height:380px;background:#161b22;border:1px solid #30363d;border-radius:10px;margin-bottom:16px;"></div>')
    parts.append('<script>(function(){')
    parts.append('var d=' + trend_line_json + ';')
    parts.append('var c=echarts.init(document.getElementById("as_trend"));')
    parts.append('c.setOption({backgroundColor:"transparent",tooltip:{trigger:"axis"},legend:{data:["未出库","已出库","售后率"],bottom:0,textStyle:{color:"#c9d1d9"}},grid:{left:"5%",right:"5%",top:"5%",bottom:"15%"},xAxis:{type:"category",data:d.dates,axisLabel:{color:"#8b949e",rotate:30},axisLine:{lineStyle:{color:"#30363d"}}},yAxis:[{type:"value",name:"单数",axisLabel:{color:"#8b949e"},splitLine:{lineStyle:{color:"#21262d"}},nameTextStyle:{color:"#8b949e"}},{type:"value",name:"售后率%",axisLabel:{color:"#8b949e",formatter:"{value}%"},splitLine:{show:false},nameTextStyle:{color:"#8b949e"}}],series:[{name:"未出库",type:"bar",stack:"a",data:d.un,itemStyle:{color:"#f97316"}},{name:"已出库",type:"bar",stack:"a",data:d.sh,itemStyle:{color:"#f85149"}},{name:"售后率",type:"line",yAxisIndex:1,data:d.rate,itemStyle:{color:"#a855f7"},smooth:true,symbol:"circle",symbolSize:6}],markLine:{data:[{yAxis:d.threshold,label:{formatter:"预警线: " + d.threshold}}],lineStyle:{color:"#f85149",type:"dashed"}}});')
    parts.append('window.addEventListener("resize",function(){c.resize();});')
    parts.append('})();</script>')

    # 八、退单理由分布
    bubble_json = _json.dumps([
        {"name": r[0], "value": r[1], "amount": round(r[2], 0), "quality": r[3]}
        for r in bubble_data if r[1] > 0
    ], ensure_ascii=False)
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">八、退单理由分布</h2>')
    parts.append('<div id="as_bubble" style="width:100%;height:350px;background:#161b22;border:1px solid #30363d;border-radius:10px;margin-bottom:16px;"></div>')
    parts.append('<script>(function(){')
    parts.append('var d=' + bubble_json + ';')
    parts.append('var c=echarts.init(document.getElementById("as_bubble"));')
    parts.append('c.setOption({backgroundColor:"transparent",tooltip:{formatter:function(p){return p.data.name+"<br/>单数: "+p.data.value+"<br/>金额: ¥"+p.data.amount.toLocaleString()}},grid:{left:"10%",right:"10%",top:"10%",bottom:"10%"},xAxis:{show:false},yAxis:{show:false},series:[{type:"scatter",data:d.map(function(x,i){return [i*60+50,50,x.value,x.quality?"质量相关":"其他",x.name]}),symbolSize:function(d){return Math.max(d[2]*3,12);},itemStyle:{color:function(p){return p.data[3]=="质量相关"?"#f85149":"#3b82f6"}},label:{show:true,formatter:function(p){return p.data[4]},color:"#c9d1d9",fontSize:10,position:"inside"}}]});')
    parts.append('window.addEventListener("resize",function(){c.resize();});')
    parts.append('})();</script>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:13px;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;"><th style="padding:8px;text-align:left;color:#8b949e;">退单理由</th><th style="padding:8px;text-align:right;color:#8b949e;">单数</th><th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th><th style="padding:8px;text-align:left;color:#8b949e;">标签</th></tr></thead><tbody>')
    for reason, cnt, amt, iq in bubble_data:
        col = '#f85149' if iq else '#3b82f6'
        tag = '质量相关' if iq else ''
        parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:' + col + ';">' + reason + '</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(cnt) + '</td><td style="padding:8px;text-align:right;color:#f85149;">¥' + fmt_amt(amt) + '</td><td style="padding:8px;color:#f85149;font-size:11px;">' + tag + '</td></tr>')
    parts.append('</tbody></table></div>')

    # 九、高频退款预警
    if anomaly_dates:
        parts.append('<h2 style="color:#f85149;font-size:16px;margin-bottom:12px;">九、高频退款预警</h2>')
        parts.append('<div style="background:rgba(248,81,73,0.05);border:1px solid #f85149;border-radius:10px;padding:12px;margin-bottom:16px;">')
        parts.append('<div style="color:#8b949e;font-size:12px;">7 天均值: ' + '{:.1f}'.format(trend_mean) + ' 单/天 | 标准差: ' + '{:.1f}'.format(trend_std) + ' | 预警阈值: ' + '{:.1f}'.format(trend_mean + 2*trend_std) + '</div>')
        parts.append('</div>')
        parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:13px;">')
        parts.append('<thead><tr style="border-bottom:2px solid #30363d;"><th style="padding:8px;text-align:left;color:#8b949e;">日期</th><th style="padding:8px;text-align:right;color:#8b949e;">售后数</th><th style="padding:8px;text-align:right;color:#8b949e;">7天均值</th><th style="padding:8px;text-align:right;color:#8b949e;">偏差</th><th style="padding:8px;text-align:left;color:#8b949e;">状态</th></tr></thead><tbody>')
        for dt, val, mu, dev in anomaly_dates[:10]:
            tag = '异常' if float(dev) > 2 else '偏高'
            parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#00d4ff;">' + dt + '</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(val) + '</td><td style="padding:8px;text-align:right;color:#8b949e;">' + str(mu) + '</td><td style="padding:8px;text-align:right;color:#f97316;">+' + str(dev) + 'σ</td><td style="padding:8px;">' + tag + '</td></tr>')
        parts.append('</tbody></table></div>')
    else:
        parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">九、高频退款预警</h2>')
        parts.append('<div style="background:rgba(34,197,94,0.1);border:1px solid #22c55e;border-radius:10px;padding:16px;margin-bottom:24px;"><div style="color:#22c55e;font-size:14px;">当前无异常 | 7天均值: ' + '{:.1f}'.format(trend_mean) + ' 单/天 | 标准差: ' + '{:.1f}'.format(trend_std) + '</div></div>')

    # 十、审核不通过
    if reject_cnt_total and reject_cnt_total > 0:
        parts.append('<h2 style="color:#f85149;font-size:16px;margin-bottom:12px;">十、审核不通过</h2>')
        parts.append('<div style="background:rgba(248,81,73,0.1);border:1px solid #f85149;border-radius:10px;padding:16px;margin-bottom:12px;">')
        parts.append('  <div style="display:flex;gap:24px;">')
        parts.append('    <span style="color:#fff;font-size:24px;font-weight:700;">' + fmt(reject_cnt_total) + '</span>')
        parts.append('    <span style="color:#f85149;font-size:14px;">ERP已付: ¥' + fmt_amt(reject_paid) + '</span>')
        parts.append('    <span style="color:#8b949e;font-size:14px;">财务净额: ¥' + fmt_amt(reject_net) + '</span>')
        parts.append('  </div></div>')
        if reject_reasons:
            parts.append('<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:24px;">')
            parts.append('<thead><tr style="border-bottom:1px solid #30363d;"><th style="padding:8px;text-align:left;color:#8b949e;">原因</th><th style="padding:8px;text-align:right;color:#8b949e;">单数</th><th style="padding:8px;text-align:right;color:#8b949e;">ERP已付</th></tr></thead><tbody>')
            for reason, cnt, paid in reject_reasons:
                parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#c9d1d9;">' + str(reason) + '</td><td style="padding:8px;text-align:right;color:#fff;">' + fmt(cnt) + '</td><td style="padding:8px;text-align:right;color:#f85149;">¥' + fmt_amt(paid) + '</td></tr>')
            parts.append('</tbody></table>')

    # 十一、未出库按下单日期
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">十一、未出库售后 — 按下单日期</h2>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;"><th style="padding:8px;text-align:left;color:#8b949e;">日期</th><th style="padding:8px;text-align:right;color:#8b949e;">订单数</th><th style="padding:8px;text-align:right;color:#8b949e;">未出库售后</th><th style="padding:8px;text-align:right;color:#8b949e;">售后率</th><th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th><th style="padding:8px;text-align:left;color:#8b949e;">Top理由</th></tr></thead><tbody>')
    all_dt_ordered = sorted(set(list(daily_orders.keys()) + list(as_by_date.keys())), reverse=True)
    max_as_un = max([as_by_date[dt]['未出库']['count'] for dt in as_by_date if '未出库' in as_by_date[dt]], default=1)
    for dt in all_dt_ordered:
        oi = daily_orders.get(dt, {'count': 0, 'amount': 0})
        ai = as_by_date[dt].get('未出库', {'count': 0, 'amount': 0})
        if ai['count'] == 0 and oi['count'] == 0:
            continue
        r3 = top3(as_reasons_by_date.get((dt, '未出库'), []))
        parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:6px;color:#00d4ff;">' + dt + '</td><td style="padding:6px;text-align:right;color:#fff;">' + fmt(oi['count']) + '</td><td style="padding:6px;text-align:right;color:#fff;">' + fmt(ai['count']) + '</td><td style="padding:6px;text-align:right;color:#f97316;">' + pct(ai['count'], oi['count']) + '</td><td style="padding:6px;text-align:right;color:#f85149;">¥' + fmt_amt(ai['amount']) + '</td><td style="padding:6px;color:#8b949e;font-size:11px;">' + r3 + '</td></tr>')
    parts.append('</tbody></table></div>')

    # 十二、已出库按发货日期
    parts.append('<h2 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">十二、已出库售后 — 按发货日期</h2>')
    parts.append('<div style="overflow-x:auto;margin-bottom:24px;"><table style="width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap;">')
    parts.append('<thead><tr style="border-bottom:2px solid #30363d;"><th style="padding:8px;text-align:left;color:#8b949e;">日期</th><th style="padding:8px;text-align:right;color:#8b949e;">发货数</th><th style="padding:8px;text-align:right;color:#8b949e;">已签收</th><th style="padding:8px;text-align:right;color:#8b949e;">已出库售后</th><th style="padding:8px;text-align:right;color:#8b949e;">售后率</th><th style="padding:8px;text-align:right;color:#8b949e;">退款金额</th><th style="padding:8px;text-align:left;color:#8b949e;">Top理由</th></tr></thead><tbody>')
    all_ship_dt = sorted(set(list(daily_shipped.keys()) + list(as_by_shipdate.keys())), reverse=True)
    max_as_sh = max([as_by_shipdate[dt]['已出库']['count'] for dt in as_by_shipdate if '已出库' in as_by_shipdate[dt]], default=1)
    for dt in all_ship_dt:
        si = daily_shipped.get(dt, {'count': 0, 'amount': 0})
        ai2 = as_by_shipdate[dt].get('已出库', {'count': 0, 'amount': 0})
        sc2 = daily_signed.get(dt, 0)
        if ai2['count'] == 0 and si['count'] == 0:
            continue
        r32 = ' | '.join(str(r[0]) + '(' + str(r[1]) + '单)' for r in as_reasons_shipdate.get(dt, [])[:3])
        parts.append('<tr style="border-bottom:1px solid #21262d;"><td style="padding:6px;color:#00d4ff;">' + dt + '</td><td style="padding:6px;text-align:right;color:#fff;">' + fmt(si['count']) + '</td><td style="padding:6px;text-align:right;color:#3b82f6;">' + fmt(sc2) + '</td><td style="padding:6px;text-align:right;color:#fff;">' + fmt(ai2['count']) + '</td><td style="padding:6px;text-align:right;color:#f97316;">' + pct(ai2['count'], si['count']) + '</td><td style="padding:6px;text-align:right;color:#f85149;">¥' + fmt_amt(ai2['amount']) + '</td><td style="padding:6px;color:#8b949e;font-size:11px;">' + r32 + '</td></tr>')
    parts.append('</tbody></table></div>')

    return chr(10).join(parts)




def render_logistics():
    """物流轨迹详细报告"""
    import sqlite3
    from datetime import datetime
    DB_PATH = os.path.join(BASE, "erp_all.db")
    conn = sqlite3.connect(DB_PATH, timeout=30.0)

    # 快递状态统计（按最新状态）
    stats = {}
    for row in conn.execute("""
        SELECT t.operate_desc, COUNT(DISTINCT t.logistics_no)
        FROM logistics_trace t
        INNER JOIN (
            SELECT logistics_no, MAX(operate_time) as max_time
            FROM logistics_trace GROUP BY logistics_no
        ) latest ON t.logistics_no = latest.logistics_no AND t.operate_time = latest.max_time
        GROUP BY t.operate_desc
    """).fetchall():
        stats[row[0]] = row[1]

    signed = stats.get("已签收", 0)
    in_transit = stats.get("运输中", 0)
    delivering = stats.get("派送中", 0)
    picked = stats.get("已揽件", 0)
    problem = stats.get("问题件", 0)
    rejected = stats.get("拒收", 0)
    other = stats.get("其它", 0)
    pending = stats.get("待揽件", 0)
    total = sum(stats.values())

    # 超时预警列表
    timeout_list = []
    for row in conn.execute("""
        SELECT l.logistics_no, MAX(l.operate_time), l.current_addr
        FROM logistics_trace l
        WHERE l.operate_desc = "已揽件"
        AND l.operate_time < datetime("now", "-72 hours")
        AND l.logistics_no NOT IN (
            SELECT DISTINCT logistics_no FROM logistics_trace WHERE operate_desc = "已签收"
        )
        GROUP BY l.logistics_no
        ORDER BY MAX(l.operate_time) ASC
        LIMIT 50
    """).fetchall():
        timeout_list.append(row)

    # 问题件详情
    problem_list = []
    for row in conn.execute("""
        SELECT t.logistics_no, MAX(t.operate_time), t.current_addr, t.trace_info
        FROM logistics_trace t
        WHERE t.operate_desc = "问题件"
        GROUP BY t.logistics_no
        ORDER BY MAX(t.operate_time) DESC
        LIMIT 20
    """).fetchall():
        problem_list.append(row)

    # 时效分布
    timing_stats = {}
    for row in conn.execute("""
        SELECT
            CASE
                WHEN hours < 24 THEN "0-24h"
                WHEN hours < 48 THEN "24-48h"
                WHEN hours < 72 THEN "48-72h"
                WHEN hours < 96 THEN "72-96h"
                ELSE "96h+"
            END,
            COUNT(*)
        FROM (
            SELECT l1.logistics_no,
                   (julianday(l2.operate_time) - julianday(l1.operate_time)) * 24 as hours
            FROM logistics_trace l1
            JOIN logistics_trace l2 ON l1.logistics_no = l2.logistics_no
            WHERE l1.operate_desc = "已揽件"
            AND l2.operate_desc = "已签收"
            AND l1.operate_time < l2.operate_time
        )
        GROUP BY 1
    """).fetchall():
        timing_stats[row[0]] = row[1]

    # 快递公司时效
    courier_stats = []
    for row in conn.execute("""
        SELECT l.logistics_name,
               ROUND(AVG((julianday(l2.operate_time) - julianday(l1.operate_time)) * 24), 1),
               COUNT(*)
        FROM logistics_trace l1
        JOIN logistics_trace l2 ON l1.logistics_no = l2.logistics_no
        JOIN (SELECT DISTINCT logistics_no, logistics_name FROM logistics_trace) l
             ON l1.logistics_no = l.logistics_no
        WHERE l1.operate_desc = "已揽件"
        AND l2.operate_desc = "已签收"
        AND l1.operate_time < l2.operate_time
        GROUP BY l.logistics_name
    """).fetchall():
        courier_stats.append(row)

    # 今日签收
    today_signed_list = []
    for row in conn.execute("""
        SELECT DISTINCT logistics_no, MAX(operate_time), current_addr
        FROM logistics_trace
        WHERE operate_desc = "已签收"
        AND operate_time >= date("now")
        GROUP BY logistics_no
        ORDER BY MAX(operate_time) DESC
        LIMIT 20
    """).fetchall():
        today_signed_list.append(row)

    conn.close()

    # 构建表格行
    timeout_rows = ""
    for r in timeout_list:
        try:
            hours = round((datetime.now() - datetime.fromisoformat(r[1])).total_seconds() / 3600, 1)
        except:
            hours = "N/A"
        timeout_rows += f"<tr><td style='padding:8px;color:#f85149;'>{r[0]}</td><td style='padding:8px;color:#d29922;'>{hours}h</td><td style='padding:8px;'>{r[2] or '-'}</td></tr>"

    problem_rows = ""
    for r in problem_list:
        info = (r[3] or "-")[:50]
        problem_rows += f"<tr><td style='padding:8px;color:#f97316;'>{r[0]}</td><td style='padding:8px;'>{(r[1] or '-')[:16]}</td><td style='padding:8px;'>{r[2] or '-'}</td><td style='padding:8px;font-size:12px;'>{info}</td></tr>"

    today_rows = ""
    for r in today_signed_list:
        time_str = (r[1] or "")[11:16] if len(r[1]) > 11 else r[1]
        today_rows += f"<tr><td style='padding:8px;color:#00e676;'>{r[0]}</td><td style='padding:8px;'>{time_str}</td><td style='padding:8px;'>{r[2] or '-'}</td></tr>"

    timing_html = ""
    for bucket in ["0-24h", "24-48h", "48-72h", "72-96h", "96h+"]:
        cnt = timing_stats.get(bucket, 0)
        total_t = sum(timing_stats.values()) if timing_stats else 1
        pct = round(cnt / total_t * 100, 1) if total_t > 0 else 0
        color = "#00e676" if bucket in ["0-24h", "24-48h"] else "#d29922" if bucket == "48-72h" else "#f85149"
        timing_html += f"<div style='background:#0d1117;border-radius:6px;padding:8px 12px;text-align:center;'><div style='color:#8b949e;font-size:11px;'>{bucket}</div><div style='color:{color};font-size:16px;font-weight:600;'>{cnt}</div><div style='color:#8b949e;font-size:10px;'>{pct}%</div></div>"

    courier_html = ""
    for name, avg_h, cnt in courier_stats:
        color = "#00e676" if avg_h < 60 else "#d29922" if avg_h < 80 else "#f85149"
        courier_html += f"<tr><td style='padding:8px;'>{name}</td><td style='padding:8px;color:{color};font-weight:600;'>{avg_h}h</td><td style='padding:8px;'>{cnt}</td></tr>"

    html = f"""
<h2 style="color:#00d4ff;font-size:18px;margin-bottom:16px;">物流状态总览</h2>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px;">
  <div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;min-width:100px;text-align:center;">
    <div style="color:#8b949e;font-size:11px;">总快递</div>
    <div style="color:#fff;font-size:20px;font-weight:600;">{total}</div>
  </div>
  <div style="background:#161b22;border:1px solid #00e676;border-radius:8px;padding:16px;min-width:100px;text-align:center;">
    <div style="color:#8b949e;font-size:11px;">已签收</div>
    <div style="color:#00e676;font-size:20px;font-weight:600;">{signed}</div>
  </div>
  <div style="background:#161b22;border:1px solid #00d4ff;border-radius:8px;padding:16px;min-width:100px;text-align:center;">
    <div style="color:#8b949e;font-size:11px;">运输中</div>
    <div style="color:#00d4ff;font-size:20px;font-weight:600;">{in_transit}</div>
  </div>
  <div style="background:#161b22;border:1px solid #a855f7;border-radius:8px;padding:16px;min-width:100px;text-align:center;">
    <div style="color:#8b949e;font-size:11px;">派送中</div>
    <div style="color:#a855f7;font-size:20px;font-weight:600;">{delivering}</div>
  </div>
  <div style="background:#161b22;border:1px solid #3b82f6;border-radius:8px;padding:16px;min-width:100px;text-align:center;">
    <div style="color:#8b949e;font-size:11px;">已揽件</div>
    <div style="color:#3b82f6;font-size:20px;font-weight:600;">{picked}</div>
  </div>
  <div style="background:#161b22;border:1px solid #f97316;border-radius:8px;padding:16px;min-width:100px;text-align:center;">
    <div style="color:#8b949e;font-size:11px;">问题件</div>
    <div style="color:#f97316;font-size:20px;font-weight:600;">{problem}</div>
  </div>
  <div style="background:#161b22;border:1px solid #f85149;border-radius:8px;padding:16px;min-width:100px;text-align:center;">
    <div style="color:#8b949e;font-size:11px;">拒收</div>
    <div style="color:#f85149;font-size:20px;font-weight:600;">{rejected}</div>
  </div>
  <div style="background:#161b22;border:1px solid #6b7280;border-radius:8px;padding:16px;min-width:100px;text-align:center;">
    <div style="color:#8b949e;font-size:11px;">其它</div>
    <div style="color:#6b7280;font-size:20px;font-weight:600;">{other}</div>
  </div>
</div>

<h3 style="color:#f85149;font-size:16px;margin-bottom:12px;">超时预警（超过72小时未签收）</h3>
<p style="color:#8b949e;font-size:13px;margin-bottom:8px;">以下 {len(timeout_list)} 个快递已超过72小时，请立即跟进</p>
<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:24px;">
<table style="width:100%;border-collapse:collapse;font-size:13px;">
<thead><tr style="border-bottom:2px solid #30363d;">
<th style="padding:10px;text-align:left;color:#8b949e;">快递单号</th>
<th style="padding:10px;text-align:left;color:#8b949e;">超时时长</th>
<th style="padding:10px;text-align:left;color:#8b949e;">城市</th>
</tr></thead>
<tbody>{timeout_rows}</tbody></table></div>

<h3 style="color:#f97316;font-size:16px;margin-bottom:12px;">问题件详情</h3>
<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:24px;">
<table style="width:100%;border-collapse:collapse;font-size:13px;">
<thead><tr style="border-bottom:2px solid #30363d;">
<th style="padding:10px;text-align:left;color:#8b949e;">快递单号</th>
<th style="padding:10px;text-align:left;color:#8b949e;">时间</th>
<th style="padding:10px;text-align:left;color:#8b949e;">城市</th>
<th style="padding:10px;text-align:left;color:#8b949e;">问题详情</th>
</tr></thead>
<tbody>{problem_rows}</tbody></table></div>

<h3 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">时效分布</h3>
<div style="display:flex;gap:8px;margin-bottom:24px;">{timing_html}</div>

<h3 style="color:#00d4ff;font-size:16px;margin-bottom:12px;">快递公司时效对比</h3>
<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:24px;">
<table style="width:100%;border-collapse:collapse;font-size:13px;">
<thead><tr style="border-bottom:2px solid #30363d;">
<th style="padding:10px;text-align:left;color:#8b949e;">快递公司</th>
<th style="padding:10px;text-align:left;color:#8b949e;">平均时效</th>
<th style="padding:10px;text-align:left;color:#8b949e;">样本数</th>
</tr></thead>
<tbody>{courier_html}</tbody></table></div>

<h3 style="color:#00e676;font-size:16px;margin-bottom:12px;">今日签收</h3>
<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden;margin-bottom:24px;">
<table style="width:100%;border-collapse:collapse;font-size:13px;">
<thead><tr style="border-bottom:2px solid #30363d;">
<th style="padding:10px;text-align:left;color:#8b949e;">快递单号</th>
<th style="padding:10px;text-align:left;color:#8b949e;">签收时间</th>
<th style="padding:10px;text-align:left;color:#8b949e;">城市</th>
</tr></thead>
<tbody>{today_rows}</tbody></table></div>

<p style="color:#8b949e;font-size:12px;text-align:center;margin-top:24px;">数据更新时间: {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
"""

    return page("物流报告", "logistics", html)




#!/usr/bin/env python3
"""
New render_logistics function. This file IS the function body.
The splicer will read it and insert it into dashboard.py.
"""

#!/usr/bin/env python3
"""
New render_logistics function. This file IS the function body.
The splicer will read it and insert it into dashboard.py.
"""

def render_logistics():
    """物流轨迹详细报告"""
    import sqlite3
    import json
    from datetime import datetime, timedelta
    DB_PATH = os.path.join(BASE, "erp_all.db")
    conn = sqlite3.connect(DB_PATH, timeout=30.0)

    # 快递状态统计
    stats = {}
    for row in conn.execute("""
        SELECT t.operate_desc, COUNT(DISTINCT t.logistics_no)
        FROM logistics_trace t
        INNER JOIN (
            SELECT logistics_no, MAX(operate_time) as max_time
            FROM logistics_trace GROUP BY logistics_no
        ) latest ON t.logistics_no = latest.logistics_no AND t.operate_time = latest.max_time
        GROUP BY t.operate_desc
    """).fetchall():
        stats[row[0]] = row[1]

    signed = stats.get("已签收", 0)
    in_transit = stats.get("运输中", 0)
    delivering = stats.get("派送中", 0)
    picked = stats.get("已揽件", 0)
    problem = stats.get("问题件", 0)
    rejected = stats.get("拒收", 0)
    other = stats.get("其它", 0)
    pending = stats.get("待揽件", 0)
    total = sum(stats.values())

    # 物流节点时效：按trace_info识别关键阶段（揽收→曼谷→国内机场→转运→派送）
    pipeline_avg = {}
    pipeline_stuck = []
    stage_map = {}  # {logistics_no: {stage_name: datetime}}

    for row in conn.execute("""
        SELECT logistics_no, trace_info, MIN(operate_time) as first_time
        FROM logistics_trace
        GROUP BY logistics_no, trace_info
    """).fetchall():
        no, info, t = row
        if no not in stage_map:
            stage_map[no] = {}
        if any(k in info for k in ["尖竹汶", "揽收点", "已收取快件"]):
            stage_map[no].setdefault("揽收", t)
        if "曼谷" in info:
            stage_map[no].setdefault("曼谷机场", t)
        if any(k in info for k in ["机场转运中心", "深圳机场"]):
            stage_map[no].setdefault("国内机场", t)
        if "转运中心" in info and "机场" not in info:
            stage_map[no].setdefault("转运中心", t)
        if "正在派送" in info or "已派送成功" in info or "已签收" in info:
            stage_map[no].setdefault("派送签收", t)

    segments = [("揽收", "曼谷机场"), ("曼谷机场", "国内机场"), ("国内机场", "转运中心"), ("转运中心", "派送签收")]
    for seg_from, seg_to in segments:
        durations = []
        for no, stages in stage_map.items():
            if seg_from in stages and seg_to in stages:
                try:
                    d1 = datetime.fromisoformat(stages[seg_from])
                    d2 = datetime.fromisoformat(stages[seg_to])
                    h = (d2 - d1).total_seconds() / 3600
                    if h > 0:
                        durations.append(h)
                except Exception:
                    pass
        avg = round(sum(durations) / len(durations), 1) if durations else 0
        pipeline_avg[(seg_from, seg_to)] = avg

    # 当前停留过久的未签收订单
    signed_nos = set()
    for row in conn.execute("SELECT DISTINCT logistics_no FROM logistics_trace WHERE operate_desc = '已签收'"):
        signed_nos.add(row[0])

    for no, stages in stage_map.items():
        if no in signed_nos:
            continue
        latest_stage = None
        latest_time = None
        for s_name in ["派送签收", "转运中心", "国内机场", "曼谷机场", "揽收"]:
            if s_name in stages:
                latest_stage = s_name
                try:
                    latest_time = datetime.fromisoformat(stages[s_name])
                except Exception:
                    latest_time = None
                break
        if latest_stage and latest_time:
            stuck_hours = round((datetime.now() - latest_time).total_seconds() / 3600, 1)
            idx_map = {"揽收": 0, "曼谷机场": 1, "国内机场": 2, "转运中心": 3, "派送签收": 4}
            idx = idx_map.get(latest_stage, -1)
            next_stage = None
            if idx < len(segments):
                next_stage = segments[idx][1]
            if next_stage and stuck_hours > 12:
                pipeline_stuck.append((no, latest_stage, next_stage, stuck_hours))

    pipeline_stuck.sort(key=lambda x: x[3], reverse=True)
    pipeline_stuck = pipeline_stuck[:50]

    # 问题件
    problem_list = conn.execute("""
        SELECT t.logistics_no, MAX(t.operate_time), t.current_addr, t.trace_info
        FROM logistics_trace t
        WHERE t.operate_desc = '问题件'
        GROUP BY t.logistics_no
        ORDER BY MAX(t.operate_time) DESC
        LIMIT 20
    """).fetchall()

    # 时效分布
    timing_stats = {}
    for row in conn.execute("""
        SELECT
            CASE
                WHEN hours < 24 THEN '0-24h'
                WHEN hours < 48 THEN '24-48h'
                WHEN hours < 72 THEN '48-72h'
                WHEN hours < 96 THEN '72-96h'
                ELSE '96h+'
            END, COUNT(*)
        FROM (
            SELECT l1.logistics_no,
                   (julianday(l2.operate_time) - julianday(l1.operate_time)) * 24 as hours
            FROM logistics_trace l1
            JOIN logistics_trace l2 ON l1.logistics_no = l2.logistics_no
            WHERE l1.operate_desc = '已揽件'
            AND l2.operate_desc = '已签收'
            AND l1.operate_time < l2.operate_time
        ) GROUP BY 1
    """).fetchall():
        timing_stats[row[0]] = row[1]

    # 快递公司时效
    courier_stats = conn.execute("""
        SELECT l.logistics_name,
               ROUND(AVG((julianday(l2.operate_time) - julianday(l1.operate_time)) * 24), 1),
               COUNT(*)
        FROM logistics_trace l1
        JOIN logistics_trace l2 ON l1.logistics_no = l2.logistics_no
        JOIN (SELECT DISTINCT logistics_no, logistics_name FROM logistics_trace) l
             ON l1.logistics_no = l.logistics_no
        WHERE l1.operate_desc = '已揽件'
        AND l2.operate_desc = '已签收'
        AND l1.operate_time < l2.operate_time
        GROUP BY l.logistics_name
    """).fetchall()

    # 今日签收
    today_signed_list = conn.execute("""
        SELECT DISTINCT logistics_no, MAX(operate_time), current_addr
        FROM logistics_trace
        WHERE operate_desc = '已签收' AND operate_time >= date('now')
        GROUP BY logistics_no
        ORDER BY MAX(operate_time) DESC
        LIMIT 20
    """).fetchall()

    # 7天签收趋势
    daily_signed = {}
    for row in conn.execute("""
        SELECT SUBSTR(operate_time, 1, 10), COUNT(DISTINCT logistics_no)
        FROM logistics_trace
        WHERE operate_desc = '已签收'
        AND operate_time >= date('now', '-7 days')
        GROUP BY 1
    """).fetchall():
        daily_signed[row[0]] = row[1]
    days_7 = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        days_7.append((d[5:], daily_signed.get(d, 0)))
    day_labels = [d[0] for d in days_7]
    day_counts = [d[1] for d in days_7]

    conn.close()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""
<style>
  .log-stat {{ background:#161b22; border:1px solid #30363d; border-top:3px solid var(--accent); border-radius:10px; padding:16px; text-align:center; transition:all .2s ease; }}
  .log-stat:hover {{ transform:translateY(-2px); box-shadow:0 4px 16px rgba(0,0,0,0.3); }}
  .log-card {{ background:#161b22; border:1px solid #30363d; border-top:3px solid var(--accent); border-radius:10px; overflow:hidden; }}
  .log-card-h {{ padding:12px 16px; border-bottom:1px solid #30363d; }}
  .log-card-b {{ padding:0; }}
  .log-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }}
  @media(max-width:900px){{ .log-grid{{grid-template-columns:1fr; }} }}
</style>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script><h2 class="section-title">物流报告</h2>

<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px;">
  <div class="log-stat" style="--accent:#00d4ff;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">总快递数</div>
    <div style="color:#fff;font-size:28px;font-weight:700;">{format(total, ",")}</div>
  </div>
  <div class="log-stat" style="--accent:#00e676;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">已签收</div>
    <div style="color:#00e676;font-size:28px;font-weight:700;">{format(signed, ",")}</div>
    <div style="color:#8b949e;font-size:11px;">{round(signed/total*100,1) if total>0 else 0}%</div>
  </div>
  <div class="log-stat" style="--accent:#3b82f6;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">在途</div>
    <div style="color:#3b82f6;font-size:28px;font-weight:700;">{format(in_transit + delivering, ",")}</div>
    <div style="color:#8b949e;font-size:11px;">运输中 {in_transit} / 派送中 {delivering}</div>
  </div>

  <div class="log-stat" style="--accent:#f85149;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">问题件</div>
    <div style="color:#f85149;font-size:28px;font-weight:700;">{problem}</div>
  </div>
</div>

<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px;">
  <div class="log-stat" style="--accent:#00d4ff;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">揽收 -> 曼谷机场</div>
    <div style="color:#00d4ff;font-size:28px;font-weight:700;">{pipeline_avg.get(("揽收", "曼谷机场"), 0)}h</div>
    <div style="color:#8b949e;font-size:11px;">平均耗时</div>
  </div>
  <div class="log-stat" style="--accent:#a855f7;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">曼谷机场 -> 国内机场</div>
    <div style="color:#a855f7;font-size:28px;font-weight:700;">{pipeline_avg.get(("曼谷机场", "国内机场"), 0)}h</div>
    <div style="color:#8b949e;font-size:11px;">平均耗时</div>
  </div>
  <div class="log-stat" style="--accent:#3b82f6;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">国内机场 -> 转运中心</div>
    <div style="color:#3b82f6;font-size:28px;font-weight:700;">{pipeline_avg.get(("国内机场", "转运中心"), 0)}h</div>
    <div style="color:#8b949e;font-size:11px;">平均耗时</div>
  </div>
  <div class="log-stat" style="--accent:#00e676;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">转运中心 -> 派送签收</div>
    <div style="color:#00e676;font-size:28px;font-weight:700;">{pipeline_avg.get(("转运中心", "派送签收"), 0)}h</div>
    <div style="color:#8b949e;font-size:11px;">平均耗时</div>
  </div>
</div>

<div class="log-grid">"""

    if pipeline_stuck:
        stuck_rows = ""
        for no, stage_from, stage_to, stuck_h in pipeline_stuck:
            c = "#f85149" if stuck_h > 48 else "#f97316" if stuck_h > 24 else "#d29922"
            stuck_rows += f'<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#fff;font-family:monospace;font-size:12px;">{no}</td><td style="padding:8px;color:#8b949e;font-size:12px;">{stage_from}</td><td style="padding:8px;color:#8b949e;font-size:12px;">{stage_to}</td><td style="padding:8px;text-align:right;"><span style="color:{c};font-weight:700;">{stuck_h}h</span></td></tr>'

        html += f'''
  <div class="log-card" style="--accent:#f97316;">
    <div class="log-card-h" style="background:rgba(249,115,22,0.1);">
      <span style="color:#f97316;font-weight:700;font-size:14px;">&#9888; 滞留件</span>
      <span style="color:#8b949e;font-size:12px;margin-left:8px;">在某一阶段停留超12h ({len(pipeline_stuck)})</span>
    </div>
    <div class="log-card-b" style="max-height:320px;overflow-y:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="border-bottom:1px solid #30363d;">
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">快递单号</th>
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">当前阶段</th>
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">下一阶段</th>
      <th style="padding:8px;text-align:right;color:#8b949e;font-size:12px;">停留</th>
      </tr></thead><tbody>{stuck_rows}</tbody></table>
    </div>
  </div>'''

    if problem_list:
        problem_rows = ""
        for r in problem_list:
            info = (r[3] or "-")[:60]
            time_str = (r[1] or "")[:16]
            problem_rows += f'<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#fff;font-family:monospace;font-size:12px;">{r[0]}</td><td style="padding:8px;color:#8b949e;font-size:12px;">{time_str}</td><td style="padding:8px;color:#8b949e;font-size:12px;">{r[2] or "-"}</td><td style="padding:8px;font-size:12px;color:#f97316;">{info}</td></tr>'

        html += f'''
  <div class="log-card" style="--accent:#f97316;">
    <div class="log-card-h" style="background:rgba(249,115,22,0.1);">
      <span style="color:#f97316;font-weight:700;font-size:14px;">&#9888; 问题件</span>
      <span style="color:#8b949e;font-size:12px;margin-left:8px;">{len(problem_list)} 件</span>
    </div>
    <div class="log-card-b" style="max-height:320px;overflow-y:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="border-bottom:1px solid #30363d;">
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">快递单号</th>
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">时间</th>
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">城市</th>
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">问题</th>
      </tr></thead><tbody>{problem_rows}</tbody></table>
    </div>
  </div>'''

    # 时效分布
    timing_html = ""
    for bucket in ["0-24h", "24-48h", "48-72h", "72-96h", "96h+"]:
        cnt = timing_stats.get(bucket, 0)
        total_t = sum(timing_stats.values()) if timing_stats else 1
        pct = round(cnt / total_t * 100, 1) if total_t > 0 else 0
        color = "#00e676" if bucket in ["0-24h", "24-48h"] else "#d29922" if bucket == "48-72h" else "#f85149"
        timing_html += f'<div class="log-stat" style="--accent:{color};"><div style="color:#8b949e;font-size:11px;">{bucket}</div><div style="color:{color};font-size:15px;font-weight:700;">{cnt}</div><div style="color:#8b949e;font-size:10px;">{pct}%</div></div>'

    html += f'''
</div>

<h2 style="color:#00d4ff;font-size:16px;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid #30363d;">时效分布</h2>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:24px;">{timing_html}</div>

<div class="chart-grid" style="margin-bottom:16px;">
  <div class="chart-card" style="--accent:#00d4ff;">
    <h3>物流状态分布</h3>
    <canvas id="chart_log_status" style="max-height:200px;"></canvas>
  </div>
  <div class="chart-card" style="--accent:#00e676;">
    <h3>近7天签收趋势</h3>
    <canvas id="chart_log_signed" style="max-height:200px;"></canvas>
  </div>
</div>'''

    # 快递公司时效
    courier_html = ""
    max_h = max([r[1] for r in courier_stats]) if courier_stats else 1
    for name, avg_h, cnt in courier_stats:
        bar_w = int(avg_h / max_h * 80)
        color = "#00e676" if avg_h < 60 else "#d29922" if avg_h < 80 else "#f85149"
        courier_html += f'<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#c9d1d9;">{name}</td><td style="padding:8px;"><span style="display:inline-block;width:{bar_w}px;height:10px;background:{color};border-radius:2px;vertical-align:middle;"></span></td><td style="padding:8px;color:{color};font-weight:600;text-align:right;">{avg_h}h</td><td style="padding:8px;color:#8b949e;text-align:right;">{cnt}</td></tr>'

    html += f'''
<div class="log-card" style="--accent:#a855f7;margin-bottom:16px;">
  <div class="log-card-h">
    <span style="color:#a855f7;font-weight:600;font-size:14px;">快递公司时效对比</span>
  </div>
  <div class="log-card-b">
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
    <thead><tr style="border-bottom:1px solid #30363d;">
    <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">快递公司</th>
    <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">进度</th>
    <th style="padding:8px;text-align:right;color:#8b949e;font-size:12px;">平均时效</th>
    <th style="padding:8px;text-align:right;color:#8b949e;font-size:12px;">样本数</th>
    </tr></thead><tbody>{courier_html}</tbody></table>
  </div>
</div>'''

    # 今日签收
    today_rows = ""
    for r in today_signed_list:
        time_str = (r[1] or "")[11:16] if len(r[1]) > 11 else r[1]
        today_rows += f'<tr style="border-bottom:1px solid #21262d;"><td style="padding:6px 8px;color:#fff;font-family:monospace;font-size:12px;">{r[0]}</td><td style="padding:6px 8px;color:#8b949e;font-size:12px;">{time_str}</td><td style="padding:6px 8px;color:#8b949e;font-size:12px;">{r[2] or "-"}</td></tr>'

    html += f'''
<div class="log-card" style="--accent:#00e676;margin-bottom:24px;">
  <div class="log-card-h">
    <span style="color:#00e676;font-weight:600;font-size:14px;">今日签收 ({len(today_signed_list)})</span>
  </div>
  <div class="log-card-b" style="max-height:300px;overflow-y:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
    <thead><tr style="border-bottom:1px solid #30363d;">
    <th style="padding:6px 8px;text-align:left;color:#8b949e;font-size:12px;">快递单号</th>
    <th style="padding:6px 8px;text-align:left;color:#8b949e;font-size:12px;">时间</th>
    <th style="padding:6px 8px;text-align:left;color:#8b949e;font-size:12px;">城市</th>
    </tr></thead><tbody>{today_rows}</tbody></table>
  </div>
</div>

<p style="color:#484f58;font-size:12px;text-align:center;">数据更新: {now}</p>

<script>
Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#21262d';

var _logStatus = new Chart(document.getElementById('chart_log_status'), {{
  type: 'bar',
  data: {{
    labels: ['已签收', '运输中', '派送中', '问题件'],
    datasets: [{{
      data: [{signed}, {in_transit}, {delivering}, {problem}],
      backgroundColor: ['#00e676', '#00d4ff', '#3b82f6', '#f85149'],
      borderRadius: 6, barThickness: 40
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ grid: {{ display: false }} }}, y: {{ grid: {{ color: '#21262d' }}, beginAtZero: true }} }},
    animation: {{ duration: 800 }}
  }}
}});

var _logSigned = new Chart(document.getElementById('chart_log_signed'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(day_labels)},
    datasets: [{{
      data: {json.dumps(day_counts)},
      borderColor: '#00e676', backgroundColor: 'rgba(0,230,118,0.1)',
      fill: true, tension: 0.3, pointRadius: 4
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ grid: {{ display: false }} }}, y: {{ grid: {{ color: '#21262d' }}, beginAtZero: true }} }},
    animation: {{ duration: 800 }}
  }}
}});

</script>
'''

    return page("物流报告", "logistics", html)


# ===== 异常件页面 =====
_exception_cache = None

def render_exceptionorders():
    """异常件：物流问题件 + 售后异常，AI逐条分析"""
    global _exception_cache
    import sqlite3, json
    from datetime import datetime

    now = time.time()
    if _exception_cache and now - _exception_cache[0] < 300:
        return _exception_cache[1]

    DB_PATH = os.path.join(BASE, "erp_all.db")
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    shop = "榴愿时刻工厂店"

    lg_total = conn.execute("SELECT COUNT(*) FROM (SELECT DISTINCT logistics_no FROM logistics_trace WHERE shop_name=? AND operate_desc='问题件')", (shop,)).fetchone()[0]
    as_pending = conn.execute("SELECT COUNT(*) FROM after_sales WHERE shop_name=? AND service_status NOT IN ('完成','取消')", (shop,)).fetchone()[0]
    normal_cnt = conn.execute("SELECT COUNT(*) FROM exception_analysis WHERE classification='正常换单'").fetchone()[0]
    communicate_cnt = conn.execute("SELECT COUNT(*) FROM exception_analysis WHERE classification='需沟通'").fetchone()[0]
    unknown_cnt = conn.execute("SELECT COUNT(*) FROM exception_analysis WHERE classification='无法查询'").fetchone()[0]

    analysis_map = {}
    for row in conn.execute("SELECT exception_id, classification, ai_explanation, confidence FROM exception_analysis").fetchall():
        analysis_map[row[0]] = (row[1], row[2], row[3])

    # 物流问题件 LIMIT 200
    lg_orders = []
    for row in conn.execute("SELECT logistics_no, current_addr, operate_time FROM logistics_trace WHERE shop_name=? AND operate_desc='问题件' ORDER BY operate_time DESC LIMIT 200", (shop,)).fetchall():
        ao = conn.execute("SELECT srcTids, warehouseName, receiverCityName, receiverProvinceName, goodsAmount, realAmount, orderItemList, logisticsName, tradeTime, consignTime, tradeStatusFrontText, refundStatusText FROM erp_all_orders WHERE shopName=? AND logisticsNo=? LIMIT 1", (shop, row[0])).fetchone()
        if not ao: continue
        tid = ao[0] or ""
        eid = "lg_" + row[0]
        product = "-"
        try:
            for it in (json.loads(ao[6]) if ao[6] else [])[:1]:
                sku = it.get("skuName", "") or it.get("spuName", "")
                if sku and ";" in sku: sku = sku.split(";", 1)[1].strip()
                if sku: product = f"{sku} x{it.get('skuNum', it.get('quantity', '1'))}"; break
        except Exception: pass
        hours = 0
        if row[2]:
            try: hours = (datetime.now() - datetime.fromisoformat(row[2].replace(" ", "T"))).total_seconds() / 3600
            except: pass
        ai = analysis_map.get(eid)
        if ai: c, e, cf = ai
        elif (ao[11] or "") in ("全部退款", "退款成功"): c, e, cf = "正常换单", f"物流问题件（{row[1]}），订单已全部退款，货物自动拦截。", 0.80
        elif hours > 48: c, e, cf = "需沟通", f"物流问题件在{row[1]}停留超{int(hours)}小时，建议联系物流。", 0.70
        else: c, e, cf = "正常换单", f"物流问题件，位于{row[1]}，物流可能正在处理。", 0.60
        lg_orders.append({"eid": eid, "tid": tid, "logistics_no": row[0], "logistics_name": ao[7] or "", "current_addr": row[1] or "", "operate_time": row[2] or "", "warehouse": ao[1] or "", "receiver_city": ao[2] or "", "receiver_province": ao[3] or "", "product": product, "amount": ao[4] or 0, "trade_status": ao[10] or "", "refund_status": ao[11] or "", "consign_time": ao[9] or "", "trade_time": ao[8] or "", "hours_since": hours, "ai_class": c, "ai_exp": e, "ai_conf": cf, "source": "物流问题件"})

    # 售后异常 LIMIT 200
    as_orders = []
    for row in conn.execute("SELECT tid, service_no, service_status, primary_reason, secondary_reason, outbound_status, logistics_no, refund_amount, description, audit_opinion, apply_time FROM after_sales WHERE shop_name=? AND service_status NOT IN ('完成','取消') ORDER BY apply_time DESC LIMIT 200", (shop,)).fetchall():
        tid = row[0] or ""
        eid = "as_" + (row[1] or tid)
        ao = conn.execute("SELECT warehouseName, receiverCityName, tradeTime, goodsAmount, orderItemList, logisticsNo, logisticsName, consignTime, tradeStatusFrontText, refundStatusText FROM erp_all_orders WHERE shopName=? AND srcTids=? LIMIT 1", (shop, tid)).fetchone()
        product = "-"
        if ao and ao[4]:
            try:
                for it in (json.loads(ao[4]) if ao[4] else [])[:1]:
                    sku = it.get("skuName", "") or it.get("spuName", "")
                    if sku and ";" in sku: sku = sku.split(";", 1)[1].strip()
                    if sku: product = f"{sku} x{it.get('skuNum', it.get('quantity', '1'))}"; break
            except: pass
        ai = analysis_map.get(eid)
        if ai: c, e, cf = ai
        elif row[2] == "审核不通过": c, e, cf = "需沟通", f"售后审核不通过（{row[3]}），需主动联系协商。", 0.85
        elif row[2] in ("待客户反馈", "待商家审核"): c, e, cf = "需沟通", f"售后处理中（{row[2]}，{row[3]}），请关注时效。", 0.80
        elif row[2] == "待买家退货": c, e, cf = "需沟通", "已同意退货，等待买家寄回。", 0.75
        elif row[2] == "买家已退货": c, e, cf = "正常换单", "客户已退货，等待仓库验货。", 0.70
        else: c, e, cf = "需沟通", f"售后状态：{row[2]}（{row[3]}）。", 0.60
        as_orders.append({"eid": eid, "tid": tid, "service_no": row[1], "service_status": row[2], "primary_reason": row[3], "secondary_reason": row[4], "outbound_status": row[5], "logistics_no": row[6] or "", "refund_amount": row[7] or 0, "description": row[8] or "", "audit_opinion": row[9] or "", "apply_time": row[10] or "", "warehouse": ao[0] if ao else "", "receiver_city": ao[1] if ao else "", "product": product, "amount": ao[3] if ao else 0, "trade_time": ao[2] if ao else "", "consign_time": ao[7] if ao else "", "ao_logistics_no": ao[5] if ao else "", "logistics_name": ao[6] if ao else "", "trade_status": ao[8] if ao else "", "refund_status": ao[9] if ao else "", "ai_class": c, "ai_exp": e, "ai_conf": cf, "source": "售后异常"})

    conn.close()
    total = lg_total + as_pending
    tc = {"正常换单": "#238636", "需沟通": "#d29922", "无法查询": "#f85149"}
    p = []
    p.append('<h1 style="color:#fff;font-size:22px;margin-bottom:6px;">异常件追踪</h1>')
    p.append(f'<p style="color:#8b949e;margin-bottom:16px;font-size:13px;">物流问题件 + 售后异常 | AI逐条分析 | {datetime.now().strftime("%m/%d %H:%M")}</p>')
    p.append('<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:24px;">')
    for l, v, cl in [("总异常", f"{total:,}", "#f85149"), ("物流问题件", f"{lg_total:,}", "#f97316"), ("售后异常", f"{as_pending:,}", "#a855f7"), ("需沟通", f"{communicate_cnt:,}", "#d29922"), ("正常换单", f"{normal_cnt:,}", "#238636")]:
        p.append(f'<div style="background:#161b22;border:1px solid {cl};border-radius:10px;padding:16px;text-align:center;"><div style="color:#8b949e;font-size:12px;">{l}</div><div style="color:{cl};font-size:32px;font-weight:700;">{v}</div></div>')
    p.append('</div>')
    tids = ["tab_all", "tab_communicate", "tab_normal", "tab_unknown"]
    tlab = [f"全部 ({total})", f"需沟通 ({communicate_cnt})", f"正常换单 ({normal_cnt})", f"无法查询 ({unknown_cnt})"]
    p.append('<style>.etb{padding:8px 16px;border:1px solid #30363d;background:#161b22;color:#8b949e;cursor:pointer;font-size:13px;border-radius:6px 6px 0 0}.etb.on{background:#1c2333;color:#fff;border-color:#00d4ff;border-bottom-color:#1c2333}.etb:hover{background:#21262d;color:#fff}.eg{max-height:0;overflow:hidden;transition:max-height .3s ease}.eg.on{max-height:600px}.etg{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;color:#fff}</style>')
    p.append('<div style="display:flex;gap:4px;">')
    for i, (t, l) in enumerate(zip(tids, tlab)): p.append(f'<button class="etb {"on" if i==0 else ""}" onclick="ef(\x27{t}\x27)" id="{t}">{l}</button>')
    p.append('</div><div style="overflow-x:auto;border:1px solid #30363d;border-top:none;border-radius:0 0 10px 10px;"><table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr style="background:#161b22;color:#8b949e;text-align:left;">')
    for th in ["#", "AI分类", "来源", "品名规格", "订单号", "物流单号", "位置/原因", "金额", "异常时间", "详情"]:
        p.append(f'<th style="padding:10px 8px;border-bottom:1px solid #30363d;">{th}</th>')
    p.append('</tr></thead>')
    ae = lg_orders + as_orders
    co = {"需沟通": 0, "正常换单": 1, "无法查询": 2}
    ae.sort(key=lambda x: (co.get(x.get("ai_class", ""), 3), -x.get("hours_since", 0) if x.get("hours_since") else 0))
    for idx, o in enumerate(ae, 1):
        ac, ae2, af = o["ai_class"], o["ai_exp"], o["ai_conf"]
        tl = tc.get(ac, "#8b949e")
        sr, td2 = o["source"], o["tid"]
        lg = o.get("logistics_no", "") or o.get("ao_logistics_no", "")
        pr, am = o["product"][:40], o["amount"]
        wh = o["warehouse"]
        if sr == "物流问题件":
            lr = o.get("current_addr", "")[:30]
            et = o.get("operate_time", "")[:16]
            h = o.get("hours_since", 0)
            td3 = f'{et} ({int(h)}h前)' if h > 0 else et
        else:
            lr = o.get("primary_reason", "")[:30]
            td3 = o.get("apply_time", "")[:16]
        dc = "communicate" if ac == "需沟通" else ("normal" if ac == "正常换单" else "unknown")
        p.append(f'<tr class="er" data-c="{dc}" style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#484f58;">{idx}</td>')
        p.append(f'<td style="padding:8px;"><span class="etg" style="background:{tl};">{ac}</span></td>')
        p.append(f'<td style="padding:8px;color:#8b949e;font-size:11px;">{sr[:4]}</td>')
        p.append(f'<td style="padding:8px;color:#c9d1d9;">{pr}</td>')
        p.append(f'<td style="padding:8px;"><code style="background:#161b22;padding:1px 4px;border-radius:3px;font-size:11px;color:#58a6ff;">{td2[:16] if td2 else "-"}</code></td>')
        p.append(f'<td style="padding:8px;"><code style="background:#161b22;padding:1px 4px;border-radius:3px;font-size:11px;color:#8b949e;">{lg[:20] if lg else "-"}</code></td>')
        p.append(f'<td style="padding:8px;color:#8b949e;font-size:11px;">{lr}</td>')
        p.append(f'<td style="padding:8px;color:#00e676;font-size:12px;font-weight:600;">¥{float(am or 0):,.0f}</td>')
        p.append(f'<td style="padding:8px;color:#8b949e;font-size:11px;">{td3}</td>')
        p.append('<td style="padding:8px;text-align:center;"><button onclick="this.closest(\'tr\').nextElementSibling.classList.toggle(\'on\');this.textContent=this.textContent==\'▼\'?\'▶\':\'▼\'" style="background:none;border:none;color:#00d4ff;cursor:pointer;">▶</button></td></tr>')
        # detail
        p.append('<tr class="eg" style="border-bottom:1px solid #21262d;"><td colspan="10" style="padding:0;"><div style="padding:12px 16px;background:#161b22;"><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;">')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">AI 分析</div><div style="color:#c9d1d9;font-size:13px;line-height:1.6;">{ae2}</div><div style="color:#484f58;font-size:11px;">置信度: {af:.0%}</div></div>')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">订单详情</div><div style="color:#c9d1d9;font-size:12px;line-height:1.8;">仓库: {wh}<br>订单: {o.get("trade_status","-")}<br>退款: {o.get("refund_status","-")}<br>下单: {o.get("trade_time","")[:16] if o.get("trade_time") else "-"}<br>发货: {o.get("consign_time","")[:16] if o.get("consign_time") else "-"}</div></div>')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">异常信息</div><div style="color:#c9d1d9;font-size:12px;line-height:1.8;">类型: {sr}<br>')
        if sr == "物流问题件":
            p.append(f'位置: {o.get("current_addr","-")}<br>物流: {o.get("logistics_name","-")}<br>收货地: {o.get("receiver_province","")} {o.get("receiver_city","")}<br>')
        else:
            p.append(f'售后单号: {o.get("service_no","-")}<br>状态: {o.get("service_status","-")}<br>原因: {o.get("primary_reason","-")}<br>')
            if o.get("description"): p.append(f'描述: {o["description"][:50]}<br>')
            if o.get("audit_opinion"): p.append(f'审核: {o["audit_opinion"]}<br>')
        p.append('</div></div></div></div></td></tr>')
    if not ae: p.append('<tr><td colspan="10" style="padding:40px;text-align:center;color:#484f58;">暂无异常件</td></tr>')
    p.append('</table></div>')
    p.append('<script>function ef(t){document.querySelectorAll(".etb").forEach(b=>b.classList.remove("on"));document.getElementById(t).classList.add("on");var f=t==="tab_all"?"all":t==="tab_communicate"?"communicate":t==="tab_normal"?"normal":"unknown";document.querySelectorAll(".er").forEach(r=>{if(f==="all"){r.style.display=""}else{r.style.display=r.getAttribute("data-c")===f?"":"none"}})}</script>')
    html = "\n".join(p)
    html = page("异常件追踪", "exceptionorders", html)
    _exception_cache = (now, html)
    return html

def render_replaceorder():
    """换单补单：已发货退单未签收"""
    import sqlite3
    import json
    from datetime import datetime

    DB_PATH = os.path.join(BASE, "erp_all.db")
    conn = sqlite3.connect(DB_PATH, timeout=30.0)

    orders = conn.execute("""
        SELECT
            f.srcTids,
            f.logisticsNo,
            f.goodsCount,
            f.orderItemList,
            t.operate_desc,
            f.shopName,
            f.tradeTime,
            f.refundAmount,
            f.paid,
            f.estimateConsignTime
        FROM erp_finished f
        INNER JOIN (
            SELECT logistics_no, MAX(operate_time) as max_time
            FROM logistics_trace
            GROUP BY logistics_no
        ) latest ON f.logisticsNo = latest.logistics_no
        INNER JOIN logistics_trace t
            ON f.logisticsNo = t.logistics_no
            AND t.operate_time = latest.max_time
        WHERE f.refundStatusText = '全部退款'
          AND t.operate_desc != '已签收'
          AND f.logisticsNo IS NOT NULL
          AND f.logisticsNo != ''
        ORDER BY f.tradeTime DESC
    """).fetchall()

    total = len(orders)
    status_count = {}
    for row in orders:
        st = row[4]
        status_count[st] = status_count.get(st, 0) + 1

    rows_html = ""
    for i, (srcTids, logisticsNo, goodsCount, orderItemList, logisticsStatus, shopName, tradeTime, refundAmount, paid, estimateConsignTime) in enumerate(orders, 1):
        items = []
        try:
            item_list = json.loads(orderItemList) if orderItemList else []
            for item in item_list:
                spu = item.get("spuName", "")
                sku = item.get("skuName", "")
                num = item.get("skuNum", "1")
                spec = ""
                if sku:
                    parts = sku.split(";", 1)
                    spec = parts[1] if len(parts) > 1 else sku
                elif spu:
                    spec = spu
                else:
                    spec = "-"
                items.append(f"{spec} x{num}")
        except Exception:
            items = ["解析失败"]
        product_str = "<br>".join(items) if items else "-"

        status_color = {
            "运输中": "#3b82f6",
            "派送中": "#a855f7",
            "其它": "#8b949e",
            "问题件": "#f85149",
            "拒收": "#f97316",
            "待揽件": "#d29922",
        }.get(logisticsStatus, "#8b949e")

        rows_html += f"""<tr>
            <td>{i}</td>
            <td style="color:#fff;font-weight:600;">{product_str}</td>
            <td style="text-align:center;">{goodsCount}</td>
            <td><code style="background:#161b22;padding:2px 6px;border-radius:4px;font-size:12px;">{srcTids}</code></td>
            <td><code style="background:#161b22;padding:2px 6px;border-radius:4px;font-size:12px;">{logisticsNo}</code></td>
            <td><span style="color:{status_color};font-weight:600;">{logisticsStatus}</span></td>
            <td style="color:#8b949e;font-size:12px;">{shopName}</td>
            <td style="color:#d29922;font-size:12px;font-weight:600;">{estimateConsignTime[:16] if estimateConsignTime else '-'}</td>
            <td style="color:#8b949e;font-size:12px;">{tradeTime[:16] if tradeTime else '-'}</td>
        </tr>"""

    conn.close()

    status_summary = ""
    for st, cnt in sorted(status_count.items(), key=lambda x: -x[1]):
        color = {"运输中": "#3b82f6", "派送中": "#a855f7", "其它": "#8b949e", "问题件": "#f85149", "拒收": "#f97316", "待揽件": "#d29922"}.get(st, "#8b949e")
        status_summary += f'<span style="color:{color};margin-right:16px;font-weight:600;">{st}: {cnt}</span>'

    summary_div = f"""
<div style="margin-bottom:20px;padding:16px;background:#161b22;border:1px solid #30363d;border-radius:10px;">
    <div style="font-size:14px;color:#8b949e;margin-bottom:8px;">已发货退单未签收订单汇总</div>
    <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
        <span style="color:#f85149;font-size:24px;font-weight:700;">{total}</span>
        <span style="color:#8b949e;font-size:13px;">单</span>
        <span style="color:#30363d;">|</span>
        {status_summary}
    </div>
</div>"""

    table_html = f"""
<div style="overflow-x:auto;border-radius:10px;border:1px solid #30363d;">
<table style="width:100%;border-collapse:collapse;font-size:13px;">
    <thead>
        <tr style="background:#161b22;color:#8b949e;text-align:left;">
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">#</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">品名 &amp; 规格</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;text-align:center;">数量</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">平台订单号</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">快递单号</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">物流状态</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">店铺</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">最晚发货时间</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">下单时间</th>
        </tr>
    </thead>
    <tbody>
        {rows_html if rows_html else "<tr><td colspan='9' style='padding:20px;text-align:center;color:#484f58;'>暂无数据</td></tr>"}
    </tbody>
</table>
</div>"""

    body = summary_div + table_html
    return page("换单补单", "replaceorder", body)



def render_upload():
    files = upload_files_list()
    body = '<h1 style="color:#fff;font-size:22px;margin-bottom:16px;">文件上传</h1>'
    body += '<div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:16px;margin-bottom:20px;">'
    TEMPLATES = {
        'weight': ('规格品名重量_模版.xlsx', '规格品名重量'),
        'product_cost': ('产品成本_模版.xlsx', '产品成本'),
    }
    for zone_key, label, accept_ext, btn_text, icon in [
        ('finance', '财务对账 CSV', '.csv', '上传财务文件', '📊'),
        ('after_sales', '售后数据 Excel', '.xlsx', '上传售后文件', '📋'),
        ('weight', '规格重量 Excel', '.xlsx', '上传规格文件', '⚖️'),
        ('product_cost', '产品成本 Excel', '.xlsx', '上传成本文件', '💰'),
    ]:
        tmpl_link = ''
        if zone_key in TEMPLATES:
            tmpl_file, tmpl_name = TEMPLATES[zone_key]
            tmpl_link = f'<a href="/templates/{tmpl_file}" style="display:block;text-align:center;font-size:12px;color:#58a6ff;margin-top:6px;text-decoration:none;">下载{tmpl_name}模板</a>'
        body += f'''<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;">
  <h3 style="color:#00d4ff;margin-bottom:8px;">{label}</h3>
  <div class="drop-zone" id="drop_{zone_key}" onclick="document.getElementById('file_{zone_key}').click()">
    <div style="font-size:28px;">{icon}</div><div style="color:#00d4ff;font-size:13px;">点击或拖拽</div>
    <div class="file-name" id="fname_{zone_key}" style="color:#00e676;font-size:13px;margin-top:6px;display:none;"></div>
    <input type="file" id="file_{zone_key}" accept="{accept_ext}" onchange="showName('{zone_key}')" style="display:none;">
  </div>
  <button class="btn" onclick="upload('{zone_key}')" style="display:block;width:100%;padding:10px;border:none;border-radius:6px;background:#238636;color:#fff;font-size:14px;cursor:pointer;margin-top:10px;">{btn_text}</button>
  {tmpl_link}
</div>'''
    body += '</div>'
    body += '<div style="background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;"><h3 style="color:#00d4ff;margin-bottom:12px;">已上传文件</h3>'
    body += '<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr style="border-bottom:1px solid #30363d;"><th style="padding:8px;text-align:left;color:#8b949e;">文件名</th><th style="padding:8px;text-align:left;color:#8b949e;">类型</th><th style="padding:8px;text-align:left;color:#8b949e;">时间</th><th style="padding:8px;text-align:left;color:#8b949e;">大小</th></tr></thead><tbody id="file_list">'
    for f in files:
        body += f'<tr style="border-bottom:1px solid #21262d;"><td style="padding:6px 8px;">{html_mod.escape(f["name"])}</td><td style="padding:6px 8px;">{html_mod.escape(f["type"])}</td><td style="padding:6px 8px;">{html_mod.escape(f["time"])}</td><td style="padding:6px 8px;">{html_mod.escape(f["size"])}</td></tr>'
    body += '</tbody></table></div><div id="result" style="display:none;padding:10px;border-radius:6px;margin-top:12px;font-size:13px;"></div>'
    body += """<script>
const FILES={};
function showName(type){const f=document.getElementById('file_'+type).files[0];if(f){FILES[type]=f;const el=document.getElementById('fname_'+type);el.textContent=f.name;el.style.display='block'}}
['finance','after_sales','weight','product_cost'].forEach(type=>{const dz=document.getElementById('drop_'+type);dz.addEventListener('dragover',e=>{e.preventDefault();dz.style.borderColor='#00d4ff';dz.style.background='rgba(0,212,255,0.05)'});dz.addEventListener('dragleave',()=>{dz.style.borderColor='#30363d';dz.style.background=''});dz.addEventListener('drop',e=>{e.preventDefault();dz.style.borderColor='#30363d';dz.style.background='';const f=e.dataTransfer.files[0];if(f){FILES[type]=f;const el=document.getElementById('fname_'+type);el.textContent=f.name;el.style.display='block'}})});
function upload(type){
  const file=FILES[type];
  if(!file){showResult("请先选择文件",false);return}
  const idx=["finance","after_sales","weight","product_cost"].indexOf(type);
  const btns=document.querySelectorAll(".btn");
  if(btns[idx]){btns[idx].disabled=true;btns[idx].textContent="上传中..."}
  
  const CHUNK_SIZE=5*1024*1024; // 5MB每块
  const totalChunks=Math.ceil(file.size/CHUNK_SIZE);
  showProgress(0,file.name+" (分"+totalChunks+"块)");
  
  let uploadedChunks=0;
  
  async function uploadChunk(chunkId){
    const start=chunkId*CHUNK_SIZE;
    const end=Math.min(start+CHUNK_SIZE,file.size);
    const chunk=file.slice(start,end);
    
    const reader=new FileReader();
    reader.onload=async function(){
      const base64=btoa(new Uint8Array(reader.result).reduce((d,b)=>d+String.fromCharCode(b),""));
      
      try{
        const resp=await fetch("/chunk",{
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({
            chunkId:chunkId,
            filename:file.name,
            data:base64,
            totalChunks:totalChunks,
            type:type
          })
        });
        const d=await resp.json();
        
        if(d.ok){
          uploadedChunks++;
          const pct=Math.round((uploadedChunks/totalChunks)*100);
          showProgress(pct,file.name+" (块"+uploadedChunks+"/"+totalChunks+")");
          
          if(d.merged){
            hideProgress();
            showResult(d.msg,true);
            loadFiles();
            FILES[type]=null;
            document.getElementById("file_"+type).value="";
            document.getElementById("fname_"+type).style.display="none";
            const btns2=document.querySelectorAll(".btn");
            if(btns2[idx]){btns2[idx].disabled=false;btns2[idx].textContent=["上传财务文件","上传售后文件","上传规格文件","上传成本文件"][idx]}
          }else if(chunkId+1<totalChunks){
            uploadChunk(chunkId+1);
          }
        }else{
          hideProgress();
          showResult(d.msg,false);
          const btns2=document.querySelectorAll(".btn");
          if(btns2[idx]){btns2[idx].disabled=false;btns2[idx].textContent=["上传财务文件","上传售后文件","上传规格文件","上传成本文件"][idx]}
        }
      }catch(e){
        hideProgress();
        showResult("上传失败: "+e.message,false);
        const btns2=document.querySelectorAll(".btn");
        if(btns2[idx]){btns2[idx].disabled=false;btns2[idx].textContent=["上传财务文件","上传售后文件","上传规格文件","上传成本文件"][idx]}
      }
    };
    reader.readAsArrayBuffer(chunk);
  }
  
  uploadChunk(0);
}
function showProgress(pct,filename){const el=document.getElementById("progress-container");if(!el){const div=document.createElement("div");div.id="progress-container";div.innerHTML="<div style='margin:12px 0;padding:16px;background:#161b22;border:1px solid #30363d;border-radius:8px;'><div style='color:#8b949e;font-size:13px;margin-bottom:8px;'>上传: "+filename+"</div><div style='background:#21262d;border-radius:4px;height:24px;overflow:hidden;'><div id='progress-bar' style='height:100%;background:linear-gradient(90deg,#238636,#3fb950);width:"+pct+"%;transition:width 0.2s;'></div></div><div id='progress-text' style='text-align:center;color:#3fb950;font-size:16px;font-weight:600;margin-top:8px;'>"+pct+"%</div></div>";document.getElementById("result").before(div)}else{document.getElementById("progress-bar").style.width=pct+"%";document.getElementById("progress-text").textContent=pct+"%"}document.getElementById("result").style.display="none"}
function hideProgress(){const el=document.getElementById("progress-container");if(el)el.remove()}
function showResult(msg,ok){const el=document.getElementById("result");el.textContent=msg;el.style.display="block";el.style.background=ok?"rgba(0,230,118,0.1)":"rgba(255,82,82,0.1)";el.style.color=ok?"#00e676":"#ff5252";setTimeout(()=>el.style.display="none",15000)}
async function loadFiles(){try{const r=await fetch('/files');const data=await r.json();document.getElementById('file_list').innerHTML=data.files.map(f=>'<tr style="border-bottom:1px solid #21262d;"><td style="padding:6px 8px;">'+f.name+'</td><td style="padding:6px 8px;">'+f.type+'</td><td style="padding:6px 8px;">'+f.time+'</td><td style="padding:6px 8px;">'+f.size+'</td></tr>').join('')}catch(e){}}
</script>"""
    return page('文件上传', 'upload', body)


# ===== HTTP Handler =====
class ERPHandler(BaseHTTPRequestHandler):
    allow_reuse_address = True

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == '/':
            self._send_html(render_home())
        elif path == '/daily':
            shop = qs.get('shop', [None])[0]
            self._send_html(render_daily(shop))
        elif path == '/warehouse':
            self._send_html(render_warehouse())
        elif path == '/waitcheck':
            self._send_html(render_waitcheck())
        elif path == '/forecast':
            self._send_html(render_forecast())
        elif path == '/finance':
            self._send_html(render_finance())
        elif path == '/aftersales':
            self._send_html(render_aftersales())
        elif path == '/afterdashboard':
            self._send_html(render_aftersales_v2())
        elif path == '/logistics':
            self._send_html(render_logistics_cached())
        elif path == '/exceptionorders':
            self._send_html(render_exceptionorders())
        elif path == '/replaceorder':
            self._send_html(render_replaceorder())
        elif path == '/upload':
            self._send_html(render_upload())
        elif path == '/files':
            self._json_response({'files': upload_files_list()})
        elif path == '/refresh-cache':
            clear_cache()
            self._json_response({'ok': True, 'msg': '缓存已清除，下次访问将重新生成'})
        elif path.startswith('/templates/'):
            self._serve_template(path)
        else:
            self._404()

    def do_POST(self):
        if self.path == '/chunk':
            # 分块上传接口
            try:
                length = int(self.headers.get('Content-Length', 0))
                data = self.rfile.read(length)
                import json as js
                req = js.loads(data.decode('utf-8'))
                chunk_id = req.get('chunkId')
                filename = req.get('filename')
                chunk_data = req.get('data')  # base64
                total_chunks = req.get('totalChunks')
                type_item = req.get('type')
                
                if chunk_id is None or not filename or not chunk_data or not type_item:
                    self._json_response({'ok': False, 'msg': '缺少参数'}, 400)
                    return
                
                if type_item not in UPLOAD_DIRS:
                    self._json_response({'ok': False, 'msg': f'未知类型: {type_item}'}, 400)
                    return
                
                import base64
                chunk_dir = os.path.join(UPLOAD_DIRS[type_item], '.chunks_' + filename)
                os.makedirs(chunk_dir, exist_ok=True)
                chunk_path = os.path.join(chunk_dir, str(chunk_id))
                with open(chunk_path, 'wb') as f:
                    f.write(base64.b64decode(chunk_data))
                
                # 检查是否所有块都已上传
                uploaded = len(os.listdir(chunk_dir))
                if uploaded == total_chunks:
                    # 合并文件
                    final_path = os.path.join(UPLOAD_DIRS[type_item], filename)
                    with open(final_path, 'wb') as outfile:
                        for i in range(total_chunks):
                            chunk_file = os.path.join(chunk_dir, str(i))
                            with open(chunk_file, 'rb') as infile:
                                outfile.write(infile.read())
                    # 清理临时文件
                    shutil.rmtree(chunk_dir)
                    self._json_response({'ok': True, 'msg': f'上传完成: {filename}', 'merged': True})
                else:
                    self._json_response({'ok': True, 'msg': f'块 {chunk_id+1}/{total_chunks} 上传成功', 'merged': False})
            except Exception as e:
                self._json_response({'ok': False, 'msg': f'错误: {str(e)}'}, 500)
            return
        if self.path == '/upload':
            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' not in content_type:
                self._json_response({'ok': False, 'msg': '请使用 multipart/form-data'}, 400)
                return
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers,
                environ={'REQUEST_METHOD': 'POST'})
            file_item = form['file']
            type_item = form.getvalue('type')
            if file_item is None or not type_item:
                self._json_response({'ok': False, 'msg': '缺少文件或类型参数'}, 400)
                return
            if type_item not in UPLOAD_DIRS:
                self._json_response({'ok': False, 'msg': f'未知类型: {type_item}'}, 400)
                return
            filename = os.path.basename(file_item.filename)
            dest_dir = UPLOAD_DIRS[type_item]
            dest_path = os.path.join(dest_dir, filename)
            with open(dest_path, 'wb') as f:
                shutil.copyfileobj(file_item.file, f)
            self._json_response({'ok': True, 'msg': f'上传成功: {filename}'})
        else:
            self._404()

    def _send_html(self, html_str):
        body = html_str.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Connection', 'keep-alive')
        self.send_header('Keep-Alive', 'timeout=15, max=100')
        self.end_headers()
        self.wfile.write(body)

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Connection', 'keep-alive')
        self.send_header('Keep-Alive', 'timeout=15, max=100')
        self.end_headers()
        self.wfile.write(body)

    def _404(self):
        self.send_response(404)
        self.send_header('Connection', 'keep-alive')
        self.send_header('Keep-Alive', 'timeout=15, max=100')
        self.end_headers()

    def _serve_template(self, path):
        import mimetypes
        from urllib.parse import quote, unquote
        filename = unquote(os.path.basename(path))
        safe_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.')
        if not all(c in safe_chars or ord(c) > 127 for c in filename):
            self._404()
            return
        tmpl_dir = os.path.join(BASE, 'templates')
        filepath = os.path.join(tmpl_dir, filename)
        if not os.path.isfile(filepath):
            self._404()
            return
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        with open(filepath, 'rb') as f:
            data = f.read()
        safe_name = quote(filename.encode('utf-8'))
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Disposition', f'attachment; filename*=UTF-8\'\'{safe_name}')
        self.send_header('Content-Length', len(data))
        self.send_header('Connection', 'keep-alive')
        self.send_header('Keep-Alive', 'timeout=15, max=100')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        if '/files' not in str(args):
            print(f"[{self.log_date_time_string()}] {format % args}")


def main():
    port = 9999
    for i, arg in enumerate(sys.argv):
        if arg == '--port' and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    # 启动时预生成缓存
    print("正在预生成页面缓存...")
    for script in ['erp_full_report_email.py', 'erp_warehouse_demand_email.py', 'erp_wait_check_email.py']:
        try:
            get_cached_html(script)
            print(f"  ✓ {script}")
        except Exception as e:
            print(f"  ✗ {script}: {e}")
    server = ThreadingHTTPServer(('0.0.0.0', port), ERPHandler)
    print(f"ERP 仪表盘已启动 — http://100.101.170.7:{port}")
    print(f"缓存有效期: {CACHE_TTL}秒，访问 /refresh-cache 可手动刷新")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
