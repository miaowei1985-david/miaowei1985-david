#!/usr/bin/env python3
"""
电商运营综合日报 — HTML邮件发送
"""
import sqlite3, json, smtplib, os
from datetime import datetime
from email.mime.text import MIMEText
from email.header import Header
from collections import defaultdict
import erp_metrics_tracker as tracker
import erp_config
from erp_config import DB_PATH, EMAIL_FROM, EMAIL_TO_FULL, send_email, setup_logger
setup_logger("full_report", "/tmp/erp_full_report.log")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=WAL")
now = datetime.now()
SEQ_KEY = "/tmp/seq_full_" + now.strftime("%Y%m%d")
if os.path.exists(SEQ_KEY):
    SEQ = int(open(SEQ_KEY).read().strip()) + 1
else:
    SEQ = 1
open(SEQ_KEY, "w").write(str(SEQ))
SEQ_STR = now.strftime("%Y%m%d") + "-" + str(SEQ)

SHOP_NAME = "榴愿时刻工厂店"

# ===== 数据加载 =====
wait_check = [dict(r) for r in conn.execute(
    f"SELECT * FROM erp_wait_check WHERE shopName = ?", (SHOP_NAME,)).fetchall()]
wait_send = [dict(r) for r in conn.execute(
    f"SELECT * FROM erp_wait_send_self WHERE shopName = ?", (SHOP_NAME,)).fetchall()]
finished = [dict(r) for r in conn.execute(
    f"SELECT * FROM erp_finished WHERE shopName = ?", (SHOP_NAME,)).fetchall()]
fin_rows = [dict(r) for r in conn.execute('SELECT * FROM finance_matched').fetchall()]
fin_unmatched_rows = [dict(r) for r in conn.execute('SELECT * FROM finance_unmatched').fetchall()]

def total_paid(rows):
    s = 0
    for r in rows:
        try: s += float(r.get('paid') or 0)
        except: pass
    return s

wc_cnt, wc_paid = len(wait_check), total_paid(wait_check)
ws_cnt, ws_paid = len(wait_send), total_paid(wait_send)
fn_cnt, fn_paid = len(finished), total_paid(finished)
total_orders = wc_cnt + ws_cnt + fn_cnt
total_money = wc_paid + ws_paid + fn_paid

# ===== 售后 =====
as_total = conn.execute(
    'SELECT COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ?',
    (SHOP_NAME,)
).fetchone()
as_unshipped = conn.execute(
    'SELECT COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ? AND outbound_status = ?',
    (SHOP_NAME, '未出库')
).fetchone()
as_shipped = conn.execute(
    'SELECT COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ? AND outbound_status = ?',
    (SHOP_NAME, '已出库')
).fetchone()

# 未售后原因
as_unshipped_reasons = conn.execute(
    'SELECT primary_reason, COUNT(*) as cnt, COALESCE(SUM(refund_amount),0) as amt '
    'FROM after_sales WHERE shop_name = ? AND outbound_status = ? AND primary_reason != "" GROUP BY primary_reason ORDER BY cnt DESC',
    (SHOP_NAME, '未出库')
).fetchall()

# 已售后原因
as_shipped_reasons = conn.execute(
    'SELECT primary_reason, COUNT(*) as cnt, COALESCE(SUM(refund_amount),0) as amt '
    'FROM after_sales WHERE shop_name = ? AND outbound_status = ? AND primary_reason != "" GROUP BY primary_reason ORDER BY cnt DESC',
    (SHOP_NAME, '已出库')
).fetchall()

# 未售后状态
as_unshipped_status = conn.execute(
    'SELECT service_status, COUNT(*) as cnt FROM after_sales WHERE shop_name = ? AND outbound_status = ? GROUP BY service_status ORDER BY cnt DESC',
    (SHOP_NAME, '未出库')
).fetchall()

# 已售后状态
as_shipped_status = conn.execute(
    'SELECT service_status, COUNT(*) as cnt FROM after_sales WHERE shop_name = ? AND outbound_status = ? GROUP BY service_status ORDER BY cnt DESC',
    (SHOP_NAME, '已出库')
).fetchall()

# 已售后仓库分布
as_shipped_warehouse = conn.execute(
    'SELECT warehouse_name, COUNT(*) as cnt, COALESCE(SUM(refund_amount),0) as amt '
    'FROM after_sales WHERE shop_name = ? AND outbound_status = ? AND warehouse_name != "" GROUP BY warehouse_name ORDER BY cnt DESC',
    (SHOP_NAME, '已出库')
).fetchall()

