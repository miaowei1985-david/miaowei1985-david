#!/usr/bin/env python3
"""运营日报页面：店铺总览 + 各店铺详情"""
import os, json, sqlite3, time
from datetime import datetime
from urllib.parse import quote

from erp_config import DB_PATH
from dashboard import page, cached_page, get_cached_html

# 店铺列表
SHOP_LIST = ['榴愿时刻工厂店', '觅糖生鲜国际', '冕宁国源优品', '青邮生鲜三店', '榴愿食刻', 'pdd54247437212', 'pdd92663579562']
SHOP_COLORS = ['#58a6ff', '#3fb950', '#d29922', '#f0883e', '#bc8cff', '#f778ba', '#79c0ff']

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

    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        html = render_daily_landing(conn)
        conn.close()
    except Exception as e:
        html = f'<p style="color:#f85149;">数据加载失败: {e}</p>'
    return page('店铺总览', 'daily', html)
