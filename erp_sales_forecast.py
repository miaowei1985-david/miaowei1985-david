#!/usr/bin/env python3
"""
AI 销量预测 V2 — 加入全国榴莲进口季节系数
季节系数来源：2024年中国榴莲进口数据
- 6月为全年峰值，占全年36.5%
- 4-7月为旺季，占全年60%+
- 11-1月为淡季

生成时间: 2026-05-11
"""
import sqlite3
from datetime import datetime, timedelta

DB = "/Users/macmini4pro/pdd/Claudecode/erp_all.db"
conn = sqlite3.connect(DB)

# ===== 全国榴莲进口季节系数（2024年数据映射）=====
# 以全年平均月进口量为 1.0，各月相对系数
# 6月占全年36.5% → 6月系数 = 36.5% / (1/12) = 4.38 → 但这是单月极端峰值
# 更合理的做法是用月度进口量占比计算系数：
MONTHLY_COEFF = {
    1:  0.35,   # 1月淡季
    2:  0.55,   # 2月淡季转暖
    3:  0.85,   # 3月升温
    4:  1.65,   # 4月旺季起点
    5:  2.80,   # 5月旺季中段（当前）
    6:  3.50,   # 6月全年峰值（36.5%/月平均=4.38，折中取3.5）
    7:  2.20,   # 7月旺季尾声
    8:  1.20,   # 8月回落
    9:  0.90,   # 9月平稳
    10: 0.70,   # 10月降温
    11: 0.40,   # 11月淡季
    12: 0.30,   # 12月低谷
}

now = datetime.now()
current_month = now.month

# 计算未来30天每日的季节系数（线性插值）
daily_coeff = {}
for i in range(1, 31):
    d = now + timedelta(days=i)
    m = d.month
    daily_coeff[d.strftime("%Y-%m-%d")] = MONTHLY_COEFF.get(m, 1.0)

days = ["周日","周一","周二","周三","周四","周五","周六"]

# ===== 主力SKU预测 =====
target_skus = [
    ("260418269071002", "金枕8-10斤"),
    ("260407269071202", "5-6斤/1个"),
    ("260426269071002", "新SKU ¥220"),
]

print("=" * 75)
print("  未来 7 天销量预测 V2（含全国进口季节系数）")
print(f"  生成时间: {now.strftime('%Y-%m-%d %H:%M')}")
print(f"  当前月份季节系数: {MONTHLY_COEFF.get(current_month, 1.0):.2f}x")
print(f"  下月({current_month+1 if current_month<12 else 1}月)季节系数: {MONTHLY_COEFF.get(current_month+1 if current_month<12 else 1, 1.0):.2f}x")
print("=" * 75)

all_forecasts = []