# 未售后财务关联
as_unshipped_fin = conn.execute(
    'SELECT COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ? AND outbound_status = ? AND finance_status != ""',
    (SHOP_NAME, '未出库')
).fetchone()

# 已售后财务关联
as_shipped_fin = conn.execute(
    'SELECT COUNT(*), COALESCE(SUM(refund_amount),0) FROM after_sales WHERE shop_name = ? AND outbound_status = ? AND finance_status != ""',
    (SHOP_NAME, '已出库')
).fetchone()

# 审核不通过（已发货但售后被拒）
as_reject = conn.execute(
    'SELECT COUNT(*), COALESCE(SUM(erp_paid),0), COALESCE(SUM(finance_net),0) '
    'FROM after_sales WHERE shop_name = ? AND service_status = ?',
    (SHOP_NAME, '审核不通过')
).fetchone()

as_reject_reasons = conn.execute(
    'SELECT primary_reason, COUNT(*), COALESCE(SUM(erp_paid),0), COALESCE(SUM(finance_net),0) '
    'FROM after_sales WHERE shop_name = ? AND service_status = ? '
    'GROUP BY primary_reason ORDER BY COUNT(*) DESC',
    (SHOP_NAME, '审核不通过')
).fetchall()

conn.close()

# ===== 指标趋势追踪 =====
prev = tracker.load_prev('full_report')

# ===== 财务聚合 =====
fin_total_income = sum(float(d.get('总收入') or 0) for d in fin_rows)
fin_total_expense = sum(float(d.get('总支出') or 0) for d in fin_rows)
fin_total_net = fin_total_income - fin_total_expense

fin_settled = sum(1 for d in fin_rows if d.get('结算状态') == '已结算')
fin_pending = sum(1 for d in fin_rows if d.get('结算状态') == '待结算')
pending_cnt = fin_pending
fin_not_settle = sum(1 for d in fin_rows if d.get('结算状态') not in ('已结算', '待结算'))

# 结算状态分组
settle_groups = defaultdict(lambda: [0, 0.0, 0.0])
for d in fin_rows:
    st = d.get('结算状态', '未知')
    settle_groups[st][0] += 1
    settle_groups[st][1] += float(d.get('总收入') or 0)
    settle_groups[st][2] += float(d.get('总支出') or 0)

# 费用项
fin_income_items = defaultdict(float)
fin_expense_items = defaultdict(float)
for d in fin_rows:
    for k, v in d.items():
        if k.endswith('收入') and v and k not in ('总收入', '代收配送费收入'):
            fin_income_items[k] += float(v or 0)
        elif k.endswith('支出') and v and k not in ('总支出', '代收配送费支出'):
            fin_expense_items[k] += abs(float(v or 0))

# 仓库财务
fin_warehouse = defaultdict(lambda: [0, 0.0])
for d in fin_rows:
    wn = d.get('warehouse_name', '') or '未知'
    fin_warehouse[wn][0] += 1
    fin_warehouse[wn][1] += float(d.get('净额') or 0)

# ===== 核心指标 — 运营 =====
as_rate = as_total[0] / total_orders * 100 if total_orders else 0
ship_rate = fn_cnt / total_orders * 100 if total_orders else 0

# 售后率 = 售后数 / 总订单数（待审核+待发货+已完成）
as_rate_on_total = as_total[0] / total_orders * 100 if total_orders else 0

# ===== 核心指标 — 财务 =====
settled_net = sum(float(d.get('净额') or 0) for d in fin_rows if d.get('结算状态') == '已结算')
pending_net = sum(float(d.get('净额') or 0) for d in fin_rows if d.get('结算状态') == '待结算')
refund_ratio = as_total[1] / fin_total_net * 100 if fin_total_net else 0

# ===== 仓库分布 =====
def warehouse_breakdown(rows):
    wh = defaultdict(lambda: [0, 0.0])
    for r in rows:
        wn = r.get('warehouseName', '') or '未知'
        wh[wn][0] += 1
        try: wh[wn][1] += float(r.get('paid') or 0)
        except: pass
    return sorted(wh.items(), key=lambda x: -x[1][0])

wc_wh = warehouse_breakdown(wait_check)
ws_wh = warehouse_breakdown(wait_send)
fn_wh = warehouse_breakdown(finished)

