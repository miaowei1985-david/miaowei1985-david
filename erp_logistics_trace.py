#!/usr/bin/env python3
"""物流轨迹增量拉取脚本 - 只查询未签收的快递"""

import os
import sys
import sqlite3
import requests
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/tmp/erp_logistics_trace.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

DB_PATH = os.path.expanduser("~/pdd/Claudecode/erp_all.db")
API_URL = "https://erp.huice.com/api/main/oms/logistics/trace/list"
TOKEN = os.environ.get("ERP_TOKEN", "")
SHOP_NAME = "榴愿时刻工厂店"


def create_table(conn):
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logistics_trace (
            trace_id TEXT PRIMARY KEY,
            logistics_no TEXT NOT NULL,
            sys_logistics_code TEXT,
            logistics_name TEXT,
            order_id TEXT,
            src_tid TEXT,
            shop_name TEXT,
            operate_desc TEXT,
            operate_time TEXT,
            current_addr TEXT,
            trace_info TEXT,
            operate_type TEXT,
            operate_phone TEXT,
            current_site TEXT,
            next_site TEXT,
            error_message TEXT,
            created_at TEXT,
            UNIQUE(trace_id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_logistics_no ON logistics_trace(logistics_no)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_operate_time ON logistics_trace(operate_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_id ON logistics_trace(order_id)")
    conn.commit()
    logger.info("物流轨迹表检查完成")


def get_unsigned_packages(conn):
    """获取需要查询轨迹的快递单号（增量：排除已签收的）"""
    cursor = conn.cursor()
    
    # 1. 从 erp_all_orders 获取所有有快递单号的订单
    cursor.execute("""
        SELECT DISTINCT tradeId, srcTids, logisticsNo, sysLogisticsCode, logisticsName
        FROM erp_all_orders
        WHERE shopName = ?
        AND logisticsNo IS NOT NULL
        AND logisticsNo != ""
        AND logisticsNo != "9999-12-01"
    """, (SHOP_NAME,))
    all_packages = {row[2]: row for row in cursor.fetchall()}  # 以 logistics_no 为 key
    
    logger.info(f"全量订单中有快递单号: {len(all_packages)} 个")
    
    # 2. 查询已签收的快递（不需要再查）
    cursor.execute("""
        SELECT DISTINCT logistics_no
        FROM logistics_trace
        WHERE operate_desc = "已签收"
    """)
    signed_packages = set(row[0] for row in cursor.fetchall())
    
    logger.info(f"已签收不再查询: {len(signed_packages)} 个")
    
    # 3. 过滤出需要查询的（未签收的）
    to_query = []
    for logistics_no, row in all_packages.items():
        if logistics_no not in signed_packages:
            to_query.append(row)
    
    logger.info(f"需要查询轨迹（未签收）: {len(to_query)} 个")
    
    return to_query


def fetch_trace(logistics_no, sys_logistics_code, token):
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "app-code": "web",
        "app-product-code": "jisu",
        "app-version": "1.0.640",
        "cookie": "X-HC-TOKEN=" + token
    }
    payload = {"logisticsNo": logistics_no, "sysLogisticsCode": sys_logistics_code or "32"}
    
    try:
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=30)
        data = resp.json()
        if data.get("success") and data.get("errorCode") == 200:
            return data.get("data", [])
        msg = data.get("msg") or "unknown"
        if "超过90天" in msg:
            return []  # 90天以上的跳过，不算失败
        logger.warning(f"API错误 {logistics_no}: {msg}")
        return None
    except Exception as e:
        logger.error(f"请求失败 {logistics_no}: {e}")
        return None


def save_traces(conn, traces, order_info):
    cursor = conn.cursor()
    order_id, src_tid, logistics_no, sys_logistics_code, logistics_name = order_info[:5]
    
    saved = 0
    for trace in traces:
        trace_id = trace.get("traceId")
        if not trace_id:
            continue
        cursor.execute("""
            INSERT OR REPLACE INTO logistics_trace (
                trace_id, logistics_no, sys_logistics_code, logistics_name,
                order_id, src_tid, shop_name, operate_desc, operate_time,
                current_addr, trace_info, operate_type, operate_phone,
                current_site, next_site, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trace_id, logistics_no, sys_logistics_code, logistics_name,
            order_id, src_tid, SHOP_NAME, trace.get("operateDesc"),
            trace.get("operateTime"), trace.get("currentAddr"),
            trace.get("traceInfo"), trace.get("operateType"),
            trace.get("operatePhone"), trace.get("currentSite"),
            trace.get("nextSite"), trace.get("errorMessage"),
            datetime.now().isoformat()
        ))
        saved += 1
    return saved


def main():
    if not TOKEN:
        logger.error("请设置 ERP_TOKEN 环境变量")
        sys.exit(1)
    
    conn = sqlite3.connect(DB_PATH)
    create_table(conn)
    
    # 增量查询：只查未签收的
    packages = get_unsigned_packages(conn)
    
    if not packages:
        logger.info("所有快递都已签收，无需查询")
        conn.close()
        return
    
    total_saved = 0
    success_count = 0
    fail_count = 0
    new_signed = 0
    
    logger.info(f"开始并发拉取 {len(packages)} 个未签收快递...")
    
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for pkg in packages:
            future = executor.submit(fetch_trace, pkg[2], pkg[3], TOKEN)
            futures[future] = pkg
        
        for future in as_completed(futures):
            pkg = futures[future]
            try:
                traces = future.result()
                if traces:
                    saved = save_traces(conn, traces, pkg)
                    total_saved += saved
                    success_count += 1
                    
                    # 检查是否新签收
                    for trace in traces:
                        if trace.get("operateDesc") == "已签收":
                            new_signed += 1
                            break
                    
                    if success_count % 50 == 0:
                        conn.commit()
                        logger.info(f"已处理 {success_count} 个...")
                elif traces == []:
                    pass  # 90天以上跳过
                else:
                    fail_count += 1
            except Exception as e:
                fail_count += 1
    
    conn.commit()
    
    # 统计
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM logistics_trace")
    total_rows = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT logistics_no) FROM logistics_trace")
    unique_packages = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT logistics_no) FROM logistics_trace WHERE operate_desc = \"已签收\"")
    signed_count = cursor.fetchone()[0]
    
    logger.info(f"完成: 查询 {success_count}, 失败 {fail_count}, 新增轨迹 {total_saved}")
    logger.info(f"本次新签收: {new_signed} 个")
    logger.info(f"表总行数: {total_rows}, 独立快递: {unique_packages}, 已签收: {signed_count}")
    conn.close()


if __name__ == "__main__":
    main()
