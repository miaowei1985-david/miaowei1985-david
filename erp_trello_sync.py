#!/usr/bin/env python3
"""
ERP Trello Sync — 将 ERP 订单数据同步到 Trello 看板
栏目：待审核 → 待发货 → 已揽件 → 运输中 → 派送中 → 已签收 → 异常件
每个栏目顶部有一张 AI 总结卡片，下方是订单卡片。
"""

import sqlite3
import json
import urllib.request
import urllib.parse
import time
from datetime import datetime

# === 配置 ===
TRELLO_KEY = os.environ.get("TRELLO_KEY", "")
TRELLO_TOKEN = os.environ.get("TRELLO_TOKEN", "")
BOARD_ID = "6a0bff4f9449877e387214a7"
DB_PATH = "/Users/macmini4pro/pdd/Claudecode/erp_all.db"
SHOP = "榴愿时刻工厂店"

COLUMN_LIMITS = {
    "待审核": 25, "待发货": 25, "已揽件": 25,
    "运输中": 25, "派送中": 30, "已签收": 25,
    "异常件": 9999,
}
COLUMN_ORDER = ["待审核", "待发货", "已揽件", "运输中", "派送中", "已签收", "异常件"]

# === SQL 片段 ===
# 从物流表 JOIN 订单表获取完整信息的列定义（8 列）
ORDER_COLS = """
    COALESCE(ao.tradeNo, wc.tradeNo, ws.tradeNo),
    COALESCE(ao.warehouseName, wc.warehouseName, ws.warehouseName),
    COALESCE(ao.receiverProvinceName, wc.receiverProvinceName, ws.receiverProvinceName),
    COALESCE(ao.receiverCityName, wc.receiverCityName, ws.receiverCityName),
    COALESCE(ao.receiverDistrictName, wc.receiverDistrictName, ws.receiverDistrictName),
    COALESCE(ao.realAmount, wc.realAmount, ws.realAmount),
    COALESCE(ao.orderItemList, wc.orderItemList, ws.orderItemList),
    COALESCE(ao.estimateConsignTime, wc.estimateConsignTime, ws.estimateConsignTime)
"""

ORDER_JOIN = """
    FROM logistics_trace lt
    INNER JOIN (
        SELECT logistics_no, MAX(operate_time) as max_time
        FROM logistics_trace GROUP BY logistics_no
    ) latest ON lt.logistics_no = latest.logistics_no AND lt.operate_time = latest.max_time
    LEFT JOIN erp_all_orders ao ON lt.src_tid = ao.srcTids
    LEFT JOIN erp_wait_check wc ON lt.src_tid = wc.srcTids
    LEFT JOIN erp_wait_send_self ws ON lt.src_tid = ws.srcTids
"""


def trello_api(method, path, data=None, retries=3):
    url = f"https://api.trello.com/1/{path}?key={TRELLO_KEY}&token={TRELLO_TOKEN}"
    if data:
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(url, data=body, method=method)
    else:
        req = urllib.request.Request(url, method=method)
    for attempt in range(retries):
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            return json.loads(resp.read())
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def get_spu_name(item_list):
    try:
        items = json.loads(item_list) if item_list else []
        return ", ".join([i.get("spuName", "") for i in items if i.get("spuName")][:2])
    except Exception:
        return ""


def get_sku_spec(item_list):
    try:
        items = json.loads(item_list) if item_list else []
        return ", ".join([i.get("skuName", "") for i in items if i.get("skuName")][:2])
    except Exception:
        return ""


def get_deadline_date(ect):
    if ect:
        try:
            return ect[5:10]
        except Exception:
            pass
    return ""


def make_card_name(spu, sku, dl):
    parts = []
    if spu: parts.append(spu)
    if sku: parts.append(sku)
    name = " | ".join(parts) or "未知商品"
    if dl: name += f" | 预计发货: {dl}"
    return name[:128]


