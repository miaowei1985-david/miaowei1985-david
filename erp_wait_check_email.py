#!/usr/bin/env python3
"""
【服务器】待审核订单 — 按仓库&发货时效明细 — 从数据库读取 → 邮件发送
"""
import json, os, sys
import smtplib
import sqlite3
from email.mime.text import MIMEText
from email.header import Header
from datetime import datetime
from collections import defaultdict
import erp_config
from erp_config import DB_PATH, EMAIL_FROM, send_email, setup_logger
setup_logger("wait_check", "/tmp/erp_wait_check.log")

EMAIL_TO_WAIT_CHECK = [
    "88187402@qq.com",
    "17821279335@163.com",
    "hb-champion@foxmail.com",
    "136941100@qq.com",
    "949547543@qq.com",
]

TIME_ORDER = ["24小时内", "24-48小时", "48-72小时", "72-96小时", "96-120小时", ">120小时"]

TIME_COLORS = {
    "24小时内": "#e74c3c",
    "24-48小时": "#e67e22",
    "48-72小时": "#f39c12",
    "72-96小时": "#3498db",
    "96-120小时": "#9b59b6",
    ">120小时": "#95a5a6",
}

def load_wait_check(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    rows = conn.execute('SELECT * FROM erp_wait_check').fetchall()
    conn.close()
    return [dict(r) for r in rows]

def classify_deadline(estimate_consign_time, now):
    if not estimate_consign_time:
        return ">120小时"
    dl = datetime.fromisoformat(estimate_consign_time.replace("T", " "))
    hours = (dl - now).total_seconds() / 3600
    if hours <= 24: return "24小时内"
    elif hours <= 48: return "24-48小时"
    elif hours <= 72: return "48-72小时"
    elif hours <= 96: return "72-96小时"
    elif hours <= 120: return "96-120小时"
    else: return ">120小时"

def extract_products(rows):
    products = defaultdict(int)
    for row in rows:
        raw = row.get("orderItemList", "")
        if not raw: continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(items, list):
                for item in items:
                    spu = item.get("spuName", "")
                    sku = item.get("skuName", "")
                    key = f"{spu} | {sku}" if spu and sku else spu or sku
                    if key:
                        products[key] += 1
        except: pass
    return dict(sorted(products.items(), key=lambda x: -x[1]))

def build_wait_check_detail_html(rows, now):
    html = '<h3 style="color:#2c3e50;border-bottom:2px solid #2c3e50;padding-bottom:5px;margin-top:20px;">【服务器】待审核订单 — 按仓库&发货时效明细</h3>'

    time_buckets = {tag: defaultdict(list) for tag in TIME_ORDER}
    for row in rows:
        tag = classify_deadline(row.get("estimateConsignTime", ""), now)
        warehouse = row.get("warehouseName", "") or "未知仓库"
        time_buckets[tag][warehouse].append(row)

    for tag in TIME_ORDER:
        warehouse_map = time_buckets[tag]
        if not warehouse_map:
            html += f'<p style="margin:10px 0 5px;color:{TIME_COLORS[tag]};font-weight:bold;font-size:14px;">{tag}: 无订单</p>'
            continue

        total_orders = sum(len(v) for v in warehouse_map.values())
        total_paid = sum(float(r.get("paid", 0) or 0) for rl in warehouse_map.values() for r in rl)
        html += f'<h4 style="color:{TIME_COLORS[tag]};margin:15px 0 8px;font-size:15px;">{tag} — 合计 {total_orders} 单 | 实收 {total_paid:,.0f} 元</h4>'

        for warehouse in sorted(warehouse_map.keys()):
            items = warehouse_map[warehouse]
            wh_paid = sum(float(r.get("paid", 0) or 0) for r in items)
            html += f'<p style="margin:8px 0 4px;font-weight:bold;font-size:13px;color:#555;">📦 {warehouse} ({len(items)} 单 | {wh_paid:,.0f} 元)</p>'
            html += '<table style="border-collapse:collapse;width:100%;font-size:12px;font-family:monospace;margin-bottom:8px;">'
            html += '<tr style="background:#f9f9f9;"><th style="padding:4px 8px;border:1px solid #ddd;text-align:left;">品名规格</th>'
            html += '<th style="padding:4px 8px;border:1px solid #ddd;text-align:right;width:70px;">数量</th></tr>'
            products = extract_products(items)
            for name, cnt in products.items():
                html += f'<tr><td style="padding:3px 8px;border:1px solid #ddd;">{name}</td>'
                html += f'<td style="padding:3px 8px;border:1px solid #ddd;text-align:right;">{cnt}</td></tr>'
            html += f'<tr style="background:#f5f5f5;font-weight:bold;"><td style="padding:3px 8px;border:1px solid #ddd;text-align:right;">合计</td>'
            html += f'<td style="padding:3px 8px;border:1px solid #ddd;text-align:right;">{sum(products.values())}</td></tr>'
            html += '</table>'

    return html

def send_email_fn(html_body):
    from datetime import datetime as _dt
    _skey = "/tmp/seq_waitcheck_" + _dt.now().strftime("%Y%m%d")
    if os.path.exists(_skey):
        _seq = int(open(_skey).read().strip()) + 1
    else:
        _seq = 1
    open(_skey, "w").write(str(_seq))
    send_email(f'【服务器】订单截止时间倒计时 {_dt.now().strftime("%Y%m%d-")}{_seq}', html_body, EMAIL_TO_WAIT_CHECK)

def main():
    if not os.path.exists(DB_PATH):
        print(f"❌ 数据库不存在: {DB_PATH}，请先运行 erp_fetch.py 拉取数据")
        sys.exit(1)

    import sys; sys.stderr.write("📊 从数据库加载待审核数据...")
    rows = load_wait_check(DB_PATH)
    now = datetime.now()
    print(f"  待审核: {len(rows)} 条")

    import sys; sys.stderr.write("📊 生成HTML...")
    html = f'''<html><body style="margin:20px;font-family:sans-serif;">
    <h2 style="color:#2c3e50;">【服务器】待审核订单 — 按仓库&发货时效明细</h2>
    <p style="font-size:13px;color:#888;">生成时间: {now.strftime("%Y-%m-%d %H:%M:%S")}</p>
    '''
    html += build_wait_check_detail_html(rows, now)
    html += '</body></html>'

    import sys
    if "--html" in sys.argv:
        print(html)
        sys.exit(0)

    if "--email" in sys.argv:
        send_email_fn(html)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Script failed: %s", e)
        raise
