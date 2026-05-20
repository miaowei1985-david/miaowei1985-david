#!/usr/bin/env python3
"""售后专题页面"""
import os, json, sqlite3, time
from datetime import datetime
from collections import defaultdict

from erp_config import DB_PATH, SHOP_NAME
from dashboard import page, cached_page, _cache, CACHE_TTL

def render_aftersales():
    import sqlite3
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
