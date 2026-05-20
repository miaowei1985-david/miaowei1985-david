#!/usr/bin/env python3
"""物流报告页面"""
import os, json, sqlite3, time
from datetime import datetime
from collections import defaultdict

from erp_config import DB_PATH, SHOP_NAME
from dashboard import page, BASE, CACHE_TTL

_logistics_cache = None

def render_logistics_cached():
    global _logistics_cache
    try:
        now = time.time()
        if _logistics_cache and now - _logistics_cache[0] < CACHE_TTL:
            return _logistics_cache[1]
        html = render_logistics()
        if html:
            _logistics_cache = (now, html)
        return html
    except Exception as e:
        import traceback
        print(f"[logistics_cached] Error: {e}")
        traceback.print_exc()
        if _logistics_cache:
            return _logistics_cache[1]
        return f"<h1>物流页面加载失败</h1><pre>{e}</pre>"

def render_logistics():
    """物流轨迹详细报告"""
    import sqlite3
    import json
    from datetime import datetime, timedelta
    conn = sqlite3.connect(DB_PATH, timeout=30.0)

    # 快递状态统计 — 按最新状态的 operate_category
    stats = {}
    for row in conn.execute("""
        SELECT t.operate_category, COUNT(DISTINCT t.logistics_no)
        FROM logistics_trace t
        INNER JOIN (
            SELECT logistics_no, MAX(operate_time) as max_time
            FROM logistics_trace GROUP BY logistics_no
        ) latest ON t.logistics_no = latest.logistics_no AND t.operate_time = latest.max_time
        GROUP BY t.operate_category
    """).fetchall():
        stats[row[0]] = row[1]

    # 物流阶段统计 — 按最新状态的 pipeline_stage（真实节点）
    pstages = {}
    for row in conn.execute("""
        SELECT t.pipeline_stage, COUNT(DISTINCT t.logistics_no)
        FROM logistics_trace t
        INNER JOIN (
            SELECT logistics_no, MAX(operate_time) as max_time
            FROM logistics_trace GROUP BY logistics_no
        ) latest ON t.logistics_no = latest.logistics_no AND t.operate_time = latest.max_time
        GROUP BY t.pipeline_stage
    """).fetchall():
        pstages[row[0]] = row[1]

    signed = stats.get("已签收", 0)
    # 跨境物流核心阶段
    thai_pickup = pstages.get("泰国揽收", 0)
    bangkok_port = pstages.get("曼谷口岸", 0)
    bangkok_flight = pstages.get("曼谷飞行", 0)
    sz_airport = pstages.get("深圳机场", 0)
    sz_sorting = pstages.get("深圳分拣", 0)
    customs = pstages.get("海关清关", 0)
    domestic_transfer = pstages.get("国内转运", 0)
    east_china = pstages.get("华东转运", 0)
    domestic_delivery = pstages.get("国内派送", 0)
    delivering = pstages.get("派送中", 0)
    problem = stats.get("问题件", 0)
    # 其他状态
    picked = stats.get("已揽件", 0)
    transit_sort = stats.get("中转分拣", 0)
    cross_transfer = stats.get("跨城转运", 0)
    rejected = stats.get("拒收", 0)
    pending_pick = stats.get("待揽件", 0)
    coop_pickup = stats.get("合作点自提", 0)
    locker = stats.get("快递柜投放", 0)
    rail = stats.get("铁路运输", 0)
    self_pick = stats.get("指定自取", 0)
    claim_pick = stats.get("领取成功", 0)
    order_cancel = stats.get("订单取消", 0)
    long_haul = stats.get("长途运输", 0)
    other_cat = stats.get("其他", 0)
    total = sum(stats.values())

    # 物流节点时效：按trace_info识别关键阶段（揽收→曼谷→国内机场→转运→派送）
    pipeline_avg = {}
    pipeline_stuck = []
    stage_map = {}  # {logistics_no: {stage_name: datetime}}

    for row in conn.execute("""
        SELECT logistics_no, trace_info, MIN(operate_time) as first_time
        FROM logistics_trace
        GROUP BY logistics_no, trace_info
    """).fetchall():
        no, info, t = row
        info = info or ""
        if not info:
            continue
        if no not in stage_map:
            stage_map[no] = {}
        if any(k in info for k in ["尖竹汶", "揽收点", "已收取快件"]):
            stage_map[no].setdefault("揽收", t)
        if "曼谷" in info:
            stage_map[no].setdefault("曼谷机场", t)
        if any(k in info for k in ["机场转运中心", "深圳机场"]):
            stage_map[no].setdefault("国内机场", t)
        if "转运中心" in info and "机场" not in info:
            stage_map[no].setdefault("转运中心", t)
        if "正在派送" in info or "已派送成功" in info or "已签收" in info:
            stage_map[no].setdefault("派送签收", t)

    segments = [("揽收", "曼谷机场"), ("曼谷机场", "国内机场"), ("国内机场", "转运中心"), ("转运中心", "派送签收")]
    for seg_from, seg_to in segments:
        durations = []
        for no, stages in stage_map.items():
            if seg_from in stages and seg_to in stages:
                try:
                    d1 = datetime.fromisoformat(stages[seg_from])
                    d2 = datetime.fromisoformat(stages[seg_to])
                    h = (d2 - d1).total_seconds() / 3600
                    if h > 0:
                        durations.append(h)
                except Exception:
                    pass
        avg = round(sum(durations) / len(durations), 1) if durations else 0
        pipeline_avg[(seg_from, seg_to)] = avg

    # 当前停留过久的未签收订单
    signed_nos = set()
    for row in conn.execute("SELECT DISTINCT logistics_no FROM logistics_trace WHERE operate_desc = '已签收'"):
        signed_nos.add(row[0])

    for no, stages in stage_map.items():
        if no in signed_nos:
            continue
        latest_stage = None
        latest_time = None
        for s_name in ["派送签收", "转运中心", "国内机场", "曼谷机场", "揽收"]:
            if s_name in stages:
                latest_stage = s_name
                try:
                    latest_time = datetime.fromisoformat(stages[s_name])
                except Exception:
                    latest_time = None
                break
        if latest_stage and latest_time:
            stuck_hours = round((datetime.now() - latest_time).total_seconds() / 3600, 1)
            idx_map = {"揽收": 0, "曼谷机场": 1, "国内机场": 2, "转运中心": 3, "派送签收": 4}
            idx = idx_map.get(latest_stage, -1)
            next_stage = None
            if idx < len(segments):
                next_stage = segments[idx][1]
            if next_stage and stuck_hours > 12:
                pipeline_stuck.append((no, latest_stage, next_stage, stuck_hours))

    pipeline_stuck.sort(key=lambda x: x[3], reverse=True)
    pipeline_stuck = pipeline_stuck[:50]

    # 问题件
    problem_list = conn.execute("""
        SELECT t.logistics_no, MAX(t.operate_time), t.current_addr, t.trace_info
        FROM logistics_trace t
        WHERE t.operate_desc = '问题件'
        GROUP BY t.logistics_no
        ORDER BY MAX(t.operate_time) DESC
        LIMIT 20
    """).fetchall()

    # 时效分布
    timing_stats = {}
    for row in conn.execute("""
        SELECT
            CASE
                WHEN hours < 24 THEN '0-24h'
                WHEN hours < 48 THEN '24-48h'
                WHEN hours < 72 THEN '48-72h'
                WHEN hours < 96 THEN '72-96h'
                ELSE '96h+'
            END, COUNT(*)
        FROM (
            SELECT l1.logistics_no,
                   (julianday(l2.operate_time) - julianday(l1.operate_time)) * 24 as hours
            FROM logistics_trace l1
            JOIN logistics_trace l2 ON l1.logistics_no = l2.logistics_no
            WHERE l1.operate_desc = '已揽件'
            AND l2.operate_desc = '已签收'
            AND l1.operate_time < l2.operate_time
        ) GROUP BY 1
    """).fetchall():
        timing_stats[row[0]] = row[1]

    # 快递公司时效
    courier_stats = conn.execute("""
        SELECT l.logistics_name,
               ROUND(AVG((julianday(l2.operate_time) - julianday(l1.operate_time)) * 24), 1),
               COUNT(*)
        FROM logistics_trace l1
        JOIN logistics_trace l2 ON l1.logistics_no = l2.logistics_no
        JOIN (SELECT DISTINCT logistics_no, logistics_name FROM logistics_trace) l
             ON l1.logistics_no = l.logistics_no
        WHERE l1.operate_desc = '已揽件'
        AND l2.operate_desc = '已签收'
        AND l1.operate_time < l2.operate_time
        GROUP BY l.logistics_name
    """).fetchall()

    # 今日签收
    today_signed_list = conn.execute("""
        SELECT DISTINCT logistics_no, MAX(operate_time), current_addr
        FROM logistics_trace
        WHERE operate_desc = '已签收' AND operate_time >= date('now')
        GROUP BY logistics_no
        ORDER BY MAX(operate_time) DESC
        LIMIT 20
    """).fetchall()

    # 7天签收趋势
    daily_signed = {}
    for row in conn.execute("""
        SELECT SUBSTR(operate_time, 1, 10), COUNT(DISTINCT logistics_no)
        FROM logistics_trace
        WHERE operate_desc = '已签收'
        AND operate_time >= date('now', '-7 days')
        GROUP BY 1
    """).fetchall():
        daily_signed[row[0]] = row[1]
    days_7 = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        days_7.append((d[5:], daily_signed.get(d, 0)))
    day_labels = [d[0] for d in days_7]
    day_counts = [d[1] for d in days_7]

    conn.close()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""
