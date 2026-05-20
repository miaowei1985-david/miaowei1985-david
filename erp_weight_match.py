#!/usr/bin/env python3
"""
产品规格重量匹配 — 将规格品名重量.xlsx匹配到数据库
匹配策略:
  1. 优先用 skuName（规格名称）匹配
  2. skuName 为空时，用 spuName（品名）匹配规格名称
"""
import openpyxl
import sqlite3
import json
import logging
logging.basicConfig(filename="/tmp/erp_weight_match.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


XLSX_PATH = "/Users/macmini4pro/pdd/jd售后/产品规格/规格品名重量.xlsx"
DB_PATH = "/Users/macmini4pro/pdd/Claudecode/erp_all.db"

def main():
    print("📊 读取产品规格重量表...", flush=True)
    wb = openpyxl.load_workbook(XLSX_PATH, data_only=True)
    ws = wb.active

    # 建立匹配映射: key -> weight, 同时保存货品编码和商家编码
    weight_map = {}  # name -> {weight, goods_code, merchant_code}
    for row in ws.iter_rows(min_row=2, values_only=True):
        goods_code = str(row[1] or "").strip()
        spec_name = str(row[3] or "").strip()  # 规格名称
        merchant_code = str(row[4] or "").strip()
        weight = row[5]
        if spec_name and weight:
            weight_map[spec_name] = {
                "weight": float(weight),
                "goods_code": goods_code,
                "merchant_code": merchant_code,
            }
    wb.close()
    print(f"  规格品名: {len(weight_map)} 个", flush=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()

    # 建表
    c.execute("DROP TABLE IF EXISTS product_weight")
    c.execute('''CREATE TABLE product_weight (
        goods_code TEXT,
        sku_name TEXT,
        merchant_code TEXT,
        weight REAL
    )''')
    for name, info in weight_map.items():
        c.execute("INSERT INTO product_weight VALUES (?,?,?,?)",
                  (info["goods_code"], name, info["merchant_code"], info["weight"]))

    conn.commit()

    # 遍历三张ERP表，统计匹配情况
    tables = ["erp_wait_check", "erp_wait_send_self", "erp_finished"]
    total_orders = 0
    matched_sku = 0
    matched_spu = 0
    unmatched_spus = set()

    for tbl in tables:
        exists = c.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{tbl}'").fetchone()
        if not exists:
            continue
        rows = c.execute(f"SELECT tid, orderItemList FROM {tbl}").fetchall()
        for tid, order_item_raw in rows:
            total_orders += 1
            if not order_item_raw:
                continue
            try:
                items = json.loads(order_item_raw) if isinstance(order_item_raw, str) else order_item_raw
                if not isinstance(items, list):
                    continue
                for item in items:
                    sku_name = (item.get("skuName") or "").strip()
                    spu_name = (item.get("spuName") or "").strip()
                    if sku_name in weight_map:
                        matched_sku += 1
                        break
                    elif not sku_name and spu_name in weight_map:
                        matched_spu += 1
                        break
                    else:
                        unmatched_spus.add(spu_name or "(空)")
            except:
                pass

    conn.close()
    matched_total = matched_sku + matched_spu
    print(f"  总订单数: {total_orders}", flush=True)
    print(f"  skuName匹配: {matched_sku}", flush=True)
    print(f"  spuName匹配: {matched_spu}", flush=True)
    print(f"  匹配到重量的订单: {matched_total} ({matched_total / total_orders * 100:.1f}%)", flush=True)
    print(f"  未匹配的品名: {len(unmatched_spus)}", flush=True)
    for s in list(unmatched_spus)[:10]:
        print(f"    - {s[:80]}", flush=True)
    print("✅ 产品规格重量表已入库", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Script failed: %s", e)
        raise
