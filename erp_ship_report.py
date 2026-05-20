#!/usr/bin/env python3
"""
旺店通 ERP 发货日报 — 从数据库读取数据 → 分析 → 邮件发送
"""
import json
import os
import smtplib
import sqlite3
import sys
from datetime import datetime
from email.mime.text import MIMEText
from email.header import Header
from collections import defaultdict
import logging
logging.basicConfig(filename="/tmp/erp_ship_report.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ===== 配置 =====
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "erp_all.db")

EMAIL_FROM = "88187402@qq.com"
EMAIL_TO = [
    "88187402@qq.com",
    "17821279335@163.com",
    "hb-champion@foxmail.com",
    "136941100@qq.com",
    "903470571@qq.com",
    "179710675@qq.com",
    "32507586@qq.com",
    "michealzheng2000@gmail.com",
    "592340474@qq.com",
    "398307955@qq.com",
]
EMAIL_AUTH = "uqauegwesrrybibe"
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465

# ===== 从数据库读取 =====
def load_from_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # 待审核
    rows = conn.execute('SELECT * FROM erp_wait_check').fetchall()
    wait_check = [dict(r) for r in rows]

    # 待发货
    rows = conn.execute('SELECT * FROM erp_wait_send_self').fetchall()
    wait_send = [dict(r) for r in rows]

    # 已完成
    rows = conn.execute('SELECT * FROM erp_finished').fetchall()
    finished = [dict(r) for r in rows]

    conn.close()
    return wait_check, wait_send, finished

# ===== 内存分析 =====
def get_deadlines(rows):
    now = datetime.now()
    groups = {}
    total_paid = 0
    total_cnt = len(rows)
    for row in rows:
        paid = row.get('paid')
        try:
            paid_val = float(paid) if paid else 0
        except:
            paid_val = 0
        total_paid += paid_val
        dl = row.get('estimateConsignTime', '')
        if dl:
            if dl not in groups:
                groups[dl] = [0, 0]
            groups[dl][0] += 1
            groups[dl][1] += paid_val

    deadlines = []
    overdue = urgent = normal = 0
    for deadline in sorted(groups.keys()):
        cnt, row_paid = groups[deadline]
        dl = datetime.fromisoformat(deadline.replace('T', ' '))
        hours = (dl - now).total_seconds() / 3600
        if hours < 0:
            status = '已超时'; overdue += cnt
        elif hours < 12:
            status = '紧急'; urgent += cnt
        else:
            status = '正常'; normal += cnt
        deadlines.append((deadline, cnt, row_paid, hours, status))
    return deadlines, total_cnt, total_paid, overdue, urgent, normal, now

def parse_product_info(rows):
    products = {}
    for row in rows:
        raw = row.get('orderItemList', '')
        if not raw:
            continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(items, list):
                for item in items:
                    spu = item.get('spuName', '')
                    sku = item.get('skuName', '')
                    if spu:
                        key = f"{spu}|{sku}"
                        if key not in products:
                            products[key] = {'spu': spu, 'sku': sku, 'count': 0}
                        products[key]['count'] += 1
        except:
            pass
    return sorted(products.values(), key=lambda x: -x['count'])

def get_logistics_status(rows):
    groups = {}
    total_paid = 0
    total_cnt = len(rows)
    for row in rows:
        paid = row.get('paid')
        try:
            paid_val = float(paid) if paid else 0
        except:
            paid_val = 0
        total_paid += paid_val
        status = row.get('traceStatusMsg', '') or ''
        if status not in groups:
            groups[status] = [0, 0]
        groups[status][0] += 1
        groups[status][1] += paid_val
    result = sorted(groups.items(), key=lambda x: -x[1][0])
    return [(s, c, m) for s, (c, m) in result], total_cnt, total_paid

# ===== HTML 格式化 =====
def format_logistics_html(rows, total_cnt, total_money):
    status_colors = {
        '已签收': '#27ae60', '运输中': '#3498db', '待揽件': '#f39c12',
        '问题件': '#e74c3c', '退件': '#e67e22',
    }
    html = '<h3 style="color:#333;border-bottom:2px solid #ddd;padding-bottom:5px;">已完结订单物流状态</h3>'
    html += '<table style="border-collapse:collapse;width:100%;font-size:13px;font-family:monospace;">'
    html += '<tr style="background:#f5f5f5;"><th style="padding:6px 8px;border:1px solid #ddd;text-align:left;">物流状态</th>'
    html += '<th style="padding:6px 8px;border:1px solid #ddd;text-align:right;">订单数</th>'
    html += '<th style="padding:6px 8px;border:1px solid #ddd;text-align:right;">金额</th>'
    html += '<th style="padding:6px 8px;border:1px solid #ddd;text-align:right;">占比</th></tr>'
    for status, cnt, money in rows:
        status_label = status if status else '未知'
        color = status_colors.get(status_label, '#95a5a6')
        pct = f'{cnt / total_cnt * 100:.1f}%' if total_cnt else '0%'
        html += f'<tr><td style="padding:5px 8px;border:1px solid #ddd;color:{color};font-weight:bold;">{status_label}</td>'
        html += f'<td style="padding:5px 8px;border:1px solid #ddd;text-align:right;">{cnt}</td>'
        html += f'<td style="padding:5px 8px;border:1px solid #ddd;text-align:right;">{money:,.0f}元</td>'
        html += f'<td style="padding:5px 8px;border:1px solid #ddd;text-align:right;">{pct}</td></tr>'
    html += f'</table>'
    html += f'<p style="margin-top:5px;font-size:13px;color:#555;">合计: <b>{total_cnt}</b> 单 | 实收 <b>{total_money:,.0f}</b> 元</p>'
    return html

