#!/usr/bin/env python3
"""
拉取ERP全量订单（ALL_ORDER）→ 存入 erp_all_orders 表
用途：售后关联订单时间（含未出库售后对应的订单）
"""
import os, json, sqlite3, requests, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
logging.basicConfig(filename="/tmp/erp_all_order_fetch.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


ERP_BASE = "https://erp.huice.com"
API_QUERY = "/api/main/oms/tradeQuery/query"
SHOP = "cysxny05"
TOKEN = os.environ.get("ERP_TOKEN")

HEADERS = {
    "content-type": "application/json",
    "app-code": "web",
    "app-product-code": "jisu",
    "app-version": "1.0.640",
    "referer": "https://erp.huice.com/micro-app-new/erpx-web",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "erp_all.db")

# 需要保留的关键字段（从API全量JSON中提取）
KEY_FIELDS = [
    "tid", "tradeId", "tradeNo", "shopName", "warehouseName",
    "tradeTime", "payTime", "consignTime", "estimateConsignTime",
    "tradeStatus", "tradeStatusFront", "tradeStatusFrontText",
    "tradeFrom", "tradeFromText", "tradeType", "tradeTypeName",
    "platformName", "orgPlatformName",
    "paid", "goodsAmount", "refundAmount", "postAmount", "discount",
    "goodsCount", "goodsTypeCount",
    "buyerNick", "receiverProvinceName", "receiverCityName", "receiverDistrictName",
    "logisticsName", "logisticsNo", "consignTime",
    "remarkFlag", "remarkFlagText", "remark",
    "isPrint", "isPrintText", "refundStatus", "refundStatusText",
    "deliveryTermText", "warehouseId", "warehouseName",
    "shopId", "srcTids", "oaid", "tradeMask",
    "occupyStatus", "occupyStatusText", "abnormalReason",
]


def fetch_page(page, session=None):
    """拉取单页 ALL_ORDER"""
    cookies = {"X-HC-TOKEN": TOKEN, "_xhcsid": SHOP}
    # API限制：支付时间必须在3个月内
    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=90)
    body = {
        "shopIdList": [],
        "payTimeBegin": start.strftime("%Y-%m-%d 00:00:00"),
        "payTimeEnd": end.strftime("%Y-%m-%d 23:59:59"),
        "pageTab": "ALL_ORDER",
        "calcTotalCount": True,
        "currentPage": page,
        "pageSize": 2000,
    }
    resp = (session or requests).post(
        f"{ERP_BASE}{API_QUERY}", headers=HEADERS, cookies=cookies, json=body, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def fetch_all_orders():
    """并发拉取全部 ALL_ORDER 数据"""
    if not TOKEN:
        print("❌ 请设置环境变量 ERP_TOKEN")
        return []

    # 先拉第一页拿总数
    print("📥 拉取全量订单（ALL_ORDER）...", flush=True)
    first = fetch_page(1)
    d = first.get("data")
    if not d:
        print(f"❌ API返回异常: {first.get('msg', '未知错误')}", flush=True)
        return []
    total = int(d.get("totalCount", 0))
    pages = int(d.get("totalPage", 1))
    print(f"  总记录数: {total:,}，共 {pages} 页", flush=True)

    all_rows = d["data"]
    print(f"  第 1 页获取 {len(all_rows)} 条，累计 {len(all_rows)}/{total}", flush=True)

    if pages <= 1:
        return all_rows

    # 并发拉取剩余页
    with requests.Session() as session:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {}
            for p in range(2, pages + 1):
                future = executor.submit(fetch_page, p, session)
                futures[future] = p

            for future in as_completed(futures):
                page = futures[future]
                data = future.result()
                rows = data["data"]["data"]
                all_rows.extend(rows)
                print(f"  第 {page} 页获取 {len(rows)} 条，累计 {len(all_rows)}/{total}", flush=True)

    print(f"✅ 全量订单拉取完成: {len(all_rows):,} 条", flush=True)
    return all_rows


def save_to_db(rows):
    """写入 erp_all_orders 表"""
    print(f"💾 保存到数据库 {DB_PATH}...", flush=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    c.execute("DROP TABLE IF EXISTS erp_all_orders")

    sample = rows[0]
    # 统一列顺序：按字段名排序，确保建表和写入一致
    col_names = sorted(sample.keys())

    # 动态建表
    cols_def = []
    for k in col_names:
        v = sample[k]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            cols_def.append(f"{k} REAL")
        elif k == "tid":
            cols_def.append(f"{k} TEXT")
        else:
            cols_def.append(f"{k} TEXT")

    cols_sql = ", ".join(cols_def)
    c.execute(f"CREATE TABLE erp_all_orders ({cols_sql})")

    # 批量插入
    placeholders = ", ".join(["?" for _ in col_names])
    insert_sql = f"INSERT INTO erp_all_orders VALUES ({placeholders})"

    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        values = []
        for r in batch:
            row_vals = []
            for k in col_names:
                v = r.get(k)
                if isinstance(v, (list, dict)):
                    v = json.dumps(v, ensure_ascii=False)
                elif isinstance(v, bool):
                    v = int(v)
                row_vals.append(v)
            values.append(row_vals)
        c.executemany(insert_sql, values)
        conn.commit()
        print(f"  已写入 {min(i + batch_size, len(rows))}/{len(rows)}", flush=True)

    # 建索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_all_order_tid ON erp_all_orders(tid)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_all_order_tradeTime ON erp_all_orders(tradeTime)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_all_order_payTime ON erp_all_orders(payTime)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_all_order_consignTime ON erp_all_orders(consignTime)")
    conn.commit()
    conn.close()
    print(f"✅ 数据入库完成: {len(rows):,} 条", flush=True)


def rebuild_line_items():
    """从 erp_all_orders 展开 orderItemList 写入 order_line_items"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("DROP TABLE IF EXISTS order_line_items")
    conn.execute("""CREATE TABLE order_line_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT, sku_id TEXT, sku_no TEXT, sku_name TEXT,
        spu_name TEXT, quantity INTEGER, price REAL,
        share_amount REAL, paid REAL, warehouse_name TEXT,
        order_time TEXT, pay_time TEXT, consign_deadline TEXT,
        actual_ship_time TEXT, order_status TEXT, receiver_city TEXT,
        shop_name TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute("CREATE INDEX idx_oli_order_id ON order_line_items(order_id)")
    conn.execute("CREATE INDEX idx_oli_sku_no ON order_line_items(sku_no)")
    conn.execute("CREATE INDEX idx_oli_warehouse ON order_line_items(warehouse_name)")
    conn.execute("CREATE INDEX idx_oli_order_time ON order_line_items(order_time)")

    cur = conn.execute(
        "SELECT srcTids, tradeTime, payTime, estimateConsignTime, "
        "consignTime, warehouseName, shopName, receiverCityName, "
        "orderItemList FROM erp_all_orders")
    batch = []
    total = 0
    for row in cur:
        srcTids, tradeTime, payTime, estimateConsignTime, consignTime, \
            warehouseName, shopName, receiverCityName, orderItemList = row
        try:
            items = json.loads(orderItemList) if orderItemList else []
        except (json.JSONDecodeError, TypeError):
            continue
        for item in items:
            sku_num = int(item.get("skuNum", 0) or 0)
            if sku_num == 0:
                continue
            batch.append((
                srcTids, item.get("skuId", ""), item.get("skuNo", ""),
                item.get("skuName", ""), item.get("spuName", ""),
                sku_num, float(item.get("price", 0) or 0),
                float(item.get("shareAmount", 0) or 0),
                float(item.get("paid", 0) or 0),
                warehouseName or "", tradeTime or "", payTime or "",
                estimateConsignTime or "", consignTime or "",
                item.get("tradeStatusName", ""),
                receiverCityName or "", shopName or "",
            ))
            if len(batch) >= 1000:
                conn.executemany(
                    "INSERT INTO order_line_items "
                    "(order_id, sku_id, sku_no, sku_name, spu_name, quantity, "
                    "price, share_amount, paid, warehouse_name, order_time, "
                    "pay_time, consign_deadline, actual_ship_time, order_status, "
                    "receiver_city, shop_name) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
                total += len(batch)
                batch = []
    if batch:
        conn.executemany(
            "INSERT INTO order_line_items "
            "(order_id, sku_id, sku_no, sku_name, spu_name, quantity, "
            "price, share_amount, paid, warehouse_name, order_time, "
            "pay_time, consign_deadline, actual_ship_time, order_status, "
            "receiver_city, shop_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", batch)
        total += len(batch)
    conn.commit()
    conn.close()
    print(f"  order_line_items 重建完成: {total:,} 行", flush=True)

def main():
    rows = fetch_all_orders()
    if rows:
        save_to_db(rows)
        rebuild_line_items()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Script failed: %s", e)
        raise
