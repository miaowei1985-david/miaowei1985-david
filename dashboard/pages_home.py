#!/usr/bin/env python3
"""首页：数据看板 + 图表 + 快速导航"""
import os, json, sqlite3, time
from datetime import datetime
from collections import defaultdict

from erp_config import DB_PATH, SHOP_NAME
from dashboard import page, cached_page, get_sync_status

def _get_logistics_data(conn):
    """获取物流状态数据用于柱状图"""
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
    timeout_cnt = conn.execute("""
        SELECT COUNT(DISTINCT l.logistics_no)
        FROM logistics_trace l
        WHERE l.operate_desc = '已揽件'
        AND l.operate_time < datetime('now', '-72 hours')
        AND l.logistics_no NOT IN (
            SELECT DISTINCT logistics_no FROM logistics_trace WHERE operate_desc = '已签收'
        )
    """).fetchone()[0]
    today_signed = conn.execute("""
        SELECT COUNT(DISTINCT logistics_no)
        FROM logistics_trace
        WHERE operate_desc = '已签收' AND operate_time >= date('now')
    """).fetchone()[0]
    return {"signed": signed, "in_transit": in_transit, "delivering": delivering, "today_signed": today_signed, "timeout": timeout_cnt, "problem": problem}


def render_home():
    import sqlite3
    from datetime import datetime
    from collections import defaultdict as _dd
    now = datetime.now()
    shop = SHOP_NAME
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
