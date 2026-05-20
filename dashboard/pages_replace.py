#!/usr/bin/env python3
"""换单补单页面"""
import os, json, sqlite3
from datetime import datetime

from erp_config import DB_PATH, SHOP_NAME
from dashboard import page

def render_replaceorder():
    """换单补单：已发货退单未签收"""
    import sqlite3
    import json
    from datetime import datetime

    conn = sqlite3.connect(DB_PATH, timeout=30.0)

    orders = conn.execute("""
        SELECT
            f.srcTids,
            f.logisticsNo,
            f.goodsCount,
            f.orderItemList,
            t.operate_desc,
            f.shopName,
            f.tradeTime,
            f.refundAmount,
            f.paid,
            f.estimateConsignTime
        FROM erp_finished f
        INNER JOIN (
            SELECT logistics_no, MAX(operate_time) as max_time
            FROM logistics_trace
            GROUP BY logistics_no
        ) latest ON f.logisticsNo = latest.logistics_no
        INNER JOIN logistics_trace t
            ON f.logisticsNo = t.logistics_no
            AND t.operate_time = latest.max_time
        WHERE f.refundStatusText = '全部退款'
          AND t.operate_desc != '已签收'
          AND f.logisticsNo IS NOT NULL
          AND f.logisticsNo != ''
        ORDER BY f.tradeTime DESC
    """).fetchall()

    total = len(orders)
    status_count = {}
    for row in orders:
        st = row[4]
        status_count[st] = status_count.get(st, 0) + 1

    rows_html = ""
    for i, (srcTids, logisticsNo, goodsCount, orderItemList, logisticsStatus, shopName, tradeTime, refundAmount, paid, estimateConsignTime) in enumerate(orders, 1):
        items = []
        try:
            item_list = json.loads(orderItemList) if orderItemList else []
            for item in item_list:
                spu = item.get("spuName", "")
                sku = item.get("skuName", "")
                num = item.get("skuNum", "1")
                spec = ""
                if sku:
                    parts = sku.split(";", 1)
                    spec = parts[1] if len(parts) > 1 else sku
                elif spu:
                    spec = spu
                else:
                    spec = "-"
                items.append(f"{spec} x{num}")
        except Exception:
            items = ["解析失败"]
        product_str = "<br>".join(items) if items else "-"

        status_color = {
            "运输中": "#3b82f6",
            "派送中": "#a855f7",
            "其它": "#8b949e",
            "问题件": "#f85149",
            "拒收": "#f97316",
            "待揽件": "#d29922",
        }.get(logisticsStatus, "#8b949e")

        rows_html += f"""<tr>
            <td>{i}</td>
            <td style="color:#fff;font-weight:600;">{product_str}</td>
            <td style="text-align:center;">{goodsCount}</td>
            <td><code style="background:#161b22;padding:2px 6px;border-radius:4px;font-size:12px;">{srcTids}</code></td>
            <td><code style="background:#161b22;padding:2px 6px;border-radius:4px;font-size:12px;">{logisticsNo}</code></td>
            <td><span style="color:{status_color};font-weight:600;">{logisticsStatus}</span></td>
            <td style="color:#8b949e;font-size:12px;">{shopName}</td>
            <td style="color:#d29922;font-size:12px;font-weight:600;">{estimateConsignTime[:16] if estimateConsignTime else '-'}</td>
            <td style="color:#8b949e;font-size:12px;">{tradeTime[:16] if tradeTime else '-'}</td>
        </tr>"""

    conn.close()

    status_summary = ""
    for st, cnt in sorted(status_count.items(), key=lambda x: -x[1]):
        color = {"运输中": "#3b82f6", "派送中": "#a855f7", "其它": "#8b949e", "问题件": "#f85149", "拒收": "#f97316", "待揽件": "#d29922"}.get(st, "#8b949e")
        status_summary += f'<span style="color:{color};margin-right:16px;font-weight:600;">{st}: {cnt}</span>'

    summary_div = f"""
<div style="margin-bottom:20px;padding:16px;background:#161b22;border:1px solid #30363d;border-radius:10px;">
    <div style="font-size:14px;color:#8b949e;margin-bottom:8px;">已发货退单未签收订单汇总</div>
    <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
        <span style="color:#f85149;font-size:24px;font-weight:700;">{total}</span>
        <span style="color:#8b949e;font-size:13px;">单</span>
        <span style="color:#30363d;">|</span>
        {status_summary}
    </div>
</div>"""

    table_html = f"""
<div style="overflow-x:auto;border-radius:10px;border:1px solid #30363d;">
<table style="width:100%;border-collapse:collapse;font-size:13px;">
    <thead>
        <tr style="background:#161b22;color:#8b949e;text-align:left;">
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">#</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">品名 &amp; 规格</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;text-align:center;">数量</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">平台订单号</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">快递单号</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">物流状态</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">店铺</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">最晚发货时间</th>
            <th style="padding:10px 12px;border-bottom:1px solid #30363d;">下单时间</th>
        </tr>
    </thead>
    <tbody>
        {rows_html if rows_html else "<tr><td colspan='9' style='padding:20px;text-align:center;color:#484f58;'>暂无数据</td></tr>"}
    </tbody>
</table>
</div>"""

    body = summary_div + table_html
    return page("换单补单", "replaceorder", body)