<style>
  .log-stat {{ background:#161b22; border:1px solid #30363d; border-top:3px solid var(--accent); border-radius:10px; padding:16px; text-align:center; transition:all .2s ease; }}
  .log-stat:hover {{ transform:translateY(-2px); box-shadow:0 4px 16px rgba(0,0,0,0.3); }}
  .log-card {{ background:#161b22; border:1px solid #30363d; border-top:3px solid var(--accent); border-radius:10px; overflow:hidden; }}
  .log-card-h {{ padding:12px 16px; border-bottom:1px solid #30363d; }}
  .log-card-b {{ padding:0; }}
  .log-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:16px; }}
  @media(max-width:900px){{ .log-grid{{grid-template-columns:1fr; }} }}
</style>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script><h2 class="section-title">物流报告</h2>

<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px;">
  <div class="log-stat" style="--accent:#00d4ff;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">总快递数</div>
    <div style="color:#fff;font-size:28px;font-weight:700;">{format(total, ",")}</div>
  </div>
  <div class="log-stat" style="--accent:#00e676;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">已签收</div>
    <div style="color:#00e676;font-size:28px;font-weight:700;">{format(signed, ",")}</div>
    <div style="color:#8b949e;font-size:11px;">{round(signed/total*100,1) if total>0 else 0}%</div>
  </div>
  <div class="log-stat" style="--accent:#f85149;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">问题件</div>
    <div style="color:#f85149;font-size:28px;font-weight:700;">{problem}</div>
  </div>
</div>

<!-- 跨境物流管道: 泰国→中国 -->
<h3 style="color:#a855f7;font-size:14px;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid #30363d;">&#127794; 跨境物流管道（泰国→中国）</h3>
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px;">
  <div class="log-stat" style="--accent:#22c55e;min-width:120px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">泰国揽收</div>
    <div style="color:#22c55e;font-size:24px;font-weight:700;">{thai_pickup}</div>
  </div>
  <div class="log-stat" style="--accent:#10b981;min-width:120px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">曼谷口岸</div>
    <div style="color:#10b981;font-size:24px;font-weight:700;">{bangkok_port}</div>
    <div style="color:#8b949e;font-size:10px;">等待发往中国</div>
  </div>
  <div class="log-stat" style="--accent:#06b6d4;min-width:120px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">曼谷→深圳飞行</div>
    <div style="color:#06b6d4;font-size:24px;font-weight:700;">{bangkok_flight}</div>
    <div style="color:#8b949e;font-size:10px;">顺丰专机已起飞</div>
  </div>
  <div class="log-stat" style="--accent:#3b82f6;min-width:120px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">深圳机场到达</div>
    <div style="color:#3b82f6;font-size:24px;font-weight:700;">{sz_airport}</div>
  </div>
  <div class="log-stat" style="--accent:#f59e0b;min-width:120px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">海关清关</div>
    <div style="color:#f59e0b;font-size:24px;font-weight:700;">{customs}</div>
    <div style="color:#8b949e;font-size:10px;">清关进行中</div>
  </div>
  <div class="log-stat" style="--accent:#8b5cf6;min-width:120px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">深圳分拣</div>
    <div style="color:#8b5cf6;font-size:24px;font-weight:700;">{sz_sorting}</div>
  </div>
  <div class="log-stat" style="--accent:#ec4899;min-width:120px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">国内转运</div>
    <div style="color:#ec4899;font-size:24px;font-weight:700;">{domestic_transfer}</div>
  </div>
  <div class="log-stat" style="--accent:#a78bfa;min-width:120px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">华东转运</div>
    <div style="color:#a78bfa;font-size:24px;font-weight:700;">{east_china}</div>
  </div>
  <div class="log-stat" style="--accent:#14b8a6;min-width:120px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">末端派送</div>
    <div style="color:#14b8a6;font-size:24px;font-weight:700;">{domestic_delivery + delivering}</div>
  </div>
</div>

<!-- 其他状态 -->
<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:24px;">
  <div class="log-stat" style="--accent:#f59e0b;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">已揽件</div>
    <div style="color:#f59e0b;font-size:22px;font-weight:700;">{picked}</div>
  </div>
  <div class="log-stat" style="--accent:#a855f7;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">中转分拣</div>
    <div style="color:#a855f7;font-size:22px;font-weight:700;">{transit_sort}</div>
  </div>
  <div class="log-stat" style="--accent:#14b8a6;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">待揽件</div>
    <div style="color:#14b8a6;font-size:22px;font-weight:700;">{pending_pick}</div>
  </div>
  <div class="log-stat" style="--accent:#eab308;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">合作点自提</div>
    <div style="color:#eab308;font-size:22px;font-weight:700;">{coop_pickup}</div>
  </div>
  <div class="log-stat" style="--accent:#6366f1;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">快递柜</div>
    <div style="color:#6366f1;font-size:22px;font-weight:700;">{locker}</div>
  </div>
  <div class="log-stat" style="--accent:#ec4899;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">领取成功</div>
    <div style="color:#ec4899;font-size:22px;font-weight:700;">{claim_pick}</div>
  </div>
  <div class="log-stat" style="--accent:#22c55e;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">铁路运输</div>
    <div style="color:#22c55e;font-size:22px;font-weight:700;">{rail}</div>
  </div>
  <div class="log-stat" style="--accent:#f97316;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">指定自取</div>
    <div style="color:#f97316;font-size:22px;font-weight:700;">{self_pick}</div>
  </div>
  <div class="log-stat" style="--accent:#64748b;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">长途运输</div>
    <div style="color:#64748b;font-size:22px;font-weight:700;">{long_haul}</div>
  </div>
  <div class="log-stat" style="--accent:#a1a1aa;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">拒收</div>
    <div style="color:#a1a1aa;font-size:22px;font-weight:700;">{rejected}</div>
  </div>
  <div class="log-stat" style="--accent:#78716c;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">订单取消</div>
    <div style="color:#78716c;font-size:22px;font-weight:700;">{order_cancel}</div>
  </div>
  <div class="log-stat" style="--accent:#57534e;min-width:100px;flex:1;">
    <div style="color:#8b949e;font-size:11px;">其他</div>
    <div style="color:#57534e;font-size:22px;font-weight:700;">{other_cat}</div>
  </div>
</div>

<!-- 泰国路线时效 -->
<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px;">
  <div class="log-stat" style="--accent:#00d4ff;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">揽收 → 曼谷机场</div>
    <div style="color:#00d4ff;font-size:28px;font-weight:700;">{pipeline_avg.get(("揽收", "曼谷机场"), 0)}h</div>
    <div style="color:#8b949e;font-size:11px;">平均耗时</div>
  </div>
  <div class="log-stat" style="--accent:#a855f7;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">曼谷机场 → 国内机场</div>
    <div style="color:#a855f7;font-size:28px;font-weight:700;">{pipeline_avg.get(("曼谷机场", "国内机场"), 0)}h</div>
    <div style="color:#8b949e;font-size:11px;">平均耗时</div>
  </div>
  <div class="log-stat" style="--accent:#3b82f6;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">国内机场 → 转运中心</div>
    <div style="color:#3b82f6;font-size:28px;font-weight:700;">{pipeline_avg.get(("国内机场", "转运中心"), 0)}h</div>
    <div style="color:#8b949e;font-size:11px;">平均耗时</div>
  </div>
  <div class="log-stat" style="--accent:#00e676;min-width:140px;flex:1;">
    <div style="color:#8b949e;font-size:12px;">转运中心 → 派送签收</div>
    <div style="color:#00e676;font-size:28px;font-weight:700;">{pipeline_avg.get(("转运中心", "派送签收"), 0)}h</div>
    <div style="color:#8b949e;font-size:11px;">平均耗时</div>
  </div>
</div>

<div class="log-grid">"""

    if pipeline_stuck:
        stuck_rows = ""
        for no, stage_from, stage_to, stuck_h in pipeline_stuck:
            c = "#f85149" if stuck_h > 48 else "#f97316" if stuck_h > 24 else "#d29922"
            stuck_rows += f'<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#fff;font-family:monospace;font-size:12px;">{no}</td><td style="padding:8px;color:#8b949e;font-size:12px;">{stage_from}</td><td style="padding:8px;color:#8b949e;font-size:12px;">{stage_to}</td><td style="padding:8px;text-align:right;"><span style="color:{c};font-weight:700;">{stuck_h}h</span></td></tr>'

        html += f'''
  <div class="log-card" style="--accent:#f97316;">
    <div class="log-card-h" style="background:rgba(249,115,22,0.1);">
      <span style="color:#f97316;font-weight:700;font-size:14px;">&#9888; 滞留件</span>
      <span style="color:#8b949e;font-size:12px;margin-left:8px;">在某一阶段停留超12h ({len(pipeline_stuck)})</span>
    </div>
    <div class="log-card-b" style="max-height:320px;overflow-y:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="border-bottom:1px solid #30363d;">
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">快递单号</th>
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">当前阶段</th>
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">下一阶段</th>
      <th style="padding:8px;text-align:right;color:#8b949e;font-size:12px;">停留</th>
      </tr></thead><tbody>{stuck_rows}</tbody></table>
    </div>
  </div>'''

    if problem_list:
        problem_rows = ""
        for r in problem_list:
            info = (r[3] or "-")[:60]
            time_str = (r[1] or "")[:16]
            problem_rows += f'<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#fff;font-family:monospace;font-size:12px;">{r[0]}</td><td style="padding:8px;color:#8b949e;font-size:12px;">{time_str}</td><td style="padding:8px;color:#8b949e;font-size:12px;">{r[2] or "-"}</td><td style="padding:8px;font-size:12px;color:#f97316;">{info}</td></tr>'

        html += f'''
  <div class="log-card" style="--accent:#f97316;">
    <div class="log-card-h" style="background:rgba(249,115,22,0.1);">
      <span style="color:#f97316;font-weight:700;font-size:14px;">&#9888; 问题件</span>
      <span style="color:#8b949e;font-size:12px;margin-left:8px;">{len(problem_list)} 件</span>
    </div>
    <div class="log-card-b" style="max-height:320px;overflow-y:auto;">
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <thead><tr style="border-bottom:1px solid #30363d;">
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">快递单号</th>
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">时间</th>
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">城市</th>
      <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">问题</th>
      </tr></thead><tbody>{problem_rows}</tbody></table>
    </div>
  </div>'''

    # 时效分布
    timing_html = ""
    for bucket in ["0-24h", "24-48h", "48-72h", "72-96h", "96h+"]:
        cnt = timing_stats.get(bucket, 0)
        total_t = sum(timing_stats.values()) if timing_stats else 1
        pct = round(cnt / total_t * 100, 1) if total_t > 0 else 0
        color = "#00e676" if bucket in ["0-24h", "24-48h"] else "#d29922" if bucket == "48-72h" else "#f85149"
        timing_html += f'<div class="log-stat" style="--accent:{color};"><div style="color:#8b949e;font-size:11px;">{bucket}</div><div style="color:{color};font-size:15px;font-weight:700;">{cnt}</div><div style="color:#8b949e;font-size:10px;">{pct}%</div></div>'

    html += f'''
</div>

<h2 style="color:#00d4ff;font-size:16px;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid #30363d;">时效分布</h2>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:24px;">{timing_html}</div>

<div class="chart-grid" style="margin-bottom:16px;">
  <div class="chart-card" style="--accent:#00d4ff;">
    <h3>物流状态分布</h3>
    <canvas id="chart_log_status" style="max-height:200px;"></canvas>
  </div>
  <div class="chart-card" style="--accent:#00e676;">
    <h3>近7天签收趋势</h3>
    <canvas id="chart_log_signed" style="max-height:200px;"></canvas>
  </div>
</div>'''

    # 快递公司时效
    courier_html = ""
    max_h = max([r[1] for r in courier_stats]) if courier_stats else 1
    for name, avg_h, cnt in courier_stats:
        bar_w = int(avg_h / max_h * 80)
        color = "#00e676" if avg_h < 60 else "#d29922" if avg_h < 80 else "#f85149"
        courier_html += f'<tr style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#c9d1d9;">{name}</td><td style="padding:8px;"><span style="display:inline-block;width:{bar_w}px;height:10px;background:{color};border-radius:2px;vertical-align:middle;"></span></td><td style="padding:8px;color:{color};font-weight:600;text-align:right;">{avg_h}h</td><td style="padding:8px;color:#8b949e;text-align:right;">{cnt}</td></tr>'

    html += f'''
<div class="log-card" style="--accent:#a855f7;margin-bottom:16px;">
  <div class="log-card-h">
    <span style="color:#a855f7;font-weight:600;font-size:14px;">快递公司时效对比</span>
  </div>
  <div class="log-card-b">
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
    <thead><tr style="border-bottom:1px solid #30363d;">
    <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">快递公司</th>
    <th style="padding:8px;text-align:left;color:#8b949e;font-size:12px;">进度</th>
    <th style="padding:8px;text-align:right;color:#8b949e;font-size:12px;">平均时效</th>
    <th style="padding:8px;text-align:right;color:#8b949e;font-size:12px;">样本数</th>
    </tr></thead><tbody>{courier_html}</tbody></table>
  </div>
</div>'''

    # 今日签收
    today_rows = ""
    for r in today_signed_list:
        time_str = (r[1] or "")[11:16] if len(r[1]) > 11 else r[1]
        today_rows += f'<tr style="border-bottom:1px solid #21262d;"><td style="padding:6px 8px;color:#fff;font-family:monospace;font-size:12px;">{r[0]}</td><td style="padding:6px 8px;color:#8b949e;font-size:12px;">{time_str}</td><td style="padding:6px 8px;color:#8b949e;font-size:12px;">{r[2] or "-"}</td></tr>'

    html += f'''
<div class="log-card" style="--accent:#00e676;margin-bottom:24px;">
  <div class="log-card-h">
    <span style="color:#00e676;font-weight:600;font-size:14px;">今日签收 ({len(today_signed_list)})</span>
  </div>
  <div class="log-card-b" style="max-height:300px;overflow-y:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
    <thead><tr style="border-bottom:1px solid #30363d;">
    <th style="padding:6px 8px;text-align:left;color:#8b949e;font-size:12px;">快递单号</th>
    <th style="padding:6px 8px;text-align:left;color:#8b949e;font-size:12px;">时间</th>
    <th style="padding:6px 8px;text-align:left;color:#8b949e;font-size:12px;">城市</th>
    </tr></thead><tbody>{today_rows}</tbody></table>
  </div>
</div>

<p style="color:#484f58;font-size:12px;text-align:center;">数据更新: {now}</p>

<script>
Chart.defaults.color = '#8b949e';
Chart.defaults.borderColor = '#21262d';

var _logStatus = new Chart(document.getElementById('chart_log_status'), {{
  type: 'bar',
  data: {{
    labels: ['已签收', '曼谷口岸', '海关清关', '国内转运', '问题件', '华东转运', '曼谷飞行', '深圳机场', '末端派送', '泰国揽收'],
    datasets: [{{
      data: [{signed}, {bangkok_port}, {customs}, {domestic_transfer}, {problem}, {east_china}, {bangkok_flight}, {sz_airport}, {domestic_delivery + delivering}, {thai_pickup}],
      backgroundColor: ['#00e676', '#10b981', '#f59e0b', '#ec4899', '#f85149', '#a78bfa', '#06b6d4', '#3b82f6', '#14b8a6', '#22c55e'],
      borderRadius: 6, barThickness: 32
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ grid: {{ display: false }} }}, y: {{ grid: {{ color: '#21262d' }}, beginAtZero: true }} }},
    animation: {{ duration: 800 }}
  }}
}});

var _logSigned = new Chart(document.getElementById('chart_log_signed'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(day_labels)},
    datasets: [{{
      data: {json.dumps(day_counts)},
      borderColor: '#00e676', backgroundColor: 'rgba(0,230,118,0.1)',
      fill: true, tension: 0.3, pointRadius: 4
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ x: {{ grid: {{ display: false }} }}, y: {{ grid: {{ color: '#21262d' }}, beginAtZero: true }} }},
    animation: {{ duration: 800 }}
  }}
}});

</script>
'''

    return page("物流报告", "logistics", html)