# 商品分布
def product_breakdown(rows):
    prods = defaultdict(int)
    for r in rows:
        raw = r.get('orderItemList', '')
        if not raw: continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(items, list):
                for item in items:
                    spu = item.get('spuName', '')
                    sku = item.get('skuName', '')
                    if spu:
                        prods[f"{spu}|{sku}"] += 1
        except: pass
    return sorted(prods.items(), key=lambda x: -x[1])

products = product_breakdown(wait_check + wait_send + finished)

# ===== 待审核 — 每日SKU汇总 =====
def daily_sku_summary(rows, shop_filter=SHOP_NAME):
    """按 日期 + 仓库 + SPU + SKU 汇总数量和订单数，仅统计指定店铺"""
    sku_data = {}  # (date, wh, spu, sku) -> [qty, orders]
    for r in rows:
        # 检查shopName
        r_shop = r.get('shopName', '') or ''
        if r_shop != shop_filter:
            continue
        dl = r.get('estimateConsignTime', '') or ''
        date = dl.split('T')[0] if dl else '未知'
        wh = r.get('warehouseName', '') or '未知'
        raw = r.get('orderItemList', '')
        if not raw: continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(items, list):
                for item in items:
                    spu = item.get('spuName', '')
                    sku = item.get('skuName', '')
                    qty = int(item.get('goodsCount', 1) or 1)
                    key = (date, wh, spu, sku)
                    if key not in sku_data:
                        sku_data[key] = [0, 0]
                    sku_data[key][0] += qty
                    sku_data[key][1] += 1
        except:
            pass
    # 按日期+仓库分组
    by_date_wh = defaultdict(list)
    for (date, wh, spu, sku), (qty, orders) in sku_data.items():
        by_date_wh[(date, wh)].append((spu, sku, qty, orders))
    return by_date_wh

wc_daily_sku = daily_sku_summary(wait_check)
ws_daily_sku = daily_sku_summary(wait_send)

# ===== 物流状态（已完成订单） =====
def logistics_by_status(rows):
    """按物流状态分组，记录每组的明细"""
    groups = defaultdict(lambda: defaultdict(lambda: [0, 0.0, []]))
    for r in rows:
        status = r.get('traceStatusMsg', '') or '未知'
        wn = r.get('warehouseName', '') or '未知'
        ct = r.get('consignTime', '') or ''
        paid_val = 0
        try: paid_val = float(r.get('paid') or 0)
        except: pass
        groups[status][wn][0] += 1
        groups[status][wn][1] += paid_val
        if ct:
            groups[status][wn][2].append(ct)
    return groups

log_data = logistics_by_status(finished)

# 物流状态汇总
log_summary = defaultdict(lambda: [0, 0.0])
for r in finished:
    status = r.get('traceStatusMsg', '') or '未知'
    paid_val = 0
    try: paid_val = float(r.get('paid') or 0)
    except: pass
    log_summary[status][0] += 1
    log_summary[status][1] += paid_val

# 时间分布
def deadline_groups(rows):
    groups = defaultdict(lambda: [0, 0.0])
    for r in rows:
        dl = r.get('estimateConsignTime', '')
        if dl:
            groups[dl][0] += 1
            try: groups[dl][1] += float(r.get('paid') or 0)
            except: pass
    return sorted(groups.items())

# 按仓库分组
def deadline_by_warehouse(rows):
    groups = defaultdict(lambda: defaultdict(lambda: [0, 0.0]))
    for r in rows:
        wn = r.get('warehouseName', '') or '未知'
        dl = r.get('estimateConsignTime', '')
        if dl:
            groups[wn][dl][0] += 1
            try: groups[wn][dl][1] += float(r.get('paid') or 0)
            except: pass
    result = {}
    for wn, dls in groups.items():
        result[wn] = sorted(dls.items())
    return result

wc_dl_by_wh = deadline_by_warehouse(wait_check)
ws_dl_by_wh = deadline_by_warehouse(wait_send)
wc_deadlines = deadline_groups(wait_check)
ws_deadlines = deadline_groups(wait_send)

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

def net_color(v):
    return GREEN if v >= 0 else RED

def sec_num(n):
    return f'{n:02d}'

def metric_card(value, label, color=ACCENT, trend=""):
    return (
        f'<div class="metric-card">'
        f'<div class="metric-value" style="color:{color}">{value}{trend}</div>'
        f'<div class="metric-label">{label}</div></div>'
    )

def status_tag(text, color):
    return f'<span class="status-tag" style="color:{color}">{text}</span>'

