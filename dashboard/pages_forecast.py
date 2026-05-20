#!/usr/bin/env python3
"""AI 销量预测页面"""
import os, json, sqlite3, time
from datetime import datetime, timedelta
from collections import defaultdict

from erp_config import DB_PATH, SHOP_NAME
from dashboard import page, cached_page

def render_forecast():
    """AI销量预测页面"""
    import sqlite3
    from datetime import datetime, timedelta
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