for sku_no, sku_label in target_skus:
    print(f"\n{'─' * 70}")
    print(f"  {sku_label} ({sku_no})")
    print(f"{'─' * 70}")
    print(f"  {'日期':<10} | {'星期':<4} | {'历史中位':>8} | {'近期均值':>8} | {'季节系数':>7} | {'预测':>7}")
    print(f"  {'─'*10}-+-{'─'*4}-+-{'─'*8}-+-{'─'*8}-+-{'─'*7}-+-{'─'*7}")

    sku_total = 0

    for i in range(1, 8):
        d = now + timedelta(days=i)
        dow = d.weekday() + 1  # Python: 0=Mon → 1=Mon (same as SQLite %w except %w uses 0=Sun)
        # Adjust: Python Monday=0, SQLite %w Monday=1
        dow_sqlite = (d.weekday() + 1) % 7

        date_str = d.strftime("%m/%d")
        coeff = daily_coeff.get(d.strftime("%Y-%m-%d"), 1.0)

        # 历史中位数（排除最近30天促销期）
        cur = conn.execute("""
            SELECT daily_qty FROM daily_sales_features
            WHERE sku_no = ? AND day_of_week = ?
              AND sale_date <= date('now', '-30 days') AND daily_qty > 0
        """, (sku_no, dow_sqlite))
        hist_vals = sorted([r[0] for r in cur if r[0] > 0])

        if hist_vals:
            n = len(hist_vals)
            median = hist_vals[n // 2] if n % 2 else (hist_vals[n//2 - 1] + hist_vals[n//2]) / 2
        else:
            cur = conn.execute("""
                SELECT daily_qty FROM daily_sales_features
                WHERE sku_no = ? AND day_of_week = ? AND daily_qty > 0
            """, (sku_no, dow_sqlite))
            hist_vals = sorted([r[0] for r in cur if r[0] > 0])
            if hist_vals:
                n = len(hist_vals)
                median = hist_vals[n // 2] if n % 2 else (hist_vals[n//2 - 1] + hist_vals[n//2]) / 2
            else:
                median = 0

        # 近期7天均值
        cur = conn.execute("""
            SELECT AVG(daily_qty) FROM daily_sales_features
            WHERE sku_no = ? AND day_of_week = ? AND sale_date >= date('now', '-7 days')
        """, (sku_no, dow_sqlite))
        r = cur.fetchone()
        recent_avg = r[0] if r[0] else 0

        # 基线 = max(历史中位数, 近期均值)
        base = max(median, recent_avg)

        # 季节调整
        # 如果当前已在旺季（系数>1），实际观测值已反映季节因素，不再重复放大
        # 如果历史基线来自淡季（中位数），需要放大到当前季节水平
        # 逻辑：历史中位数来自全年混合数据，包含了淡季旺季
        # 当前月份系数 / 全年平均系数(1.0) 作为调整
        if current_month >= 4 and current_month <= 7:
            # 旺季期间：近期数据已反映当前季节，直接用近期均值
            if recent_avg > 0:
                forecast = recent_avg
            else:
                forecast = median * coeff
        elif recent_avg > 0 and median > 0:
            # 非旺季：用近期均值外推
            ratio = recent_avg / median if median > 0 else 1.0
            forecast = median * ratio * coeff
        else:
            forecast = median * coeff

        sku_total += forecast
        print(f"  {date_str:<10} | {days[dow_sqlite]:<4} | {median:>8.0f} | {recent_avg:>8.0f} | {coeff:>6.2f}x | {forecast:>7.0f}")

    all_forecasts.append((sku_label, sku_total))

# ===== 未来7天总量 =====
print(f"\n{'=' * 75}")
print("  未来7天总量预测（含季节调整）")
print(f"{'=' * 75}")
future_start = (now + timedelta(days=1)).strftime("%m/%d")
future_end = (now + timedelta(days=7)).strftime("%m/%d")
print(f"  预测区间: {future_start} ~ {future_end}")
for sku_label, total in all_forecasts:
    print(f"  {sku_label}: 约 {total:,.0f} 件")

# ===== 6月前瞻 =====
print(f"\n{'=' * 75}")
print("  6月前瞻（全年峰值，季节系数 3.5x）")
print(f"{'=' * 75}")
june_coeff = MONTHLY_COEFF.get(6, 1.0)
print(f"  当前(5月)系数: {MONTHLY_COEFF.get(5, 1.0):.2f}x")
print(f"  6月系数: {june_coeff:.2f}x → 较5月增长 {(june_coeff/MONTHLY_COEFF.get(5,1.0)-1)*100:.0f}%")
print()
for sku_no, sku_label in target_skus:
    cur = conn.execute("""
        SELECT AVG(daily_qty) FROM daily_sales_features
        WHERE sku_no = ? AND sale_date >= date('now', '-7 days')
    """, (sku_no,))
    r = cur.fetchone()
    recent = r[0] if r[0] else 0
    if recent > 0:
        june_daily = recent * (june_coeff / MONTHLY_COEFF.get(5, 1.0))
        june_monthly = june_daily * 30
        print(f"  {sku_label}: 6月日均 ~{june_daily:.0f} 件，全月 ~{june_monthly:,.0f} 件")

# ===== 与 V1 版本对比 =====
print(f"\n{'=' * 75}")
print("  与 V1 版本对比")
print(f"{'=' * 75}")
v1_totals = {
    "金枕8-10斤": 20145,
    "5-6斤/1个": 1288,
    "新SKU ¥220": 1052,
}
for sku_label, total in all_forecasts:
    v1 = v1_totals.get(sku_label, 0)
    if v1 > 0:
        change = (total - v1) / v1 * 100
        arrow = "↑" if change > 0 else "↓"
        print(f"  {sku_label}: V1={v1:,.0f} → V2={total:,.0f} ({arrow}{abs(change):.0f}%)")

conn.close()
