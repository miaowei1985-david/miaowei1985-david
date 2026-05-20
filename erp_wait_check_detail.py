#!/usr/bin/env python3
"""待审核订单按发货时效(24h/48h/72h/72-96h/>96h)按仓库分类统计商品品名和数量"""
import json, os, sys
from datetime import datetime
from collections import defaultdict
import requests
import logging
logging.basicConfig(filename="/tmp/erp_wait_check_detail.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


TOKEN = os.environ.get("ERP_TOKEN")
SHOP = "cysxny05"
ERP_BASE = "https://erp.huice.com"
API_QUERY = "/api/main/oms/tradeQuery/query"
HEADERS = {
    "app-code": "web", "app-product-code": "jisu", "app-version": "1.0.640",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

def fetch_wait_check(token):
    session = requests.Session()
    session.cookies.update({"X-HC-TOKEN": token, "_xhcsid": SHOP})
    all_rows, page = [], 1
    while True:
        body = {"pageTab": "WAIT_CHECK", "currentPage": page, "pageSize": 2000}
        resp = session.post(f"{ERP_BASE}{API_QUERY}", headers=HEADERS, json=body, timeout=30)
        data = resp.json().get("data", {})
        rows = data.get("data", [])
        total = int(data.get("totalCount", 0))
        if not rows:
            break
        all_rows.extend(rows)
        print(f"  第 {page} 页获取 {len(rows)} 条，累计 {len(all_rows)}/{total}", flush=True)
        if len(all_rows) >= total:
            break
        page += 1
    return all_rows

def classify_deadline(estimate_consign_time, now):
    """返回所属时间段标签"""
    if not estimate_consign_time:
        return ">120小时"
    dl = datetime.fromisoformat(estimate_consign_time.replace("T", " "))
    hours = (dl - now).total_seconds() / 3600
    if hours <= 24:
        return "24小时内"
    elif hours <= 48:
        return "24-48小时"
    elif hours <= 72:
        return "48-72小时"
    elif hours <= 96:
        return "72-96小时"
    elif hours <= 120:
        return "96-120小时"
    else:
        return ">120小时"

TIME_ORDER = ["24小时内", "24-48小时", "48-72小时", "72-96小时", "96-120小时", ">120小时"]

def extract_products(rows):
    """从 orderItemList JSON 中提取 {品名|规格: 数量}"""
    products = defaultdict(int)
    for row in rows:
        raw = row.get("orderItemList", "")
        if not raw:
            continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(items, list):
                for item in items:
                    spu = item.get("spuName", "")
                    sku = item.get("skuName", "")
                    key = f"{spu} | {sku}" if spu and sku else spu or sku
                    if key:
                        products[key] += 1
        except Exception:
            pass
    return dict(sorted(products.items(), key=lambda x: -x[1]))

def main():
    if not TOKEN:
        print("请设置环境变量 ERP_TOKEN")
        sys.exit(1)

    now = datetime.now()
    print(f"拉取待审核订单 (当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')})...")
    rows = fetch_wait_check(TOKEN)
    print(f"\n共 {len(rows)} 条待审核订单\n")

    # 先按时间段分类，再按仓库分组
    time_buckets = {tag: defaultdict(list) for tag in TIME_ORDER}
    for row in rows:
        tag = classify_deadline(row.get("estimateConsignTime", ""), now)
        warehouse = row.get("warehouseName", "") or "未知仓库"
        time_buckets[tag][warehouse].append(row)

    # 输出
    for tag in TIME_ORDER:
        warehouse_map = time_buckets[tag]
        if not warehouse_map:
            print(f"{'='*60}")
            print(f"=== {tag}: 无订单 ===")
            print(f"{'='*60}\n")
            continue

        total_orders = sum(len(v) for v in warehouse_map.values())
        total_paid = sum(float(r.get("paid", 0) or 0) for rows_list in warehouse_map.values() for r in rows_list)
        print(f"{'='*60}")
        print(f"=== {tag}: {total_orders} 单 | 实收 {total_paid:,.0f} 元 ===")
        print(f"{'='*60}")

        for warehouse in sorted(warehouse_map.keys()):
            items = warehouse_map[warehouse]
            wh_paid = sum(float(r.get("paid", 0) or 0) for r in items)
            print(f"\n  【{warehouse}】 {len(items)} 单 | 实收 {wh_paid:,.0f} 元")
            products = extract_products(items)
            print(f"  {'品名规格':<60} {'数量':>6}")
            print(f"  {'-'*68}")
            for name, cnt in products.items():
                print(f"  {name:<60} {cnt:>6}")
            print(f"  {'-'*68}")
            print(f"  {'合计':<60} {sum(products.values()):>6}")
        print()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Script failed: %s", e)
        raise