html = f'''<div class="report-wrapper">
<div class="report-header">
<h1>榴愿时刻工厂店 运营日报</h1>
<p class="timestamp">生成时间: {now.strftime("%Y-%m-%d %H:%M:%S")}</p>
</div>

<!-- 01 核心指标 -->
<div class="section">
<h2 class="section-title"><span class="sec-num">{sec_num(1)}</span>核心指标速览 · KPI OVERVIEW</h2>

<div class="subsection">
<h3 class="sub-title">运营指标</h3>
<div class="metric-grid">
{metric_card(f'{wc_cnt:,}', '待审核订单', AMBER, tracker.badge(wc_cnt, 'wc_cnt', prev))}
{metric_card(f'{ws_cnt:,}', '待发货订单', ACCENT, tracker.badge(ws_cnt, 'ws_cnt', prev))}
{metric_card(f'{fn_cnt:,}', '已完成订单', GREEN, tracker.badge(fn_cnt, 'fn_cnt', prev))}
{metric_card(f'{as_total[0]:,}', '售后工单', RED, tracker.badge(as_total[0], 'as_total', prev))}
{metric_card(f'{ship_rate:.1f}%', '发货率', ACCENT, tracker.badge(ship_rate, 'ship_rate', prev))}
{metric_card(f'{as_rate_on_total:.1f}%', '售后率（售后/总订单）', RED, tracker.badge(as_rate_on_total, 'as_rate_on_total', prev))}
{metric_card(f'{as_reject[0]:,}', '审核不通过（已发货）', RED, tracker.badge(as_reject[0], 'as_reject_cnt', prev))}
{metric_card(f'{as_reject[2]:,.0f}元', '审核不通过损失净额', RED, tracker.badge(as_reject[2], 'as_reject_net', prev))}
</div>
</div>

<div class="subsection">
<h3 class="sub-title">财务指标</h3>
<div class="metric-grid">
{metric_card(f'{fin_total_income:,.0f}元', '总收入', GREEN, tracker.badge(fin_total_income, 'fin_income', prev))}
{metric_card(f'{fin_total_expense:,.0f}元', '总支出', RED, tracker.badge(fin_total_expense, 'fin_expense', prev))}
{metric_card(f'{fin_total_net:,.0f}元', '净额', ACCENT, tracker.badge(fin_total_net, 'fin_net', prev))}
{metric_card(f'{pending_cnt:,}', '待结算单数', AMBER, tracker.badge(pending_cnt, 'pending_cnt', prev))}
{metric_card(f'{settled_net:,.2f}元', '已结算到账', GREEN, tracker.badge(settled_net, 'settled_net', prev))}
{metric_card(f'{pending_net:,.0f}元', '待结算在途', AMBER, tracker.badge(pending_net, 'pending_net', prev))}
{metric_card(f'{as_total[1]:,.2f}元', '售后退款总额', RED, tracker.badge(as_total[1], 'as_total_amt', prev))}
{metric_card(f'{refund_ratio:.1f}%', '退款/净额比', RED, tracker.badge(refund_ratio, 'refund_ratio', prev))}
</div>
</div>
</div>

<!-- 02 订单状态 -->
<div class="section">
<h2 class="section-title"><span class="sec-num">{sec_num(2)}</span>ERP订单状态 · ORDER STATUS</h2>
<table class="data-table">
<thead><tr>
<th>状态</th><th class="r">订单数</th><th class="r">占比</th><th class="r">实收金额</th>
</tr></thead>
<tbody>
<tr><td><span class="dot" style="background:{AMBER}"></span>待审核</td>
<td class="r b">{wc_cnt:,}</td><td class="r">{wc_cnt/total_orders*100:.1f}%</td><td class="r">{wc_paid:,.0f}元</td></tr>
<tr><td><span class="dot" style="background:{ACCENT}"></span>待发货</td>
<td class="r b">{ws_cnt:,}</td><td class="r">{ws_cnt/total_orders*100:.1f}%</td><td class="r">{ws_paid:,.0f}元</td></tr>
<tr><td><span class="dot" style="background:{GREEN}"></span>已完成</td>
<td class="r b">{fn_cnt:,}</td><td class="r">{fn_cnt/total_orders*100:.1f}%</td><td class="r">{fn_paid:,.0f}元</td></tr>
<tr class="row-total"><td>合计</td><td class="r">{total_orders:,}</td><td class="r">100%</td><td class="r">{total_money:,.0f}元</td></tr>
</tbody></table>
</div>
'''

