#!/usr/bin/env python3
"""异常件页面 — 区分物流异常与售后异常"""
import os, json, sqlite3, time
from datetime import datetime

from erp_config import DB_PATH, SHOP_NAME
from dashboard import page

_exception_cache = None

def render_exceptionorders():
    """异常件：物流异常 + 售后异常，按真实原因分类"""
    global _exception_cache

    now = time.time()
    if _exception_cache and now - _exception_cache[0] < 300:
        return _exception_cache[1]

    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    shop = SHOP_NAME

    # ===== 统计摘要 =====
    # 物流：问题件、拒收、派送超时(72h)、揽件超时(72h)
    lg_stats = conn.execute("""
        SELECT
            (SELECT COUNT(DISTINCT logistics_no) FROM logistics_trace WHERE shop_name=? AND operate_desc='问题件'),
            (SELECT COUNT(DISTINCT logistics_no) FROM logistics_trace WHERE shop_name=? AND operate_desc='拒收'),
            (SELECT COUNT(DISTINCT l.logistics_no) FROM logistics_trace l
             WHERE l.shop_name=? AND l.operate_desc='派送中'
             AND l.operate_time < datetime('now', '-72 hours')
             AND l.logistics_no NOT IN (SELECT DISTINCT logistics_no FROM logistics_trace WHERE shop_name=? AND operate_desc='已签收')),
            (SELECT COUNT(DISTINCT l.logistics_no) FROM logistics_trace l
             WHERE l.shop_name=? AND l.operate_desc='已揽件'
             AND l.operate_time < datetime('now', '-72 hours')
             AND l.logistics_no NOT IN (SELECT DISTINCT logistics_no FROM logistics_trace WHERE shop_name=? AND operate_desc='已签收'))
    """, (shop, shop, shop, shop, shop, shop)).fetchone()
    lg_wt_cnt, lg_reject_cnt, lg_dispatch_to, lg_pickup_to = lg_stats

    # 售后：按 service_status 分组
    as_stats = {}
    for row in conn.execute(
        "SELECT service_status, COUNT(*) FROM after_sales WHERE shop_name=? GROUP BY service_status",
        (shop,)).fetchall():
        as_stats[row[0]] = row[1]
    as_audit_fail = as_stats.get("审核不通过", 0)
    as_wait_cust = as_stats.get("待客户反馈", 0)
    as_wait_merchant = as_stats.get("待商家审核", 0)
    as_wait_return = as_stats.get("待买家退货", 0)
    as_returned = as_stats.get("买家已退货", 0)
    as_pending = as_audit_fail + as_wait_cust + as_wait_merchant + as_wait_return + as_returned

    # AI分析映射
    analysis_map = {}
    for row in conn.execute("SELECT exception_id, classification, ai_explanation, confidence FROM exception_analysis").fetchall():
        analysis_map[row[0]] = (row[1], row[2], row[3])

    # ===== 物流异常 =====
    lg_trace_rows = conn.execute(
        "SELECT logistics_no, operate_desc, current_addr, operate_time FROM logistics_trace WHERE shop_name=? AND operate_desc IN ('问题件','拒收') ORDER BY operate_time DESC LIMIT 200",
        (shop,)).fetchall()
    lg_nos = [r[0] for r in lg_trace_rows]

    order_by_lg = {}
    if lg_nos:
        placeholders = ",".join(["?"] * len(lg_nos))
        for row in conn.execute(
            f"SELECT logisticsNo, srcTids, warehouseName, receiverCityName, receiverProvinceName, goodsAmount, realAmount, orderItemList, logisticsName, tradeTime, consignTime, tradeStatusFrontText, refundStatusText FROM erp_all_orders WHERE shopName=? AND logisticsNo IN ({placeholders})",
            (shop,) + tuple(lg_nos)).fetchall():
            order_by_lg[row[0]] = row[1:]

    lg_orders = []
    for tr in lg_trace_rows:
        lg_no, op_desc, cur_addr, op_time = tr
        ao = order_by_lg.get(lg_no)
        if not ao: continue
        tid = ao[0] or ""
        eid = "lg_" + lg_no
        product = "-"
        try:
            for it in (json.loads(ao[5]) if ao[5] else [])[:1]:
                sku = it.get("skuName", "") or it.get("spuName", "")
                if sku and ";" in sku: sku = sku.split(";", 1)[1].strip()
                if sku: product = f"{sku} x{it.get('skuNum', it.get('quantity', '1'))}"; break
        except Exception: pass
        hours = 0
        if op_time:
            try: hours = (datetime.now() - datetime.fromisoformat(op_time.replace(" ", "T"))).total_seconds() / 3600
            except: pass
        ai = analysis_map.get(eid)
        if ai: c, e, cf = ai
        elif (ao[11] or "") in ("全部退款", "退款成功"): c, e, cf = "已退款", f"{op_desc}（{cur_addr}），订单已全部退款。", 0.80
        elif hours > 48: c, e, cf = "需关注", f"{op_desc}在{cur_addr}停留超{int(hours)}小时。", 0.70
        else: c, e, cf = "处理中", f"{op_desc}，位于{cur_addr}。", 0.60
        lg_orders.append({"eid": eid, "tid": tid, "logistics_no": lg_no, "logistics_name": ao[7] or "", "operate_desc": op_desc or "", "current_addr": cur_addr or "", "operate_time": op_time or "", "warehouse": ao[1] or "", "receiver_city": ao[2] or "", "receiver_province": ao[3] or "", "product": product, "amount": ao[4] or 0, "trade_status": ao[10] or "", "refund_status": ao[11] or "", "consign_time": ao[9] or "", "trade_time": ao[8] or "", "hours_since": hours, "ai_class": c, "ai_exp": e, "ai_conf": cf})

    # ===== 售后异常 =====
    as_rows = conn.execute(
        "SELECT tid, service_no, service_status, primary_reason, secondary_reason, outbound_status, logistics_no, refund_amount, description, audit_opinion, apply_time FROM after_sales WHERE shop_name=? AND service_status IN ('审核不通过','待客户反馈','待商家审核','待买家退货','买家已退货') ORDER BY apply_time DESC LIMIT 200",
        (shop,)).fetchall()
    as_tids = [r[0] for r in as_rows]

    order_by_tid = {}
    if as_tids:
        placeholders = ",".join(["?"] * len(as_tids))
        for row in conn.execute(
            f"SELECT srcTids, warehouseName, receiverCityName, tradeTime, goodsAmount, orderItemList, logisticsNo, logisticsName, consignTime, tradeStatusFrontText, refundStatusText FROM erp_all_orders WHERE shopName=? AND srcTids IN ({placeholders})",
            (shop,) + tuple(as_tids)).fetchall():
            order_by_tid[row[0]] = row[1:]

    as_orders = []
    for row in as_rows:
        tid = row[0] or ""
        eid = "as_" + (row[1] or tid)
        ao = order_by_tid.get(tid)
        product = "-"
        if ao and ao[4]:
            try:
                for it in (json.loads(ao[4]) if ao[4] else [])[:1]:
                    sku = it.get("skuName", "") or it.get("spuName", "")
                    if sku and ";" in sku: sku = sku.split(";", 1)[1].strip()
                    if sku: product = f"{sku} x{it.get('skuNum', it.get('quantity', '1'))}"; break
            except: pass
        ai = analysis_map.get(eid)
        if ai: c, e, cf = ai
        elif row[2] == "审核不通过": c, e, cf = "需沟通", f"售后审核不通过（{row[3]}），需主动联系协商。", 0.85
        elif row[2] in ("待客户反馈", "待商家审核"): c, e, cf = "处理中", f"售后处理中（{row[2]}，{row[3]}）。", 0.80
        elif row[2] == "待买家退货": c, e, cf = "待退货", "已同意退货，等待买家寄回。", 0.75
        elif row[2] == "买家已退货": c, e, cf = "已退货", "客户已退货，等待仓库验货。", 0.70
        else: c, e, cf = "处理中", f"售后状态：{row[2]}（{row[3]}）。", 0.60
        as_orders.append({"eid": eid, "tid": tid, "service_no": row[1], "service_status": row[2], "primary_reason": row[3], "secondary_reason": row[4], "outbound_status": row[5], "logistics_no": row[6] or "", "refund_amount": row[7] or 0, "description": row[8] or "", "audit_opinion": row[9] or "", "apply_time": row[10] or "", "warehouse": ao[0] if ao else "", "receiver_city": ao[1] if ao else "", "product": product, "amount": ao[3] if ao else 0, "trade_time": ao[2] if ao else "", "consign_time": ao[7] if ao else "", "ao_logistics_no": ao[5] if ao else "", "logistics_name": ao[6] if ao else "", "trade_status": ao[8] if ao else "", "refund_status": ao[9] if ao else "", "ai_class": c, "ai_exp": e, "ai_conf": cf})

    conn.close()

    # ===== 渲染 HTML =====
    tc = {"问题件": "#f97316", "拒收": "#ef4444", "已退款": "#238636", "需关注": "#d29922", "处理中": "#58a6ff", "审核不通过": "#f85149", "待客户反馈": "#d29922", "待商家审核": "#a855f7", "待买家退货": "#f0883e", "买家已退货": "#3fb950", "已退货": "#3fb950", "需沟通": "#d29922", "已退款_as": "#238636"}
    p = []
    p.append('<h1 style="color:#fff;font-size:22px;margin-bottom:6px;">异常件追踪</h1>')
    p.append(f'<p style="color:#8b949e;margin-bottom:16px;font-size:13px;">物流异常 + 售后异常 | 按真实原因分类 | {datetime.now().strftime("%m/%d %H:%M")}</p>')

    # 第一行：物流异常统计
    p.append('<div style="margin-bottom:8px;"><span style="color:#8b949e;font-size:12px;font-weight:600;">物流异常</span></div>')
    p.append('<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px;">')
    for l, v, cl in [("问题件", f"{lg_wt_cnt:,}", "#f97316"), ("拒收", f"{lg_reject_cnt:,}", "#ef4444"), ("派送超时", f"{lg_dispatch_to:,}", "#e3b341"), ("揽件超时", f"{lg_pickup_to:,}", "#e3b341")]:
        p.append(f'<div style="background:#161b22;border:1px solid {cl};border-radius:10px;padding:16px;text-align:center;"><div style="color:#8b949e;font-size:12px;">{l}</div><div style="color:{cl};font-size:32px;font-weight:700;">{v}</div></div>')
    p.append('</div>')

    # 第二行：售后异常统计
    p.append('<div style="margin-bottom:8px;"><span style="color:#8b949e;font-size:12px;font-weight:600;">售后异常</span></div>')
    p.append('<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:24px;">')
    for l, v, cl in [("审核不通过", f"{as_audit_fail:,}", "#f85149"), ("待客户反馈", f"{as_wait_cust:,}", "#d29922"), ("待商家审核", f"{as_wait_merchant:,}", "#a855f7"), ("待买家退货", f"{as_wait_return:,}", "#f0883e"), ("买家已退货", f"{as_returned:,}", "#3fb950")]:
        p.append(f'<div style="background:#161b22;border:1px solid {cl};border-radius:10px;padding:16px;text-align:center;"><div style="color:#8b949e;font-size:12px;">{l}</div><div style="color:{cl};font-size:32px;font-weight:700;">{v}</div></div>')
    p.append('</div>')

    # 主 Tab 切换
    p.append('<style>.etb{padding:8px 16px;border:1px solid #30363d;background:#161b22;color:#8b949e;cursor:pointer;font-size:13px;border-radius:6px 6px 0 0}.etb.on{background:#1c2333;color:#fff;border-color:#00d4ff;border-bottom-color:#1c2333}.etb:hover{background:#21262d;color:#fff}.esb{padding:4px 10px;border:1px solid #30363d;background:#0d1117;color:#8b949e;cursor:pointer;font-size:12px;border-radius:4px}.esb.on{background:#161b22;color:#fff;border-color:#58a6ff}.esb:hover{background:#21262d;color:#fff}.eg{max-height:0;overflow:hidden;transition:max-height .3s ease}.eg.on{max-height:600px}.etg{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;color:#fff}</style>')
    p.append('<div style="display:flex;gap:4px;margin-bottom:8px;">')
    p.append(f'<button class="etb on" onclick="et(\x27logistics\x27)" id="et_logistics">物流异常 ({len(lg_orders)})</button>')
    p.append(f'<button class="etb" onclick="et(\x27aftersales\x27)" id="et_aftersales">售后异常 ({len(as_orders)})</button>')
    p.append('</div>')

    # 物流子分类
    lg_subs = {"问题件": lg_orders.count(lambda x: True), "拒收": 0}
    lg_wt = sum(1 for o in lg_orders if o["operate_desc"] == "问题件")
    lg_rj = sum(1 for o in lg_orders if o["operate_desc"] == "拒收")
    p.append('<div id="sub_logistics" style="display:flex;gap:4px;margin-bottom:8px;">')
    for s, l in [("all", f"全部 ({len(lg_orders)})"), ("问题件", f"问题件 ({lg_wt})"), ("拒收", f"拒收 ({lg_rj})")]:
        p.append(f'<button class="esb {"on" if s=="all" else ""}" onclick="es(\x27logistics\x27,\x27{s}\x27)" id="es_logistics_{s}">{l}</button>')
    p.append('</div>')

    # 售后子分类
    as_by_status = {}
    for s in ["审核不通过", "待客户反馈", "待商家审核", "待买家退货", "买家已退货"]:
        as_by_status[s] = sum(1 for o in as_orders if o["service_status"] == s)
    p.append('<div id="sub_aftersales" style="display:none;flex-wrap:wrap;gap:4px;margin-bottom:8px;">')
    p.append(f'<button class="esb on" onclick="es(\x27aftersales\x27,\x27all\x27)" id="es_aftersales_all">全部 ({len(as_orders)})</button>')
    for s, l in [("审核不通过", f"审核不通过 ({as_by_status['审核不通过']})"), ("待客户反馈", f"待客户反馈 ({as_by_status['待客户反馈']})"), ("待商家审核", f"待商家审核 ({as_by_status['待商家审核']})"), ("待买家退货", f"待买家退货 ({as_by_status['待买家退货']})"), ("买家已退货", f"买家已退货 ({as_by_status['买家已退货']})")]:
        p.append(f'<button class="esb" onclick="es(\x27aftersales\x27,\x27{s}\x27)" id="es_aftersales_{s}">{l}</button>')
    p.append('</div>')

    # 物流异常表
    p.append(f'<div id="tbl_logistics"><div style="overflow-x:auto;border:1px solid #30363d;border-radius:0 0 10px 10px;"><table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr style="background:#161b22;color:#8b949e;text-align:left;">')
    for th in ["#", "异常类型", "品名规格", "订单号", "物流单号", "当前位置", "物流名称", "停留时间", "详情"]:
        p.append(f'<th style="padding:10px 8px;border-bottom:1px solid #30363d;">{th}</th>')
    p.append('</tr></thead>')
    co = {"需关注": 0, "处理中": 1, "已退款": 2}
    lg_sorted = sorted(lg_orders, key=lambda x: (co.get(x.get("ai_class", ""), 3), -x.get("hours_since", 0) if x.get("hours_since") else 0))
    for idx, o in enumerate(lg_sorted, 1):
        ac, ae2, af = o["ai_class"], o["ai_exp"], o["ai_conf"]
        tl = tc.get(ac, "#8b949e")
        pr, am = o["product"][:40], o["amount"]
        wh = o["warehouse"]
        h = o.get("hours_since", 0)
        td3 = f'{int(h)}h' if h > 0 else '-'
        dc = o.get("operate_desc", "问题件")
        p.append(f'<tr class="er" data-m="logistics" data-s="{dc}" style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#484f58;">{idx}</td>')
        p.append(f'<td style="padding:8px;"><span class="etg" style="background:{tl};">{ac}</span><br><span style="color:#484f58;font-size:10px;">{dc}</span></td>')
        p.append(f'<td style="padding:8px;color:#c9d1d9;">{pr}</td>')
        p.append(f'<td style="padding:8px;"><code style="background:#161b22;padding:1px 4px;border-radius:3px;font-size:11px;color:#58a6ff;">{o["tid"][:16]}</code></td>')
        p.append(f'<td style="padding:8px;"><code style="background:#161b22;padding:1px 4px;border-radius:3px;font-size:11px;color:#8b949e;">{o["logistics_no"][:20]}</code></td>')
        p.append(f'<td style="padding:8px;color:#8b949e;font-size:11px;">{(o.get("current_addr") or "")[:20]}</td>')
        p.append(f'<td style="padding:8px;color:#8b949e;font-size:11px;">{(o.get("logistics_name") or "-")[:12]}</td>')
        p.append(f'<td style="padding:8px;color:#8b949e;font-size:12px;font-weight:600;">{td3}</td>')
        p.append('<td style="padding:8px;text-align:center;"><button onclick="this.closest(\'tr\').nextElementSibling.classList.toggle(\'on\');this.textContent=this.textContent==\'▼\'?\'▶\':\'▼\'" style="background:none;border:none;color:#00d4ff;cursor:pointer;">▶</button></td></tr>')
        p.append(f'<tr class="eg"><td colspan="9" style="padding:0;"><div style="padding:12px 16px;background:#161b22;"><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;">')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">AI 分析</div><div style="color:#c9d1d9;font-size:13px;line-height:1.6;">{ae2}</div><div style="color:#484f58;font-size:11px;">置信度: {af:.0%}</div></div>')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">订单详情</div><div style="color:#c9d1d9;font-size:12px;line-height:1.8;">仓库: {wh}<br>状态: {o.get("trade_status","-")}<br>退款: {o.get("refund_status","-")}<br>下单: {(o.get("trade_time") or "")[:16] or "-"}<br>发货: {(o.get("consign_time") or "")[:16] or "-"}</div></div>')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">物流信息</div><div style="color:#c9d1d9;font-size:12px;line-height:1.8;">类型: {dc}<br>位置: {o.get("current_addr","-")}<br>物流: {o.get("logistics_name","-")}<br>收货地: {o.get("receiver_province","")} {o.get("receiver_city","")}<br>金额: ￥{float(am or 0):,.0f}</div></div>')
        p.append('</div></div></td></tr>')
    if not lg_sorted: p.append('<tr><td colspan="9" style="padding:40px;text-align:center;color:#484f58;">暂无物流异常</td></tr>')
    p.append('</table></div></div>')

    # 售后异常表（默认隐藏）
    p.append(f'<div id="tbl_aftersales" style="display:none;"><div style="overflow-x:auto;border:1px solid #30363d;border-radius:0 0 10px 10px;"><table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr style="background:#161b22;color:#8b949e;text-align:left;">')
    for th in ["#", "状态", "原因", "品名规格", "订单号", "售后单号", "退款金额", "申请时间", "详情"]:
        p.append(f'<th style="padding:10px 8px;border-bottom:1px solid #30363d;">{th}</th>')
    p.append('</tr></thead>')
    for idx, o in enumerate(as_orders, 1):
        ac, ae2, af = o["ai_class"], o["ai_exp"], o["ai_conf"]
        tl = tc.get(ac, "#8b949e")
        pr, am = o["product"][:40], o["refund_amount"] or o["amount"]
        wh = o["warehouse"]
        dc = o.get("service_status", "")
        p.append(f'<tr class="er" data-m="aftersales" data-s="{dc}" style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#484f58;">{idx}</td>')
        p.append(f'<td style="padding:8px;"><span class="etg" style="background:{tl};">{ac}</span><br><span style="color:#484f58;font-size:10px;">{dc}</span></td>')
        p.append(f'<td style="padding:8px;color:#c9d1d9;font-size:11px;">{(o.get("primary_reason") or "-")[:20]}</td>')
        p.append(f'<td style="padding:8px;color:#c9d1d9;">{pr}</td>')
        p.append(f'<td style="padding:8px;"><code style="background:#161b22;padding:1px 4px;border-radius:3px;font-size:11px;color:#58a6ff;">{o["tid"][:16]}</code></td>')
        p.append(f'<td style="padding:8px;"><code style="background:#161b22;padding:1px 4px;border-radius:3px;font-size:11px;color:#8b949e;">{(o.get("service_no") or "-")[:20]}</code></td>')
        p.append(f'<td style="padding:8px;color:#00e676;font-size:12px;font-weight:600;">¥{float(am or 0):,.0f}</td>')
        p.append(f'<td style="padding:8px;color:#8b949e;font-size:11px;">{(o.get("apply_time") or "")[:16]}</td>')
        p.append('<td style="padding:8px;text-align:center;"><button onclick="this.closest(\'tr\').nextElementSibling.classList.toggle(\'on\');this.textContent=this.textContent==\'▼\'?\'▶\':\'▼\'" style="background:none;border:none;color:#00d4ff;cursor:pointer;">▶</button></td></tr>')
        p.append(f'<tr class="eg"><td colspan="9" style="padding:0;"><div style="padding:12px 16px;background:#161b22;"><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;">')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">AI 分析</div><div style="color:#c9d1d9;font-size:13px;line-height:1.6;">{ae2}</div><div style="color:#484f58;font-size:11px;">置信度: {af:.0%}</div></div>')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">订单详情</div><div style="color:#c9d1d9;font-size:12px;line-height:1.8;">仓库: {wh}<br>状态: {o.get("trade_status","-")}<br>退款: {o.get("refund_status","-")}<br>下单: {(o.get("trade_time") or "")[:16] or "-"}<br>发货: {(o.get("consign_time") or "")[:16] or "-"}</div></div>')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">售后信息</div><div style="color:#c9d1d9;font-size:12px;line-height:1.8;">类型: 售后异常<br>状态: {o.get("service_status","-")}<br>原因: {o.get("primary_reason","-")}<br>')
        if o.get("description"): p.append(f'描述: {o["description"][:50]}<br>')
        if o.get("audit_opinion"): p.append(f'审核: {o["audit_opinion"]}<br>')
        p.append(f'物流: {o.get("ao_logistics_no","-")}</div></div>')
        p.append('</div></div></td></tr>')
    if not as_orders: p.append('<tr><td colspan="9" style="padding:40px;text-align:center;color:#484f58;">暂无售后异常</td></tr>')
    p.append('</table></div></div>')

    # JS
    p.append("""<script>
function et(t){
    document.querySelectorAll('.etb').forEach(b=>b.classList.remove('on'));
    document.getElementById('et_'+t).classList.add('on');
    document.getElementById('sub_logistics').style.display=t==='logistics'?'flex':'none';
    document.getElementById('sub_aftersales').style.display=t==='aftersales'?'flex':'none';
    document.getElementById('tbl_logistics').style.display=t==='logistics'?'':'none';
    document.getElementById('tbl_aftersales').style.display=t==='aftersales'?'':'none';
}
function es(m,s){
    document.querySelectorAll('#sub_'+m+' .esb').forEach(b=>b.classList.remove('on'));
    var btn=document.getElementById('es_'+m+'_'+s);
    if(btn)btn.classList.add('on');
    document.querySelectorAll('#tbl_'+m+' .er').forEach(r=>{
        if(s==='all'){r.style.display=''}
        else{r.style.display=r.getAttribute('data-s')===s?'':'none'}
    });
}
</script>""")
    html = "\n".join(p)
    html = page("异常件追踪", "exceptionorders", html)
    _exception_cache = (now, html)
    return html
