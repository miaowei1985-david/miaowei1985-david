#!/usr/bin/env python3
"""Expand orderItemList JSON from ERP tables into order_line_items."""
import sqlite3, json, sys

DB = "/Users/macmini4pro/pdd/Claudecode/erp_all.db"

TABLES = [
    "erp_wait_check",
    "erp_wait_send_self",
    "erp_finished",
]

def backfill(dry_run=False):
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    total_rows = 0
    for table in TABLES:
        cur.execute(f"SELECT srcTids, tradeTime, payTime, estimateConsignTime, "
                    f"consignTime, warehouseName, shopName, receiverCityName, "
                    f"tradeStatusType, orderItemList FROM {table}")
        rows = cur.fetchall()
        print(f"  {table}: {len(rows)} orders")

        inserts = []
        for row in rows:
            (srcTids, tradeTime, payTime, estimateConsignTime,
             consignTime, warehouseName, shopName, receiverCityName,
             tradeStatusType, orderItemList) = row

            try:
                items = json.loads(orderItemList) if orderItemList else []
            except (json.JSONDecodeError, TypeError):
                continue

            for item in items:
                sku_num = int(item.get("skuNum", 0) or 0)
                if sku_num == 0:
                    continue
                inserts.append((
                    srcTids,
                    item.get("skuId", ""),
                    item.get("skuNo", ""),
                    item.get("skuName", ""),
                    item.get("spuName", ""),
                    sku_num,
                    float(item.get("price", 0) or 0),
                    float(item.get("shareAmount", 0) or 0),
                    float(item.get("paid", 0) or 0),
                    warehouseName or "",
                    tradeTime or "",
                    payTime or "",
                    estimateConsignTime or "",
                    consignTime or "",
                    item.get("tradeStatusName", ""),
                    receiverCityName or "",
                    shopName or "",
                    table,
                ))

        if inserts:
            if not dry_run:
                conn.executemany(
                    "INSERT INTO order_line_items "
                    "(order_id, sku_id, sku_no, sku_name, spu_name, quantity, "
                    "price, share_amount, paid, warehouse_name, order_time, "
                    "pay_time, consign_deadline, actual_ship_time, order_status, "
                    "receiver_city, shop_name, source_table) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    inserts,
                )
            total_rows += len(inserts)
            print(f"    -> {len(inserts)} line items")

    if not dry_run:
        conn.commit()
        print(f"\nInserted {total_rows} line items into order_line_items")
    else:
        print(f"\nDry run: would insert {total_rows} line items")

    # Print summary
    cur.execute("SELECT COUNT(*) FROM order_line_items")
    print(f"  Total rows in order_line_items: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(DISTINCT order_id) FROM order_line_items")
    print(f"  Distinct orders: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(DISTINCT sku_no) FROM order_line_items")
    print(f"  Distinct SKUs: {cur.fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    print("Backfilling order_line_items...")
    backfill(dry_run=dry_run)