# ===== 03 仓库需求 =====
html += f'''
<div class="section">
<h2 class="section-title"><span class="sec-num">{sec_num(3)}</span>仓库需求明细 · WAREHOUSE DEADLINE</h2>
'''

def deadline_table_dark(title, dl_by_wh, summary_wh):
    t = f'<h3 class="sub-title">{title}</h3>'
    for wn in [x[0] for x in summary_wh]:
        dls = dl_by_wh.get(wn, [])
        if not dls:
            continue
        t += f'<p class="wh-label">{wn}</p>'
        t += '<table class="data-table compact">'
        t += '<thead><tr><th>最晚发货时间</th><th class="r">订单数</th><th class="r">金额</th><th class="r">剩余小时</th><th class="c">状态</th></tr></thead>'
        t += '<tbody>'
        wh_total = 0
        wh_cnt = 0
        for dl, (cnt, money) in dls:
            try:
                dl_dt = datetime.fromisoformat(dl.replace('T', ' '))
                hours = (dl_dt - now).total_seconds() / 3600
            except:
                hours = 999
            if hours < 0:
                status, color = '已超时', RED
            elif hours < 12:
                status, color = '紧急', AMBER
            else:
                status, color = '正常', GREEN
            t += f'<tr><td>{dl}</td><td class="r">{cnt:,}</td><td class="r">{money:,.0f}元</td><td class="r">{hours:+.1f}</td>'
            t += f'<td class="c">{status_tag(status, color)}</td></tr>'
            wh_cnt += cnt
            wh_total += money
        t += f'<tr class="row-subtotal"><td>小计</td><td class="r">{wh_cnt:,}</td><td class="r">{wh_total:,.0f}元</td><td colspan="2"></td></tr>'
        t += '</tbody></table>'
    return t

html += deadline_table_dark('待审核订单（按仓库）', wc_dl_by_wh, wc_wh)
html += deadline_table_dark('待发货订单（按仓库）', ws_dl_by_wh, ws_wh)
html += '</div>'

# 03.1 每日SKU汇总
html += f'''
<div class="section">
<h2 class="section-title"><span class="sec-num">3.1</span>每日SKU需求汇总 · SKU DEMAND</h2>
'''

def daily_sku_table_dark(title, by_date_wh):
    t = '<table class="data-table compact">'
    t += '<thead><tr><th>日期</th><th>仓库</th><th>品名</th><th>规格</th><th class="r">数量</th><th class="r">订单数</th></tr></thead>'
    t += '<tbody>'
    sorted_keys = sorted(by_date_wh.keys(), key=lambda x: x[0])
    prev_date = None
    for (date, wh) in sorted_keys:
        items = sorted(by_date_wh[(date, wh)], key=lambda x: -x[2])
        for spu, sku, qty, orders in items:
            row_class = ''
            if date != prev_date:
                row_class = ' class="row-new-date"'
                prev_date = date
            slabel = spu if spu else '—'
            kulabel = sku if sku else '—'
            t += f'<tr{row_class}><td>{date}</td><td>{wh}</td><td>{slabel}</td><td>{kulabel}</td>'
            t += f'<td class="r b">{qty:,}</td><td class="r">{orders:,}</td></tr>'
    t += '</tbody></table>'
    return t

html += f'<h3 class="sub-title">待审核 — 每日SKU汇总（共{len(wc_daily_sku)}个日期×仓库组合）</h3>'
html += daily_sku_table_dark('待审核SKU', wc_daily_sku)
html += f'<h3 class="sub-title">待发货 — 每日SKU汇总（共{len(ws_daily_sku)}个日期×仓库组合）</h3>'
html += daily_sku_table_dark('待发货SKU', ws_daily_sku)
html += '</div>'

# 04 商品明细
html += f'''
<div class="section">
<h2 class="section-title"><span class="sec-num">{sec_num(4)}</span>商品明细 · PRODUCT LIST</h2>
<table class="data-table compact">
<thead><tr><th>品名</th><th>规格</th><th class="r">出现次数</th></tr></thead>
<tbody>
'''
for key, cnt in products:
    parts = key.split('|', 1)
    spu = parts[0]
    sku = parts[1] if len(parts) > 1 else ''
    html += f'<tr><td>{spu}</td><td>{sku}</td><td class="r">{cnt:,}</td></tr>'
html += '</tbody></table></div>'