def format_table_html(deadlines, total_cnt, total_money, overdue, urgent, normal, title, products=None):
    html = f'<h3 style="color:#333;border-bottom:2px solid #ddd;padding-bottom:5px;">{title}</h3>'
    html += f'<table style="border-collapse:collapse;width:100%;font-size:13px;font-family:monospace;">'
    html += '<tr style="background:#f5f5f5;"><th style="padding:6px 8px;border:1px solid #ddd;text-align:left;">最晚发货时间</th>'
    html += '<th style="padding:6px 8px;border:1px solid #ddd;text-align:right;">订单数</th>'
    html += '<th style="padding:6px 8px;border:1px solid #ddd;text-align:right;">金额</th>'
    html += '<th style="padding:6px 8px;border:1px solid #ddd;text-align:right;">剩余时间</th>'
    html += '<th style="padding:6px 8px;border:1px solid #ddd;text-align:center;">状态</th></tr>'
    for dl, cnt, money, hours, status in deadlines:
        color = '#e74c3c' if status == '已超时' else ('#f39c12' if status == '紧急' else '#27ae60')
        html += f'<tr><td style="padding:5px 8px;border:1px solid #ddd;">{dl}</td>'
        html += f'<td style="padding:5px 8px;border:1px solid #ddd;text-align:right;">{cnt}</td>'
        html += f'<td style="padding:5px 8px;border:1px solid #ddd;text-align:right;">{money:,.0f}元</td>'
        html += f'<td style="padding:5px 8px;border:1px solid #ddd;text-align:right;">{hours:+.1f}小时</td>'
        html += f'<td style="padding:5px 8px;border:1px solid #ddd;text-align:center;color:{color};font-weight:bold;">{status}</td></tr>'
    html += f'</table>'
    html += f'<p style="margin-top:5px;font-size:13px;color:#555;">合计: <b>{total_cnt}</b> 单 | 实收 <b>{total_money:,.0f}</b> 元 | 超时 <b>{overdue}</b> | 紧急 <b>{urgent}</b> | 正常 <b>{normal}</b></p>'

    if products:
        html += f'<h4 style="color:#333;margin-top:15px;border-bottom:1px solid #ddd;padding-bottom:5px;">品名规格</h4>'
        html += '<table style="border-collapse:collapse;width:100%;font-size:12px;font-family:monospace;">'
        html += '<tr style="background:#f9f9f9;"><th style="padding:5px 8px;border:1px solid #ddd;text-align:left;">品名</th>'
        html += '<th style="padding:5px 8px;border:1px solid #ddd;text-align:left;">规格</th>'
        html += '<th style="padding:5px 8px;border:1px solid #ddd;text-align:right;">出现次数</th></tr>'
        for p in products:
            html += f'<tr><td style="padding:4px 8px;border:1px solid #ddd;">{p["spu"]}</td>'
            html += f'<td style="padding:4px 8px;border:1px solid #ddd;">{p["sku"]}</td>'
            html += f'<td style="padding:4px 8px;border:1px solid #ddd;text-align:right;">{p["count"]}</td></tr>'
        html += '</table>'

    return html

# ===== 邮件发送 =====
def send_email(html_body):
    msg = MIMEText(html_body, 'html', 'utf-8')
    msg['From'] = Header('88187402@qq.com')
    msg['To'] = ', '.join(EMAIL_TO)
    msg['Subject'] = Header(f'旺店通订单发货日报 {datetime.now().strftime("%Y-%m-%d")}', 'utf-8')
    try:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        server.login(EMAIL_FROM, EMAIL_AUTH)
        server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        server.quit()
        print("✅ 邮件发送成功")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")

def main():
    if not os.path.exists(DB_PATH):
        print(f"❌ 数据库不存在: {DB_PATH}，请先运行 erp_fetch.py 拉取数据")
        sys.exit(1)

    print("📊 从数据库加载数据...")
    wait_check, wait_send, finished = load_from_db(DB_PATH)
    print(f"  待审核: {len(wait_check)} 条 | 待发货: {len(wait_send)} 条 | 已完成: {len(finished)} 条")

    # 分析
    deadlines1, t1_cnt, t1_total, overdue1, urgent1, normal1, now = get_deadlines(wait_check)
    products1 = parse_product_info(wait_check)

    deadlines2, t2_cnt, t2_total, overdue2, urgent2, normal2, _ = get_deadlines(wait_send)
    products2 = parse_product_info(wait_send)

    log_rows, log_cnt, log_total = get_logistics_status(finished)

    # 生成HTML
    html = f'<h2 style="color:#2c3e50;">旺店通订单发货日报</h2>'
    html += f'<p style="font-size:13px;color:#888;">生成时间: {now.strftime("%Y-%m-%d %H:%M:%S")}</p>'
    html += format_table_html(deadlines1, t1_cnt, t1_total, overdue1, urgent1, normal1, "待审核订单", products1)
    html += format_table_html(deadlines2, t2_cnt, t2_total, overdue2, urgent2, normal2, "待发货订单", products2)
    html += format_logistics_html(log_rows, log_cnt, log_total)

    print("📧 发送邮件...")
    send_email(html)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Script failed: %s", e)
        raise