def make_card_desc(order_no, wh, prov, city, dist, amount, **extra):
    lines = []
    if order_no: lines.append(f"**单号**: {order_no}")
    if wh: lines.append(f"**仓库**: {wh}")
    if prov: lines.append(f"**收货**: {prov}{city or ''}{dist or ''}")
    if amount: lines.append(f"**金额**: ¥{amount}")
    for k, v in extra.items():
        lines.append(f"**{k}**: {v}")
    return "\n".join(lines)


def get_list_ids():
    lists = trello_api("GET", f"boards/{BOARD_ID}/lists")
    result = {}
    for l in lists:
        for name in COLUMN_ORDER:
            if l["name"] == name or l["name"].startswith(name + " "):
                result[name] = l["id"]
    return result


def delete_all_cards():
    cards = trello_api("GET", f"boards/{BOARD_ID}/cards")
    print(f"删除 {len(cards)} 张旧卡片...")
    for c in cards:
        try:
            trello_api("DELETE", f"cards/{c['id']}")
        except Exception:
            pass
    print("清除完成")


def create_card_top(list_id, name, desc):
    return trello_api("POST", "cards", {"idList": list_id, "name": name, "desc": desc, "pos": "top"})


def create_cards_batch(list_id, col, rows, title_fn, desc_fn, limit):
    cnt = 0
    for row in rows:
        if cnt >= limit:
            print(f"  {col}: 已达上限 {limit}")
            break
        try:
            name = title_fn(row)
            desc = desc_fn(row)
            trello_api("POST", "cards", {"idList": list_id, "name": name, "desc": desc})
            cnt += 1
        except Exception as e:
            try:
                name_preview = title_fn(row)
            except Exception:
                name_preview = f"row_idx={row}"
            print(f"  创建失败: {name_preview[:60]}... — {e}")
        if cnt % 10 == 0 and cnt > 0:
            time.sleep(1)
            print(f"  {col}: 已创建 {cnt}/{limit}...")
    return cnt


# === AI 分析 ===
def analyze_problem(trace_info):
    if not trace_info:
        return "暂无物流信息，建议联系快递公司核实运单号是否正确。"
    info = trace_info.lower()
    analyses = []
    rules = [
        (["破损", "损坏", "破"], "包裹破损：建议联系客户确认收货情况，如确实破损可安排补发或理赔。"),
        (["丢失", "遗失", "找不到"], "包裹丢失：立即联系快递公司报案，同时安排补发商品给客户。"),
        (["拒收", "退回", "退件"], "客户拒收/退回：联系客户确认拒收原因，安排退款或重新发货。"),
        (["延误", "滞留", "积压"], "物流延误/滞留：联系快递公司催促派件，同时告知客户预计延误时间。"),
        (["异常", "无法", "不能"], "物流异常：建议核实地址是否正确，联系收件人确认能否正常收件。"),
        (["签收", "代签"], "非本人签收：联系客户确认是否为家人/代收点代签，如无问题则无需处理。"),
        (["空包", "无货"], "包裹异常（空包/无货）：核实仓库出库记录，如确认漏发则安排补发。"),
        (["变质", "腐烂", "坏"], "商品变质：生鲜类产品常见问题，建议核实运输温度，安排补发或退款。"),
    ]
    for keywords, msg in rules:
        if any(kw in info for kw in keywords):
            analyses.append(msg)
    if not analyses:
        analyses.append(
            f"问题描述：{trace_info}\n建议：\n"
            "1. 联系快递公司核实具体情况\n"
            "2. 查看物流详细轨迹确认问题环节\n"
            "3. 必要时联系客户协商解决方案"
        )
    return "\n".join(analyses)