# 05 物流状态
html += f'''
<div class="section">
<h2 class="section-title"><span class="sec-num">{sec_num(5)}</span>已完成订单物流状态 · LOGISTICS</h2>
<h3 class="sub-title">物流状态汇总</h3>
<table class="data-table">
<thead><tr><th>物流状态</th><th class="r">订单数</th><th class="r">占比</th><th class="r">金额</th></tr></thead>
<tbody>
'''

status_order = ['已签收', '派送中', '运输中', '已揽件', '待揽件', '问题件', '订阅失败', '其它', '拦截成功', '未知']
status_colors_map = {
    '已签收': GREEN, '派送中': '#3498db', '运输中': '#2980b9',
    '已揽件': AMBER, '待揽件': '#e67e22', '问题件': RED,
    '订阅失败': TEXT_DIM, '其它': '#7f8c8d', '拦截成功': '#c0392b',
}
for status in status_order:
    if status not in log_summary:
        continue
    cnt, money = log_summary[status]
    pct = cnt / fn_cnt * 100 if fn_cnt else 0
    color = status_colors_map.get(status, TEXT)
    html += f'<tr><td>{status_tag(status, color)}</td><td class="r">{cnt:,}</td><td class="r">{pct:.1f}%</td><td class="r">{money:,.0f}元</td></tr>'
for status, (cnt, money) in sorted(log_summary.items(), key=lambda x: -x[1][0]):
    if status in status_order:
        continue
    pct = cnt / fn_cnt * 100 if fn_cnt else 0
    html += f'<tr><td>{status}</td><td class="r">{cnt:,}</td><td class="r">{pct:.1f}%</td><td class="r">{money:,.0f}元</td></tr>'
html += f'<tr class="row-total"><td>合计</td><td class="r">{fn_cnt:,}</td><td class="r">100%</td><td class="r">{fn_paid:,.0f}元</td></tr>'
html += '</tbody></table>'

# 按状态+仓库明细
for status in status_order:
    if status not in log_data:
        continue
    status_data = log_data[status]
    color = status_colors_map.get(status, TEXT)
    s_cnt, s_money = log_summary.get(status, [0, 0])
    html += f'<h3 class="sub-title">{status_tag(status, color)}（{s_cnt:,}单 / {s_money:,.0f}元）</h3>'
    for wn in sorted(status_data.keys(), key=lambda x: -status_data[x][0]):
        wh_cnt, wh_money, timestamps = status_data[wn]
        html += f'<p class="wh-label">{wn}</p>'
        html += '<table class="data-table compact"><tbody>'
        html += f'<tr><td><span class="dim">订单数:</span> {wh_cnt:,}</td>'
        html += f'<td><span class="dim">金额:</span> {wh_money:,.0f}元</td>'
        if status == '已签收':
            ts_sorted = sorted(set(timestamps))
            ts_text = ', '.join(ts_sorted[:10])
            if len(ts_sorted) > 10:
                ts_text += f' ... 共{len(ts_sorted)}个'
            html += f'<td><span class="dim">发货时间:</span> {ts_text}</td>'
        else:
            html += f'<td><span class="dim">发货时间:</span> —</td>'
        html += '</tr></tbody></table>'
html += '</div>'

# 06 财务结算
html += f'''
<div class="section">
<h2 class="section-title"><span class="sec-num">{sec_num(6)}</span>财务结算分析 · FINANCE</h2>
<h3 class="sub-title">结算状态汇总</h3>
<table class="data-table">
<thead><tr><th>结算状态</th><th class="r">订单数</th><th class="r">总收入</th><th class="r">总支出</th><th class="r">净额</th></tr></thead>
<tbody>
'''
for st, (cnt, inc, exp) in sorted(settle_groups.items(), key=lambda x: -x[1][0]):
    net = inc - exp
    nc = GREEN if net >= 0 else RED
    html += f'<tr><td><b>{st}</b></td><td class="r">{cnt:,}</td><td class="r">{inc:,.2f}元</td><td class="r">{exp:,.2f}元</td><td class="r" style="color:{nc}"><b>{net:,.2f}元</b></td></tr>'
html += f'<tr class="row-total"><td>合计</td><td class="r">{len(fin_rows):,}</td><td class="r">{fin_total_income:,.2f}元</td><td class="r">{fin_total_expense:,.2f}元</td><td class="r" style="color:{net_color(fin_total_net)}"><b>{fin_total_net:,.2f}元</b></td></tr>'
html += '</tbody></table>'

