# ===== 异常件页面 =====
_exception_cache = None

def render_exceptionorders():
    """异常件：物流问题件 + 售后异常，AI逐条分析"""
    global _exception_cache
    import sqlite3, json
    from datetime import datetime

    now = time.time()
    if _exception_cache and now - _exception_cache[0] < 300:
        return _exception_cache[1]

    DB_PATH = os.path.join(BASE, "erp_all.db")
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    shop = "榴愿时刻工厂店"

    lg_total = conn.execute("SELECT COUNT(*) FROM (SELECT DISTINCT logistics_no FROM logistics_trace WHERE shop_name=? AND operate_desc='问题件')", (shop,)).fetchone()[0]
    as_pending = conn.execute("SELECT COUNT(*) FROM after_sales WHERE shop_name=? AND service_status NOT IN ('完成','取消')", (shop,)).fetchone()[0]
    normal_cnt = conn.execute("SELECT COUNT(*) FROM exception_analysis WHERE classification='正常换单'").fetchone()[0]
    communicate_cnt = conn.execute("SELECT COUNT(*) FROM exception_analysis WHERE classification='需沟通'").fetchone()[0]
    unknown_cnt = conn.execute("SELECT COUNT(*) FROM exception_analysis WHERE classification='无法查询'").fetchone()[0]

    analysis_map = {}
    for row in conn.execute("SELECT exception_id, classification, ai_explanation, confidence FROM exception_analysis").fetchall():
        analysis_map[row[0]] = (row[1], row[2], row[3])

    # 物流问题件 LIMIT 200
    lg_orders = []
    for row in conn.execute("SELECT logistics_no, current_addr, operate_time FROM logistics_trace WHERE shop_name=? AND operate_desc='问题件' ORDER BY operate_time DESC LIMIT 200", (shop,)).fetchall():
        ao = conn.execute("SELECT srcTids, warehouseName, receiverCityName, receiverProvinceName, goodsAmount, realAmount, orderItemList, logisticsName, tradeTime, consignTime, tradeStatusFrontText, refundStatusText FROM erp_all_orders WHERE shopName=? AND logisticsNo=? LIMIT 1", (shop, row[0])).fetchone()
        if not ao: continue
        tid = ao[0] or ""
        eid = "lg_" + row[0]
        product = "-"
        try:
            for it in (json.loads(ao[6]) if ao[6] else [])[:1]:
                sku = it.get("skuName", "") or it.get("spuName", "")
                if sku and ";" in sku: sku = sku.split(";", 1)[1].strip()
                if sku: product = f"{sku} x{it.get('skuNum', it.get('quantity', '1'))}"; break
        except Exception: pass
        hours = 0
        if row[2]:
            try: hours = (datetime.now() - datetime.fromisoformat(row[2].replace(" ", "T"))).total_seconds() / 3600
            except: pass
        ai = analysis_map.get(eid)
        if ai: c, e, cf = ai
        elif (ao[11] or "") in ("全部退款", "退款成功"): c, e, cf = "正常换单", f"物流问题件（{row[1]}），订单已全部退款，货物自动拦截。", 0.80
        elif hours > 48: c, e, cf = "需沟通", f"物流问题件在{row[1]}停留超{int(hours)}小时，建议联系物流。", 0.70
        else: c, e, cf = "正常换单", f"物流问题件，位于{row[1]}，物流可能正在处理。", 0.60
        lg_orders.append({"eid": eid, "tid": tid, "logistics_no": row[0], "logistics_name": ao[7] or "", "current_addr": row[1] or "", "operate_time": row[2] or "", "warehouse": ao[1] or "", "receiver_city": ao[2] or "", "receiver_province": ao[3] or "", "product": product, "amount": ao[4] or 0, "trade_status": ao[10] or "", "refund_status": ao[11] or "", "consign_time": ao[9] or "", "trade_time": ao[8] or "", "hours_since": hours, "ai_class": c, "ai_exp": e, "ai_conf": cf, "source": "物流问题件"})

    # 售后异常 LIMIT 200
    as_orders = []
    for row in conn.execute("SELECT tid, service_no, service_status, primary_reason, secondary_reason, outbound_status, logistics_no, refund_amount, description, audit_opinion, apply_time FROM after_sales WHERE shop_name=? AND service_status NOT IN ('完成','取消') ORDER BY apply_time DESC LIMIT 200", (shop,)).fetchall():
        tid = row[0] or ""
        eid = "as_" + (row[1] or tid)
        ao = conn.execute("SELECT warehouseName, receiverCityName, tradeTime, goodsAmount, orderItemList, logisticsNo, logisticsName, consignTime, tradeStatusFrontText, refundStatusText FROM erp_all_orders WHERE shopName=? AND srcTids=? LIMIT 1", (shop, tid)).fetchone()
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
        elif row[2] in ("待客户反馈", "待商家审核"): c, e, cf = "需沟通", f"售后处理中（{row[2]}，{row[3]}），请关注时效。", 0.80
        elif row[2] == "待买家退货": c, e, cf = "需沟通", "已同意退货，等待买家寄回。", 0.75
        elif row[2] == "买家已退货": c, e, cf = "正常换单", "客户已退货，等待仓库验货。", 0.70
        else: c, e, cf = "需沟通", f"售后状态：{row[2]}（{row[3]}）。", 0.60
        as_orders.append({"eid": eid, "tid": tid, "service_no": row[1], "service_status": row[2], "primary_reason": row[3], "secondary_reason": row[4], "outbound_status": row[5], "logistics_no": row[6] or "", "refund_amount": row[7] or 0, "description": row[8] or "", "audit_opinion": row[9] or "", "apply_time": row[10] or "", "warehouse": ao[0] if ao else "", "receiver_city": ao[1] if ao else "", "product": product, "amount": ao[3] if ao else 0, "trade_time": ao[2] if ao else "", "consign_time": ao[7] if ao else "", "ao_logistics_no": ao[5] if ao else "", "logistics_name": ao[6] if ao else "", "trade_status": ao[8] if ao else "", "refund_status": ao[9] if ao else "", "ai_class": c, "ai_exp": e, "ai_conf": cf, "source": "售后异常"})

    conn.close()
    total = lg_total + as_pending
    tc = {"正常换单": "#238636", "需沟通": "#d29922", "无法查询": "#f85149"}
    p = []
    p.append('<h1 style="color:#fff;font-size:22px;margin-bottom:6px;">异常件追踪</h1>')
    p.append(f'<p style="color:#8b949e;margin-bottom:16px;font-size:13px;">物流问题件 + 售后异常 | AI逐条分析 | {datetime.now().strftime("%m/%d %H:%M")}</p>')
    p.append('<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:24px;">')
    for l, v, cl in [("总异常", f"{total:,}", "#f85149"), ("物流问题件", f"{lg_total:,}", "#f97316"), ("售后异常", f"{as_pending:,}", "#a855f7"), ("需沟通", f"{communicate_cnt:,}", "#d29922"), ("正常换单", f"{normal_cnt:,}", "#238636")]:
        p.append(f'<div style="background:#161b22;border:1px solid {cl};border-radius:10px;padding:16px;text-align:center;"><div style="color:#8b949e;font-size:12px;">{l}</div><div style="color:{cl};font-size:32px;font-weight:700;">{v}</div></div>')
    p.append('</div>')
    tids = ["tab_all", "tab_communicate", "tab_normal", "tab_unknown"]
    tlab = [f"全部 ({total})", f"需沟通 ({communicate_cnt})", f"正常换单 ({normal_cnt})", f"无法查询 ({unknown_cnt})"]
    p.append('<style>.etb{padding:8px 16px;border:1px solid #30363d;background:#161b22;color:#8b949e;cursor:pointer;font-size:13px;border-radius:6px 6px 0 0}.etb.on{background:#1c2333;color:#fff;border-color:#00d4ff;border-bottom-color:#1c2333}.etb:hover{background:#21262d;color:#fff}.eg{max-height:0;overflow:hidden;transition:max-height .3s ease}.eg.on{max-height:600px}.etg{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;color:#fff}</style>')
    p.append('<div style="display:flex;gap:4px;">')
    for i, (t, l) in enumerate(zip(tids, tlab)): p.append(f'<button class="etb {"on" if i==0 else ""}" onclick="ef(\x27{t}\x27)" id="{t}">{l}</button>')
    p.append('</div><div style="overflow-x:auto;border:1px solid #30363d;border-top:none;border-radius:0 0 10px 10px;"><table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr style="background:#161b22;color:#8b949e;text-align:left;">')
    for th in ["#", "AI分类", "来源", "品名规格", "订单号", "物流单号", "位置/原因", "金额", "异常时间", "详情"]:
        p.append(f'<th style="padding:10px 8px;border-bottom:1px solid #30363d;">{th}</th>')
    p.append('</tr></thead>')
    ae = lg_orders + as_orders
    co = {"需沟通": 0, "正常换单": 1, "无法查询": 2}
    ae.sort(key=lambda x: (co.get(x.get("ai_class", ""), 3), -x.get("hours_since", 0) if x.get("hours_since") else 0))
    for idx, o in enumerate(ae, 1):
        ac, ae2, af = o["ai_class"], o["ai_exp"], o["ai_conf"]
        tl = tc.get(ac, "#8b949e")
        sr, td2 = o["source"], o["tid"]
        lg = o.get("logistics_no", "") or o.get("ao_logistics_no", "")
        pr, am = o["product"][:40], o["amount"]
        wh = o["warehouse"]
        if sr == "物流问题件":
            lr = o.get("current_addr", "")[:30]
            et = o.get("operate_time", "")[:16]
            h = o.get("hours_since", 0)
            td3 = f'{et} ({int(h)}h前)' if h > 0 else et
        else:
            lr = o.get("primary_reason", "")[:30]
            td3 = o.get("apply_time", "")[:16]
        dc = "communicate" if ac == "需沟通" else ("normal" if ac == "正常换单" else "unknown")
        p.append(f'<tr class="er" data-c="{dc}" style="border-bottom:1px solid #21262d;"><td style="padding:8px;color:#484f58;">{idx}</td>')
        p.append(f'<td style="padding:8px;"><span class="etg" style="background:{tl};">{ac}</span></td>')
        p.append(f'<td style="padding:8px;color:#8b949e;font-size:11px;">{sr[:4]}</td>')
        p.append(f'<td style="padding:8px;color:#c9d1d9;">{pr}</td>')
        p.append(f'<td style="padding:8px;"><code style="background:#161b22;padding:1px 4px;border-radius:3px;font-size:11px;color:#58a6ff;">{td2[:16] if td2 else "-"}</code></td>')
        p.append(f'<td style="padding:8px;"><code style="background:#161b22;padding:1px 4px;border-radius:3px;font-size:11px;color:#8b949e;">{lg[:20] if lg else "-"}</code></td>')
        p.append(f'<td style="padding:8px;color:#8b949e;font-size:11px;">{lr}</td>')
        p.append(f'<td style="padding:8px;color:#00e676;font-size:12px;font-weight:600;">¥{float(am or 0):,.0f}</td>')
        p.append(f'<td style="padding:8px;color:#8b949e;font-size:11px;">{td3}</td>')
        p.append('<td style="padding:8px;text-align:center;"><button onclick="this.closest(\'tr\').nextElementSibling.classList.toggle(\'on\');this.textContent=this.textContent==\'▼\'?\'▶\':\'▼\'" style="background:none;border:none;color:#00d4ff;cursor:pointer;">▶</button></td></tr>')
        # detail
        p.append('<tr class="eg" style="border-bottom:1px solid #21262d;"><td colspan="10" style="padding:0;"><div style="padding:12px 16px;background:#161b22;"><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;">')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">AI 分析</div><div style="color:#c9d1d9;font-size:13px;line-height:1.6;">{ae2}</div><div style="color:#484f58;font-size:11px;">置信度: {af:.0%}</div></div>')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">订单详情</div><div style="color:#c9d1d9;font-size:12px;line-height:1.8;">仓库: {wh}<br>订单: {o.get("trade_status","-")}<br>退款: {o.get("refund_status","-")}<br>下单: {o.get("trade_time","")[:16] if o.get("trade_time") else "-"}<br>发货: {o.get("consign_time","")[:16] if o.get("consign_time") else "-"}</div></div>')
        p.append(f'<div><div style="color:#8b949e;font-size:11px;">异常信息</div><div style="color:#c9d1d9;font-size:12px;line-height:1.8;">类型: {sr}<br>')
        if sr == "物流问题件":
            p.append(f'位置: {o.get("current_addr","-")}<br>物流: {o.get("logistics_name","-")}<br>收货地: {o.get("receiver_province","")} {o.get("receiver_city","")}<br>')
        else:
            p.append(f'售后单号: {o.get("service_no","-")}<br>状态: {o.get("service_status","-")}<br>原因: {o.get("primary_reason","-")}<br>')
            if o.get("description"): p.append(f'描述: {o["description"][:50]}<br>')
            if o.get("audit_opinion"): p.append(f'审核: {o["audit_opinion"]}<br>')
        p.append('</div></div></div></div></td></tr>')
    if not ae: p.append('<tr><td colspan="10" style="padding:40px;text-align:center;color:#484f58;">暂无异常件</td></tr>')
    p.append('</table></div>')
    p.append('<script>function ef(t){document.querySelectorAll(".etb").forEach(b=>b.classList.remove("on"));document.getElementById(t).classList.add("on");var f=t==="tab_all"?"all":t==="tab_communicate"?"communicate":t==="tab_normal"?"normal":"unknown";document.querySelectorAll(".er").forEach(r=>{if(f==="all"){r.style.display=""}else{r.style.display=r.getAttribute("data-c")===f?"":"none"}})}</script>')
    html = "\n".join(p)
    html = page("异常件追踪", "exceptionorders", html)
    _exception_cache = (now, html)
    return html
