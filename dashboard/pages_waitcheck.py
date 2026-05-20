#!/usr/bin/env python3
"""待审核订单页面"""
import os, json, sqlite3, time
from datetime import datetime
from collections import defaultdict

from erp_config import DB_PATH
from dashboard import page, cached_page

def render_waitcheck():
    import sqlite3
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