# 收入/支出项目
html += '<h3 class="sub-title">收入项目明细</h3>'
html += '<table class="data-table compact"><tbody>'
for k, v in sorted(fin_income_items.items(), key=lambda x: -x[1]):
    html += f'<tr><td>{k}</td><td class="r" style="color:{GREEN}"><b>{v:,.2f}元</b></td></tr>'
html += '</tbody></table>'

html += '<h3 class="sub-title">支出项目明细</h3>'
html += '<table class="data-table compact"><tbody>'
for k, v in sorted(fin_expense_items.items(), key=lambda x: -x[1]):
    html += f'<tr><td>{k}</td><td class="r" style="color:{RED}"><b>{v:,.2f}元</b></td></tr>'
html += '</tbody></table>'

html += '<h3 class="sub-title">仓库财务汇总</h3>'
html += '<table class="data-table compact"><thead><tr><th>仓库</th><th class="r">订单数</th><th class="r">净额</th></tr></thead><tbody>'
for wn, (c, n) in sorted(fin_warehouse.items(), key=lambda x: -x[1][1]):
    nc = GREEN if n >= 0 else RED
    html += f'<tr><td>{wn}</td><td class="r">{c:,}</td><td class="r" style="color:{nc}"><b>{n:,.2f}元</b></td></tr>'
html += '</tbody></table>'
html += f'<p class="dim-text">未匹配财务数据的订单: <b>{len(fin_unmatched_rows):,}</b> 条</p>'
html += '</div>'

# 07 售后分析
html += f'''
<div class="section">
<h2 class="section-title"><span class="sec-num">{sec_num(7)}</span>售后分析 · AFTER-SALES</h2>
<h3 class="sub-title">售后总览</h3>
<table class="data-table">
<thead><tr><th>指标</th><th class="r">全部</th><th class="r">未出库</th><th class="r">已出库</th></tr></thead>
<tbody>
<tr><td>售后工单数</td><td class="r b">{as_total[0]:,}</td><td class="r">{as_unshipped[0]:,}</td><td class="r">{as_shipped[0]:,}</td></tr>
<tr><td>退款总额</td><td class="r" style="color:{RED}"><b>{as_total[1]:,.2f}元</b></td><td class="r" style="color:{RED}">{as_unshipped[1]:,.2f}元</td><td class="r" style="color:{RED}">{as_shipped[1]:,.2f}元</td></tr>
<tr><td>关联财务</td><td class="r">{as_unshipped_fin[0] + as_shipped_fin[0]:,}</td><td class="r">{as_unshipped_fin[0]:,}</td><td class="r">{as_shipped_fin[0]:,}</td></tr>
<tr><td>退款/财务收入比</td><td class="r" colspan="3">{as_total[1]/fin_total_income*100:.1f}%</td></tr>
</tbody></table>
'''

def reason_table_dark(title, rows, total_cnt):
    t = f'<h3 class="sub-title">{title}</h3>'
    t += '<table class="data-table compact"><thead><tr><th>原因</th><th class="r">数量</th><th class="r">退款金额</th><th class="r">占比</th></tr></thead><tbody>'
    for row in rows:
        reason, cnt, amt = row[0], row[1], float(row[2] or 0)
        pct = cnt / total_cnt * 100 if total_cnt else 0
        rlabel = reason if reason else '未分类'
        t += f'<tr><td>{rlabel}</td><td class="r">{cnt:,}</td><td class="r" style="color:{RED}">{amt:,.2f}元</td><td class="r">{pct:.1f}%</td></tr>'
    t += '</tbody></table>'
    return t

html += f'<h3 class="sub-title">未出库售后（{as_unshipped[0]:,}单 / {as_unshipped[1]:,.2f}元）</h3>'
html += '<p class="dim-text">服务单状态</p>'
html += '<table class="data-table compact"><tbody>'
for row in as_unshipped_status:
    html += f'<tr><td>{row[0]}</td><td class="r">{row[1]:,}</td></tr>'
html += '</tbody></table>'
html += reason_table_dark('售后原因', as_unshipped_reasons, as_unshipped[0])

html += f'<h3 class="sub-title">已出库售后（{as_shipped[0]:,}单 / {as_shipped[1]:,.2f}元）</h3>'
html += '<p class="dim-text">服务单状态</p>'
html += '<table class="data-table compact"><tbody>'
for row in as_shipped_status:
    html += f'<tr><td>{row[0]}</td><td class="r">{row[1]:,}</td></tr>'
html += '</tbody></table>'
html += reason_table_dark('售后原因', as_shipped_reasons, as_shipped[0])

