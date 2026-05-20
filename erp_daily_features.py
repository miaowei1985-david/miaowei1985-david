#!/usr/bin/env python3
"""Create daily_sales_features table for AI forecasting."""
import sqlite3

DB = "/Users/macmini4pro/pdd/Claudecode/erp_all.db"
conn = sqlite3.connect(DB)

conn.execute("DROP TABLE IF EXISTS daily_sales_features")
conn.execute("""
    CREATE TABLE daily_sales_features AS
    SELECT
        date(order_time) as sale_date,
        CAST(strftime('%w', order_time) AS INTEGER) as day_of_week,
        CASE WHEN CAST(strftime('%w', order_time) AS INTEGER) IN (1,2,3) THEN 1 ELSE 0 END as is_weekday_peak,
        CAST(strftime('%d', order_time) AS INTEGER) as day_of_month,
        sku_no,
        sku_name,
        SUM(quantity) as daily_qty,
        COUNT(*) as order_count,
        ROUND(AVG(paid), 2) as avg_price,
        ROUND(SUM(paid), 2) as daily_revenue,
        warehouse_name
    FROM order_line_items
    GROUP BY date(order_time), sku_no, warehouse_name
    ORDER BY sale_date, sku_no
""")

cur = conn.execute("SELECT COUNT(*) FROM daily_sales_features")
print(f"Features rows: {cur.fetchone()[0]}")

cur = conn.execute("SELECT MIN(sale_date), MAX(sale_date) FROM daily_sales_features")
r = cur.fetchone()
print(f"Date range: {r[0]} ~ {r[1]}")

# Top SKU trend
print("\n=== SKU 260418269071002 last 14 days ===")
cur = conn.execute("""
    SELECT sale_date, day_of_week, daily_qty, warehouse_name
    FROM daily_sales_features
    WHERE sku_no = '260418269071002'
      AND sale_date >= date('now', '-14 days')
    ORDER BY sale_date
""")
days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]
for r in cur:
    print(f"  {r[0]}({days[r[1]]}) {r[3]}: {r[2]}")

# New SKU
print("\n=== SKU 260426269071002 all ===")
cur = conn.execute("""
    SELECT sale_date, day_of_week, daily_qty, warehouse_name
    FROM daily_sales_features
    WHERE sku_no = '260426269071002'
    ORDER BY sale_date
""")
for r in cur:
    print(f"  {r[0]}({days[r[1]]}) {r[3]}: {r[2]}")

conn.close()