# === 物流列索引（SELECT lt.logistics_no, lt.operate_time, lt.src_tid + ORDER_COLS 8列 = 共12列）===
# 0: logistics_no
# 1: operate_time
# 2: src_tid
# 3: tradeNo
# 4: warehouseName
# 5: receiverProvinceName
# 6: receiverCityName
# 7: receiverDistrictName
# 8: realAmount
# 9: orderItemList
# 10: estimateConsignTime
# 11: （无，上面只有8个COALESCE，所以索引 3-10 才是对的）
# 等等，让我重新算：
# SELECT lt.logistics_no(0), lt.operate_time(1), lt.src_tid(2),
#   COALESCE tradeNo(3), COALESCE warehouseName(4),
#   COALESCE receiverProvinceName(5), COALESCE receiverCityName(6),
#   COALESCE receiverDistrictName(7), COALESCE realAmount(8),
#   COALESCE orderItemList(9), COALESCE estimateConsignTime(10)
# 总共 11 列！

def logistics_title_fn(r):
    return make_card_name(get_spu_name(r[9]), get_sku_spec(r[9]), get_deadline_date(r[10]))


def logistics_desc_fn(r, col_name):
    return make_card_desc(r[3] or r[0], r[4], r[5], r[6], r[7], r[8],
                          **{"运单号": r[0], "订单号": r[3] or "", "节点": f"{col_name} | {r[1]}"})


