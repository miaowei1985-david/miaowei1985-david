#!/usr/bin/env python3
"""售后看板页面 (v2)"""
import os, json, sqlite3, time
from datetime import datetime
from collections import defaultdict

from erp_config import DB_PATH, SHOP_NAME
from dashboard import page, cached_page, _cache, CACHE_TTL

def render_aftersales_v2():
    import sqlite3
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
