#!/usr/bin/env python3
"""
旺店通 ERP 数据拉取 — 只负责从ERP拉数据并写入数据库
影子库原子切换 + 行数校验 + 鲜度元数据
"""
import json
import fcntl
import os
import shutil
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
import logging
logging.basicConfig(filename="/tmp/erp_fetch.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ===== 配置 =====
from erp_config import DB_PATH, ERP_API_BASE
TOKEN = os.environ.get("ERP_TOKEN")
LOCAL_DB = f"/tmp/erp_all_{os.getpid()}.db"
SHOP = "cysxny05"

API_QUERY = "/api/main/oms/tradeQuery/query"
HEADERS = {
    "app-code": "web", "app-product-code": "jisu", "app-version": "1.0.640",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

ERP_TABLES = ["erp_wait_check", "erp_wait_send_self", "erp_finished"]

def _simple_body(tab, page, size=2000):
    return {"pageTab": tab, "currentPage": page, "pageSize": size}

def _finished_body(tab, page, size=2000):
    return {
        "abnormalFast": 0, "logisticsStatusWaringFast": 0,
        "consignTimeBegin": "2026-04-01 00:00:00", "consignTimeEnd": "2026-05-31 23:59:59",
        "containSkuSuiteType": 0, "containSkuSuiteGoodsType": 0, "excludeSkuSuiteType": 0,
        "noSearchField": 0, "noSearchType": 0, "isIncludeAbnormal": True,
        "containRemarkType": 3, "containMessageType": 3, "suiteSearchField": 0,
        "suiteSearchType": 0, "orgPlatformQueryList": [], "anchorSearchField": 0,
        "anchorSearchType": 0, "excludeAnchorSearchField": 0, "excludeAnchorSearchType": 0,
        "containRemarkFlag": 1, "remarkFlagList": [], "pageTab": tab,
        "containSkuIdList": [], "containSuiteIdList": [], "manualExcludeSkuIdList": [],
        "manualExcludeSuiteIdList": [], "remarkContainMultiContent": True,
        "calcTotalCount": True, "currentPage": page, "pageSize": size,
    }

PAGE_TABS = {
    "WAIT_CHECK": ("待审核", _simple_body),
    "WAIT_SEND_SELF": ("待发货-自营", _simple_body),
    "FINISHED": ("已完成", _finished_body),
}

# ===== 数据拉取 =====
def fetch_all(token, shop_id, page_tab, body_fn, desc="", page_size=2000, session=None):
    own_session = session is None
    if own_session:
        session = requests.Session()
    cookies = {"X-HC-TOKEN": token, "_xhcsid": shop_id}
    session.cookies.update(cookies)

    all_rows, page = [], 1
    while True:
        body = body_fn(page_tab, page, page_size)
        print(f"  [{desc}] 请求第 {page} 页...", flush=True)
        try:
            resp = session.post(f"{ERP_API_BASE}{API_QUERY}", headers=HEADERS, json=body, timeout=90)
        except requests.RequestException as e:
            print(f"  [{desc}] 请求异常: {e}", flush=True)
            break
        if resp.status_code != 200:
            print(f"  [{desc}] 状态码: {resp.status_code}", flush=True)
            break
        data = resp.json().get("data", {})
        if not isinstance(data, dict):
            break
        rows = data.get("data", [])
        total = int(data.get("totalCount", len(all_rows) + len(rows)))
        if not rows:
            break
        all_rows.extend(rows)
        print(f"  [{desc}] 第 {page} 页获取 {len(rows)} 条，累计 {len(all_rows)}/{total}", flush=True)
        if len(all_rows) >= total:
            break
        page += 1
    return all_rows

def fetch_all_tabs(token, shop_id, max_workers=4):
    all_data = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for tab_name, (desc, body_fn) in PAGE_TABS.items():
            future = executor.submit(fetch_all, token, shop_id, tab_name, body_fn, desc)
            futures[future] = (tab_name, desc)
        for future in as_completed(futures):
            tab_name, desc = futures[future]
            rows = future.result()
            all_data.append((tab_name, desc, rows))
            print(f"  {desc}: {len(rows)} 条")
    all_data.sort(key=lambda x: list(PAGE_TABS.keys()).index(x[0]))
    return all_data

# ===== 数据库写入 =====
def save_sqlite(all_data, db_path):
    conn = sqlite3.connect(db_path, timeout=30.0)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA cache_size=50000")
    for tab_name, _, rows in all_data:
        if not rows:
            continue
        table_name = f"erp_{tab_name.lower()}"
        sample = rows[0]
        if not isinstance(sample, dict):
            continue
        cols = [(k, "REAL" if isinstance(v, (int, float)) else "TEXT") for k, v in sample.items()]
        col_defs = ", ".join(f'"{k}" {t}' for k, t in cols)
        c.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        c.execute(f'CREATE TABLE "{table_name}" ({col_defs})')
        keys = list(sample.keys())
        key_str = ", ".join(f'"{k}"' for k in keys)
        placeholders = ", ".join(["?"] * len(keys))
        print(f"  写入 {table_name}: {len(rows)} 条...", flush=True)
        batch = []
        for i, row in enumerate(rows):
            vals = [json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (list, dict)) else row.get(k) for k in keys]
            batch.append(vals)
            if len(batch) >= 1000 or i == len(rows) - 1:
                c.executemany(f'INSERT INTO "{table_name}" ({key_str}) VALUES ({placeholders})', batch)
                print(f"    已写入 {i+1}/{len(rows)}", flush=True)
                batch = []
    conn.commit()
    print("  创建索引...", flush=True)
    try:
        c.execute('CREATE INDEX IF NOT EXISTS idx_wait_check_deadline ON erp_wait_check (estimateConsignTime)')
    except Exception:
        pass
    try:
        c.execute('CREATE INDEX IF NOT EXISTS idx_wait_send_deadline ON erp_wait_send_self (estimateConsignTime)')
    except Exception:
        pass
    try:
        c.execute('CREATE INDEX IF NOT EXISTS idx_finished_logistics ON erp_finished (traceStatusMsg)')
    except Exception:
        pass
    conn.commit()
    conn.close()
    print("  数据库保存完成", flush=True)

# ===== 数据校验 =====
def validate_switch(db_path, local_db, all_data):
    """校验新数据行数是否异常，返回 (ok, messages)"""
    messages = []
    new_counts = {f"erp_{t.lower()}": len(r) for t, _, r in all_data}

    if not os.path.exists(db_path):
        return True, []  # 首次运行，无历史对比

    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        old_counts = {}
        for tbl in new_counts:
            try:
                row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
                if row:
                    old_counts[tbl] = row[0]
            except Exception:
                pass
        conn.close()
    except Exception:
        return True, ["无法读取旧数据库，跳过校验"]

    for tbl, new_cnt in new_counts.items():
        old_cnt = old_counts.get(tbl, 0)
        if old_cnt > 0:
            ratio = new_cnt / old_cnt
            if ratio < 0.5:
                messages.append(f"  {tbl}: {old_cnt} → {new_cnt} (下降 {int((1-ratio)*100)}%)")
            elif ratio > 2.0:
                messages.append(f"  {tbl}: {old_cnt} → {new_cnt} (增长 {int((ratio-1)*100)}%)")

    if messages:
        return False, messages
    return True, []

# ===== 原子切换 =====
def atomic_switch(db_path, local_db, all_data):
    """通过 ATTACH + 事务将新数据原子化写入主库"""
    print("  原子切换数据...", flush=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"ATTACH DATABASE '{local_db}' AS new_db")

    try:
        conn.execute("BEGIN")
        for tab_name, _, rows in all_data:
            tbl = f"erp_{tab_name.lower()}"
            # 检查 local_db 中是否存在该表（空数据时跳过创建）
            exists = conn.execute(
                "SELECT COUNT(*) FROM new_db.sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()[0]
            if not exists:
                print(f"    跳过 {tbl}（local_db 中不存在，可能为空数据）", flush=True)
                continue
            conn.execute(f'DROP TABLE IF EXISTS {tbl}')
            conn.execute(f'CREATE TABLE {tbl} AS SELECT * FROM new_db.{tbl}')
            # 重建索引
            for idx_sql in conn.execute(
                f"SELECT sql FROM new_db.sqlite_master WHERE type='index' AND tbl_name='{tbl}' AND sql IS NOT NULL"
            ).fetchall():
                try:
                    conn.execute(idx_sql[0])
                except Exception:
                    pass
        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        conn.execute(f"DETACH DATABASE new_db")
        conn.close()
        raise e

    conn.execute(f"DETACH DATABASE new_db")
    conn.close()
    os.remove(local_db)
    print("  原子切换完成", flush=True)

# ===== 鲜度元数据 =====
def save_sync_metadata(db_path, all_data):
    """写入 sync_metadata 表"""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS sync_metadata (sync_time TEXT, table_name TEXT, row_count INTEGER)")
    now = datetime.now().isoformat()
    conn.execute("DELETE FROM sync_metadata")
    total = 0
    for tab_name, _, rows in all_data:
        tbl = f"erp_{tab_name.lower()}"
        cnt = len(rows)
        total += cnt
        conn.execute("INSERT INTO sync_metadata VALUES (?, ?, ?)", (now, tbl, cnt))
    conn.execute("INSERT INTO sync_metadata VALUES (?, ?, ?)", (now, "__total__", total))
    conn.commit()
    conn.close()
    print(f"  鲜度元数据已记录: {total} 条 @ {now}", flush=True)


def main():
    if not TOKEN:
        print("❌ 请设置环境变量 ERP_TOKEN")
        sys.exit(1)

    print("📥 并发拉取数据...")
    all_data = fetch_all_tabs(TOKEN, SHOP)

    # 获取数据库写锁，防止与物流轨迹脚本并发写入
    lock_path = DB_PATH + ".lock"
    lock_fd = open(lock_path, "w")
    print("  等待数据库写锁...", flush=True)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    try:
        _do_write(all_data, LOCAL_DB, DB_PATH)
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _do_write(all_data, local_db, db_path):
    print(f"💾 保存到本地数据库 {local_db}...", flush=True)
    save_sqlite(all_data, local_db)

    # 数据量校验
    force = os.environ.get("FORCE_SWITCH") == "1"
    ok, messages = validate_switch(db_path, local_db, all_data)
    if not ok and not force:
        print("❌ 数据量异常，拒绝切换:", flush=True)
        for m in messages:
            print(m, flush=True)
        print("  请检查 ERP Token 是否有效、API 是否正常", flush=True)
        if os.path.exists(local_db):
            os.remove(local_db)
        sys.exit(1)

    # 原子切换
    if os.path.exists(db_path):
        # 复制衍生表到新库（财务、售后等非 ERP 表）
        src_conn = sqlite3.connect(db_path, timeout=30.0)
        extra_tables = []
        for row in src_conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if row[0] not in ERP_TABLES + ["sqlite_sequence", "sync_metadata"]:
                extra_tables.append(row[0])
        if extra_tables:
            print(f"  保留衍生表: {', '.join(extra_tables)}", flush=True)
            src_conn.execute(f"ATTACH DATABASE '{local_db}' AS dst")
            for tbl in extra_tables:
                src_conn.execute(f"DROP TABLE IF EXISTS dst.{tbl}")
                src_conn.execute(f"CREATE TABLE dst.{tbl} AS SELECT * FROM {tbl}")
                for idx in src_conn.execute(
                    f"SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='{tbl}' AND sql IS NOT NULL"
                ).fetchall():
                    try:
                        src_conn.execute(idx[0])
                    except Exception:
                        pass
            src_conn.commit()
            src_conn.close()

        atomic_switch(db_path, local_db, all_data)
    else:
        shutil.move(local_db, db_path)

    # 记录鲜度元数据
    save_sync_metadata(db_path, all_data)
    print(f"✅ 数据入库完成: {sum(len(r) for _, _, r in all_data)} 条")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Script failed: %s", e)
        raise