def generate_summary(col, rows, col_type):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = len(rows)
    lines = [f"**{col} · 数据摘要**", f"_更新时间: {now}_", ""]

    if col_type == "order":
        warehouses, provinces, deadlines = {}, {}, {"24h内": 0, "24-48h": 0, "48-72h": 0, ">72h": 0}
        total_amount = 0.0
        for r in rows:
            wh = (r[1] or "").strip() or "未知"
            warehouses[wh] = warehouses.get(wh, 0) + 1
            prov = r[2] or ""
            if prov: provinces[prov] = provinces.get(prov, 0) + 1
            try:
                total_amount += float(r[6] or 0)
            except Exception:
                pass
            dl = r[7] or ""
            if dl:
                try:
                    diff_h = (datetime.strptime(dl[:19], "%Y-%m-%dT%H:%M:%S") - datetime.now()).total_seconds() / 3600
                    if diff_h <= 24:
                        deadlines["24h内"] += 1
                    elif diff_h <= 48:
                        deadlines["24-48h"] += 1
                    elif diff_h <= 72:
                        deadlines["48-72h"] += 1
                    else:
                        deadlines[">72h"] += 1
                except Exception:
                    deadlines[">72h"] += 1

        lines.append(f"**共 {total} 笔订单，总金额 ¥{total_amount:.2f}**")
        lines.append("")
        if col == "待审核":
            urgent = deadlines.get("24h内", 0) + deadlines.get("24-48h", 0)
            dist = " | ".join(f"{k}: {v}" for k, v in deadlines.items() if v > 0)
            lines.append(f"**⚠️ 时效分布**: {dist}")
            if urgent > 0:
                lines.append(f"**  紧急**: 有 {urgent} 笔订单需在 48 小时内审核发货")
        lines.append("")
        lines.append("**仓库分布**:")
        for wh, cnt in sorted(warehouses.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  - {wh}: {cnt} 笔")
        if provinces:
            lines.append("\n**省份 TOP5**:")
            for p, c in sorted(provinces.items(), key=lambda x: -x[1])[:5]:
                lines.append(f"  - {p}: {c} 笔")

    elif col_type == "logistics":
        warehouses, provinces = {}, {}
        total_amount = 0.0
        for r in rows:
            wh = (r[4] or "").strip() or "未知"
            warehouses[wh] = warehouses.get(wh, 0) + 1
            prov = r[5] or ""
            if prov:
                provinces[prov] = provinces.get(prov, 0) + 1
            try:
                total_amount += float(r[8] or 0)
            except Exception:
                pass
        lines.append(f"**共 {total} 票，总金额 ¥{total_amount:.2f}**")
        lines.append("")
        if col == "已签收":
            lines.append("**当前节点**: 已签收")
        elif col == "运输中":
            lines.append("**当前节点**: 运输途中")
        elif col == "派送中":
            lines.append("**当前节点**: 末端派送中，即将签收")
        elif col == "已揽件":
            lines.append("**当前节点**: 已揽件，等待运输")
        lines.append("")
        lines.append("**仓库分布**:")
        for wh, cnt in sorted(warehouses.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  - {wh}: {cnt} 票")
        if provinces:
            lines.append("\n**收货省份 TOP5**:")
            for p, c in sorted(provinces.items(), key=lambda x: -x[1])[:5]:
                lines.append(f"  - {p}: {c} 票")

    elif col_type == "exception":
        problem_count, timeout_count, warehouses, reasons = 0, 0, {}, {}
        for r in rows:
            is_timeout = (r[0] or "").startswith("TO_")
            if is_timeout:
                timeout_count += 1
            else:
                problem_count += 1
            wh = (r[5] or "").strip() or "未知"
            warehouses[wh] = warehouses.get(wh, 0) + 1
            trace = r[3] or ""
            if trace:
                for kw in ["破损", "丢失", "拒收", "延误", "滞留", "异常", "变质", "空包"]:
                    if kw in trace:
                        reasons[kw] = reasons.get(kw, 0) + 1
                        break
        lines.append(f"**共 {total} 件异常**")
        lines.append(f"  - 问题件: {problem_count} 件")
        lines.append(f"  - 超时未签收: {timeout_count} 件")
        lines.append("")
        if reasons:
            lines.append("**问题原因分布**:")
            for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
                lines.append(f"  - {reason}: {cnt} 件")
        lines.append("")
        lines.append("**仓库分布**:")
        for wh, cnt in sorted(warehouses.items(), key=lambda x: -x[1])[:5]:
            lines.append(f"  - {wh}: {cnt} 件")
        lines.append("\n**💡 建议**: 优先处理问题件，超时件联系快递公司核实是否丢件")

    return "\n".join(lines)


def main():
    print(f"=== ERP → Trello 看板同步 ===\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()

    # 1. 清空
    delete_all_cards()
    old_lists = trello_api("GET", f"boards/{BOARD_ID}/lists")
    for l in old_lists:
        try:
            trello_api("PUT", f"lists/{l['id']}/closed", {"value": "true"})
        except Exception:
            pass

    # 2. 创建栏目
    list_ids = {}
    for name in COLUMN_ORDER:
        r = trello_api("POST", "lists", {"name": name, "idBoard": BOARD_ID})
        list_ids[name] = r["id"]
        print(f"  栏目: {name}")
        time.sleep(0.3)

    total_cards = 0
    counts = {}

    # === 待审核 ===
    rows = c.execute(
        "SELECT tradeNo, warehouseName, receiverProvinceName, receiverCityName, receiverDistrictName, "
        "orderItemList, realAmount, estimateConsignTime, payTime FROM erp_wait_check "
        "WHERE shopName = ? ORDER BY payTime DESC LIMIT ?",
        (SHOP, COLUMN_LIMITS["待审核"])
    ).fetchall()
    print(f"\n--- 待审核 ({len(rows)}) ---")
    cnt = create_cards_batch(list_ids["待审核"], "待审核", rows,
                             lambda r: make_card_name(get_spu_name(r[5]), get_sku_spec(r[5]), get_deadline_date(r[7])),
                             lambda r: make_card_desc(r[0], r[1], r[2], r[3], r[4], r[6], **{"下单": r[8]}),
                             COLUMN_LIMITS["待审核"])
    total_cards += cnt
    counts["待审核"] = cnt + 1
    create_card_top(list_ids["待审核"], "📊 数据摘要", generate_summary("待审核", rows, "order"))

    # === 待发货 ===
    rows = c.execute(
        "SELECT tradeNo, warehouseName, receiverProvinceName, receiverCityName, receiverDistrictName, "
        "orderItemList, realAmount, estimateConsignTime, payTime FROM erp_wait_send_self "
        "WHERE shopName = ? ORDER BY payTime DESC LIMIT ?",
        (SHOP, COLUMN_LIMITS["待发货"])
    ).fetchall()
    print(f"\n--- 待发货 ({len(rows)}) ---")
    cnt = create_cards_batch(list_ids["待发货"], "待发货", rows,
                             lambda r: make_card_name(get_spu_name(r[5]), get_sku_spec(r[5]), get_deadline_date(r[7])),
                             lambda r: make_card_desc(r[0], r[1], r[2], r[3], r[4], r[6], **{"下单": r[8]}),
                             COLUMN_LIMITS["待发货"])
    total_cards += cnt
    counts["待发货"] = cnt + 1
    create_card_top(list_ids["待发货"], "📊 数据摘要", generate_summary("待发货", rows, "order"))

    # === 物流状态 (已揽件/运输中/派送中/已签收) ===
    logistics_sql = f"SELECT lt.logistics_no, lt.operate_time, lt.src_tid, {ORDER_COLS} {ORDER_JOIN} WHERE lt.operate_desc = ? ORDER BY lt.operate_time DESC LIMIT ?"

    for col in ["已揽件", "运输中", "派送中", "已签收"]:
        limit = COLUMN_LIMITS[col]
        rows = c.execute(logistics_sql, (col, limit)).fetchall()
        print(f"\n--- {col} ({len(rows)}) ---")
        if rows:
            print(f"  示例列数: {len(rows[0])}")

        cnt = create_cards_batch(list_ids[col], col, rows,
                                 lambda r: logistics_title_fn(r),
                                 lambda r, _c=col: logistics_desc_fn(r, _c),
                                 limit)
        total_cards += cnt
        counts[col] = cnt + 1
        create_card_top(list_ids[col], f"📊 {col} · 摘要", generate_summary(col, rows, "logistics"))
        time.sleep(0.5)

    # === 异常件 ===
    print(f"\n--- 异常件 ---")
    exc_rows, exc_count = [], 0

    # 问题件全量
    p_sql = f"SELECT lt.logistics_no, lt.operate_time, lt.src_tid, lt.trace_info, {ORDER_COLS} {ORDER_JOIN} WHERE lt.operate_desc = '问题件' ORDER BY lt.operate_time DESC"
    p_rows = c.execute(p_sql).fetchall()
    print(f"  问题件: {len(p_rows)}")

    # 12列: logistics_no(0), operate_time(1), src_tid(2), trace_info(3),
    #        tradeNo(4), warehouseName(5), receiverProvinceName(6),
    #        receiverCityName(7), receiverDistrictName(8), realAmount(9),
    #        orderItemList(10), estimateConsignTime(11)
    for r in p_rows:
        logistics_no, op_time, src_tid, trace_info, tradeNo, wh, prov, city, dist, amount, items, deadline = r
        title = make_card_name(get_spu_name(items), get_sku_spec(items), get_deadline_date(deadline))
        desc = (
            f"**运单号**: {logistics_no}\n**订单号**: {tradeNo}\n**仓库**: {wh or '未知'}\n"
            f"**收货**: {prov or ''}{city or ''}{dist or ''}\n**金额**: ¥{amount}\n"
            f"**问题时间**: {op_time}\n\n--- 实际原因 ---\n{trace_info or '未知'}\n\n"
            f"--- AI 分析 ---\n{analyze_problem(trace_info)}"
        )
        exc_rows.append((r, title, desc))
        exc_count += 1
        if exc_count % 10 == 0:
            time.sleep(1)

    # 超时预警 - 已揽件超过72小时未签收
    # 两步法避免大表 JOIN
    print(f"  查询超时件...")
    signed_nos = set(row[0] for row in c.execute(
        "SELECT DISTINCT logistics_no FROM logistics_trace WHERE operate_desc = '已签收'"
    ).fetchall())
    print(f"  已签收运单: {len(signed_nos)}")

    t_rows_raw = c.execute("""
        SELECT logistics_no, operate_time, src_tid
        FROM logistics_trace
        WHERE operate_desc = '已揽件'
        AND operate_time < datetime('now', '-72 hours')
    """).fetchall()
    print(f"  已揽件超72h: {len(t_rows_raw)}")

    t_rows = []
    for raw in t_rows_raw:
        if raw[0] not in signed_nos:
            logistics_no, op_time, src_tid = raw
            order = c.execute("""
                SELECT
                    COALESCE(ao.tradeNo, wc.tradeNo, ws.tradeNo),
                    COALESCE(ao.warehouseName, wc.warehouseName, ws.warehouseName),
                    COALESCE(ao.receiverProvinceName, wc.receiverProvinceName, ws.receiverProvinceName),
                    COALESCE(ao.receiverCityName, wc.receiverCityName, ws.receiverCityName),
                    COALESCE(ao.receiverDistrictName, wc.receiverDistrictName, ws.receiverDistrictName),
                    COALESCE(ao.realAmount, wc.realAmount, ws.realAmount),
                    COALESCE(ao.orderItemList, wc.orderItemList, ws.orderItemList),
                    COALESCE(ao.estimateConsignTime, wc.estimateConsignTime, ws.estimateConsignTime)
                FROM erp_all_orders ao
                LEFT JOIN erp_wait_check wc ON ao.srcTids = wc.srcTids
                LEFT JOIN erp_wait_send_self ws ON ao.srcTids = ws.srcTids
                WHERE ao.srcTids = ?
                LIMIT 1
            """, (src_tid,)).fetchone()
            if order:
                # 12 columns matching p_rows format: add NULL for trace_info(3)
                t_row = (logistics_no, op_time, src_tid, None) + order
                title = make_card_name(get_spu_name(t_row[10]), get_sku_spec(t_row[10]), get_deadline_date(t_row[11]))
                hours = 0
                try:
                    hours = (datetime.now() - datetime.strptime(op_time[:19], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
                except Exception:
                    pass
                desc = (
                    f"**运单号**: {logistics_no}\n**订单号**: {t_row[4]}\n**仓库**: {t_row[5] or '未知'}\n"
                    f"**收货**: {t_row[6] or ''}{t_row[7] or ''}{t_row[8] or ''}\n**金额**: ¥{t_row[9]}\n"
                    f"**揽件时间**: {op_time}\n**已超时**: 约 {int(hours)} 小时\n\n"
                    f"--- 实际原因 ---\n已揽件超过 72 小时未签收\n\n"
                    f"--- AI 分析 ---\n该包裹已揽件超过 {int(hours)} 小时仍未签收。\n建议：联系快递公司核实是否丢件或滞留"
                )
                r_mod = (f"TO_{logistics_no}",) + t_row[1:]
                exc_rows.append((r_mod, title, desc))
                exc_count += 1
                if exc_count % 10 == 0:
                    time.sleep(1)

    print(f"  创建 {exc_count} 张...")
    for idx, (r, title, desc) in enumerate(reversed(exc_rows)):
        try:
            trello_api("POST", "cards", {"idList": list_ids["异常件"], "name": title, "desc": desc})
        except Exception as e:
            print(f"  FAIL: {title[:60]}... — {e}")
        if (idx + 1) % 10 == 0:
            time.sleep(1)
        if (idx + 1) % 50 == 0:
            print(f"  已创建 {idx + 1}/{exc_count}...")

    total_cards += exc_count
    counts["异常件"] = exc_count + 1
    create_card_top(list_ids["异常件"], " 异常件 · 摘要", generate_summary("异常件", [r[0] for r in exc_rows], "exception"))

    # 重命名栏目
    print(f"\n--- 栏目重命名 ---")
    for col in COLUMN_ORDER:
        cid = list_ids.get(col)
        if cid:
            new_name = f"{col} ({counts.get(col, 0)}件)"
            trello_api("PUT", f"lists/{cid}", {"name": new_name})
            print(f"  {col} → {new_name}")

    db.close()
    print(f"\n=== 完成，共 {total_cards} 张卡片 ===")


if __name__ == "__main__":
    main()
