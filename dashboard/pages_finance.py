#!/usr/bin/env python3
"""财务结算页面"""
import os, json, sqlite3, time
from datetime import datetime
from collections import defaultdict

from erp_config import DB_PATH, SHOP_NAME
from dashboard import page, cached_page, _cache, CACHE_TTL

def render_finance():
    import sqlite3
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
