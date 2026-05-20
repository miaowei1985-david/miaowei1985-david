#!/usr/bin/env python3
"""
京东订单结算明细与ERP订单匹配 → 附加到现有数据库
策略: 三张ERP表匹配 + ALL_ORDER全量拉取补全
"""
import csv
import os
import sqlite3
import logging
logging.basicConfig(filename="/tmp/erp_finance_match.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


from erp_config import DB_PATH

CSV_PATH = os.path.join(DB_PATH.replace("erp_all.db", ""), "uploads", "finance_upload.csv")


def load_csv_finance(csv_path):
    finance = {}
    with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tid = row["订单编号"].strip().strip("'")
            if not tid:
                continue
            fee_name = row["费用名称"].strip()
            direction = row["收支方向"].strip()
            amount = float(row["应结金额"] or "0") if row["应结金额"].strip() else 0
            settle_status = row["结算状态"].strip()
            order_status = row["订单状态"].strip()

            if tid not in finance:
                finance[tid] = {
                    "货款收入": 0.0, "货款支出": 0.0,
                    "直营服务费收入": 0.0, "直营服务费支出": 0.0,
                    "交易服务费收入": 0.0, "交易服务费支出": 0.0,
                    "直营补贴款收入": 0.0, "直营补贴款支出": 0.0,
                    "售后卖家赔付费": 0.0,
                    "代收配送费收入": 0.0, "代收配送费支出": 0.0,
                    "总收入": 0.0, "总支出": 0.0,
                    "订单状态": order_status,
                    "结算状态": settle_status,
                    "记录数": 0,
                }
            rec = finance[tid]
            rec["记录数"] += 1
            if direction == "收入":
                rec["总收入"] += amount
            else:
                rec["总支出"] += abs(amount)

            if fee_name == "售后卖家赔付费":
                rec["售后卖家赔付费"] += abs(amount)
            elif direction == "收入":
                rec[f"{fee_name}收入"] += amount
            else:
                rec[f"{fee_name}支出"] += abs(amount)

            if order_status != "已取消":
                rec["订单状态"] = order_status
    return finance


def main():
    print("📊 读取CSV财务数据...", flush=True)
    finance = load_csv_finance(CSV_PATH)
    print(f"  唯一订单数: {len(finance)}", flush=True)

    # 从三张ERP表获取已匹配的订单
    print("📥 从ERP数据库获取订单...", flush=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    order_dict = {}
    # 从 erp_all_orders 全量匹配（覆盖更广）
    rows = conn.execute("SELECT tid, srcTids, shopName, warehouseName, paid FROM erp_all_orders").fetchall()
    for r in rows:
        tid = str(r["tid"]).strip() if r["tid"] else ""
        if tid and tid in finance:
            order_dict[tid] = {
                "tid": tid,
                "srcTids": r["srcTids"],
                "shopName": r["shopName"],
                "warehouseName": r["warehouseName"],
                "paid": r["paid"]
            }
    conn.close()

    print(f"  从 erp_all_orders 匹配到: {len(order_dict)}", flush=True)

    # 写入finance表
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    c.execute("DROP TABLE IF EXISTS finance_matched")
    c.execute("DROP TABLE IF EXISTS finance_unmatched")

    c.execute('''CREATE TABLE finance_matched (
        tids TEXT, sys_no TEXT, shop_name TEXT, warehouse_name TEXT,
        status TEXT, status_text TEXT, paid REAL,
        订单状态 TEXT, 结算状态 TEXT, 记录数 INT,
        总收入 REAL, 总支出 REAL, 净额 REAL,
        货款收入 REAL, 货款支出 REAL,
        直营服务费收入 REAL, 直营服务费支出 REAL,
        交易服务费收入 REAL, 交易服务费支出 REAL,
        直营补贴款收入 REAL, 直营补贴款支出 REAL,
        售后卖家赔付费 REAL,
        代收配送费收入 REAL, 代收配送费支出 REAL
    )''')

    c.execute('''CREATE TABLE finance_unmatched (
        tids TEXT, 订单状态 TEXT, 结算状态 TEXT, 记录数 INT,
        总收入 REAL, 总支出 REAL, 净额 REAL
    )''')

    matched = 0
    unmatched = 0

    for tid, fin in finance.items():
        order = order_dict.get(tid)
        if order:
            matched += 1
            c.execute('''INSERT INTO finance_matched VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )''', (
                tid,
                order.get("sysNo") or "",
                order.get("shopName") or "",
                order.get("warehouseName") or "",
                order.get("status") or "",
                order.get("statusText") or "",
                float(order.get("paid", 0) or 0),
                fin["订单状态"],
                fin["结算状态"],
                fin["记录数"],
                fin["总收入"],
                fin["总支出"],
                fin["总收入"] - fin["总支出"],
                fin["货款收入"],
                fin["货款支出"],
                fin["直营服务费收入"],
                fin["直营服务费支出"],
                fin["交易服务费收入"],
                fin["交易服务费支出"],
                fin["直营补贴款收入"],
                fin["直营补贴款支出"],
                fin["售后卖家赔付费"],
                fin["代收配送费收入"],
                fin["代收配送费支出"],
            ))
        else:
            unmatched += 1
            c.execute('''INSERT INTO finance_unmatched VALUES (
                ?, ?, ?, ?, ?, ?, ?
            )''', (
                tid,
                fin["订单状态"],
                fin["结算状态"],
                fin["记录数"],
                fin["总收入"],
                fin["总支出"],
                fin["总收入"] - fin["总支出"],
            ))

    conn.commit()
    c.execute("CREATE INDEX IF NOT EXISTS idx_finance_tids ON finance_matched(tids)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_finance_unmatched ON finance_unmatched(tids)")
    conn.commit()
    conn.close()

    total = matched + unmatched
    print(f"  匹配成功: {matched}", flush=True)
    print(f"  未匹配: {unmatched}", flush=True)
    print(f"  匹配率: {matched / total * 100:.1f}%" if total else "  匹配率: N/A", flush=True)
    print("✅ 完成", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Script failed: %s", e)
        raise