# 已出库售后仓库分布
html += '<h3 class="sub-title">已出库售后 — 仓库分布</h3>'
html += '<table class="data-table compact"><thead><tr><th>仓库</th><th class="r">售后单数</th><th class="r">退款金额</th></tr></thead><tbody>'
for row in as_shipped_warehouse:
    html += f'<tr><td>{row[0]}</td><td class="r">{row[1]:,}</td><td class="r" style="color:{RED}">{row[2]:,.2f}元</td></tr>'
html += '</tbody></table>'

# 审核不通过
html += f'''
<div class="alert-box">
<h3>审核不通过 — 已发货损失</h3>
<p>共 <b>{as_reject[0]:,}</b> 单售后审核不通过，已发货但客户申请被拒，ERP已收款 <b>{as_reject[1]:,.2f}元</b>，对应财务净额 <b>{as_reject[2]:,.2f}元</b>（货已出、款未退）</p>
</div>
<table class="data-table compact"><thead><tr><th>原因</th><th class="r">单数</th><th class="r">ERP收款</th><th class="r">财务净额</th></tr></thead><tbody>
'''
for row in as_reject_reasons:
    reason, cnt, erp, net = row[0], row[1], float(row[2] or 0), float(row[3] or 0)
    rlabel = reason if reason else '未分类'
    html += f'<tr><td>{rlabel}</td><td class="r">{cnt:,}</td><td class="r" style="color:{RED}">{erp:,.2f}元</td><td class="r" style="color:{RED}"><b>{net:,.2f}元</b></td></tr>'
html += f'<tr class="row-total"><td>合计</td><td class="r">{as_reject[0]:,}</td><td class="r" style="color:{RED}">{as_reject[1]:,.2f}元</td><td class="r" style="color:{RED}"><b>{as_reject[2]:,.2f}元</b></td></tr>'
html += '</tbody></table></div>'

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
.subsection {{ margin-bottom:16px; }}
.metric-grid {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }}
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
.row-new-date {{ border-top:2px solid {BORDER}; }}
.dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; vertical-align:middle; }}
.status-tag {{ font-weight:700; padding:2px 6px; border-radius:3px; background:rgba(255,255,255,0.05); }}
.wh-label {{ font-weight:600; color:{ACCENT}; margin:12px 0 4px; font-size:14px; }}
.dim {{ color:{TEXT_DIM}; font-size:11px; }}
.dim-text {{ font-size:12px; color:{TEXT_DIM}; }}
.alert-box {{ background:rgba(255,82,82,0.08); border:1px solid {RED}; border-left:3px solid {RED}; border-radius:6px; padding:12px 16px; margin:12px 0; }}
.alert-box h3 {{ margin:0 0 6px; color:{RED}; font-size:15px; }}
.alert-box p {{ margin:0; font-size:13px; color:{TEXT}; }}
</style>'''

# 把CSS插到HTML最前面
html = css + html
import sys
if '--html' in sys.argv:
    print(html)
    sys.exit(0)

import sys
if "--save" in sys.argv:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web/daily.html"), "w") as _f:
        _f.write(html)
    print("Saved web/daily.html")
    sys.exit(0)

if "--html" in sys.argv:
    sys.exit(0)
if "--email" in sys.argv:
    try:
        send_email(f'【服务器】店铺：榴愿时刻工厂店 {SEQ_STR}', html, EMAIL_TO_FULL)
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

# 保存本轮指标供下次对比
tracker.save('full_report', {
    'wc_cnt': wc_cnt, 'ws_cnt': ws_cnt, 'fn_cnt': fn_cnt,
    'as_total': as_total[0], 'as_total_amt': as_total[1],
    'ship_rate': ship_rate, 'as_rate_on_total': as_rate_on_total,
    'as_reject_cnt': as_reject[0], 'as_reject_net': as_reject[2],
    'fin_income': fin_total_income, 'fin_expense': fin_total_expense,
    'fin_net': fin_total_net, 'pending_cnt': pending_cnt,
    'settled_net': settled_net, 'pending_net': pending_net,
    'refund_ratio': refund_ratio,
})

import sys as _sys
_orig_excepthook = _sys.excepthook
def _global_excepthook(exc_type, exc_val, exc_tb):
    logger.exception("Unhandled exception", exc_info=(exc_type, exc_val, exc_tb))
    _orig_excepthook(exc_type, exc_val, exc_tb)
_sys.excepthook = _global_excepthook
