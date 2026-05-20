#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
异常件 AI 分析 — 定时运行，填充 exception_analysis 表
用法: python3 erp_exception_ai.py
Cron: 每2小时在 erp_cron.sh 中 erp_fetch.py 之后运行
"""

import os
import sys
import json
import time
import sqlite3
from datetime import datetime

from erp_config import DB_PATH
SHOP = "榴愿时刻工厂店"
MAX_ANALYZE = 200  # 每次最多分析200条，避免API超额

# ===== 初始化数据库 =====
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exception_analysis (
            exception_id TEXT PRIMARY KEY,
            exception_type TEXT,
            tid TEXT,
            logistics_no TEXT,
            classification TEXT,
            ai_explanation TEXT,
            confidence REAL,
            analysis_time TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exc_class ON exception_analysis(classification)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_exc_type ON exception_analysis(exception_type)")
    conn.commit()
    conn.close()

# ===== 数据采集 =====
def fetch_exception_orders():
    """从三个数据源采集异常件"""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    orders = []
    seen = set()

    # 1. 物流问题件
    for row in conn.execute("""
        SELECT DISTINCT lt.logistics_no, lt.current_addr, lt.operate_time,
               ao.srcTids, ao.tradeTime, ao.consignTime, ao.warehouseName,
               ao.receiverCityName, ao.receiverProvinceName, ao.goodsAmount,
               ao.realAmount, ao.orderItemList, ao.logisticsName,
               ao.tradeStatusFrontText, ao.refundStatusText
        FROM logistics_trace lt
        INNER JOIN erp_all_orders ao ON ao.logisticsNo = lt.logistics_no
        WHERE lt.shop_name = ? AND lt.operate_desc = '问题件'
          AND ao.shopName = ?
        ORDER BY lt.operate_time DESC
    """, (SHOP, SHOP)).fetchall():
        tid = row[3] or ""
        if tid not in seen:
            seen.add(tid)
            orders.append({
                "exception_id": "lg_" + (row[0] or ""),
                "exception_type": "物流问题件",
                "tid": tid,
                "logistics_no": row[0],
                "logistics_name": row[12] or "",
                "current_addr": row[1] or "",
                "operate_time": row[2] or "",
                "trade_time": row[4] or "",
                "consign_time": row[5] or "",
                "warehouse": row[6] or "",
                "receiver_city": row[7] or "",
                "receiver_province": row[8] or "",
                "goods_amount": row[9] or 0,
                "real_amount": row[10] or 0,
                "order_item_list": row[11] or "",
                "trade_status": row[13] or "",
                "refund_status": row[14] or "",
            })

    # 2. 售后待处理 + 审核不通过
    for row in conn.execute("""
        SELECT asr.tid, asr.service_no, asr.service_status, asr.primary_reason,
               asr.secondary_reason, asr.outbound_status, asr.logistics_no,
               asr.refund_amount, asr.description, asr.audit_opinion,
               asr.apply_time, asr.return_method,
               ao.warehouseName, ao.receiverCityName, ao.tradeTime,
               ao.goodsAmount, ao.realAmount, ao.orderItemList, ao.logisticsNo,
               ao.logisticsName, ao.consignTime
        FROM after_sales asr
        LEFT JOIN erp_all_orders ao ON ao.srcTids = asr.tid AND ao.shopName = ?
        WHERE asr.shop_name = ?
          AND asr.service_status NOT IN ('完成', '取消')
        ORDER BY asr.apply_time DESC
    """, (SHOP, SHOP)).fetchall():
        tid = row[0] or ""
        eid = "as_" + (row[1] or tid)
        if tid not in seen or row[2] == "审核不通过":
            if tid not in seen:
                seen.add(tid)
            orders.append({
                "exception_id": eid,
                "exception_type": "售后异常",
                "tid": tid,
                "service_no": row[1] or "",
                "service_status": row[2] or "",
                "primary_reason": row[3] or "",
                "secondary_reason": row[4] or "",
                "outbound_status": row[5] or "",
                "logistics_no": row[6] or "",
                "refund_amount": row[7] or 0,
                "description": row[8] or "",
                "audit_opinion": row[9] or "",
                "apply_time": row[10] or "",
                "return_method": row[11] or "",
                "warehouse": row[12] or "",
                "receiver_city": row[13] or "",
                "trade_time": row[14] or "",
                "goods_amount": row[15] or 0,
                "real_amount": row[16] or 0,
                "order_item_list": row[17] or "",
                "ao_logistics_no": row[18] or "",
                "logistics_name": row[19] or "",
                "consign_time": row[20] or "",
            })

    conn.close()
    return orders


# ===== 规则引擎分析 =====
def rule_based_analysis(order):
    """基于业务规则的分类 + 解释"""
    etype = order.get("exception_type", "")
    refund = order.get("refund_status", "")
    refund_amt = order.get("refund_amount", 0) or 0
    service_status = order.get("service_status", "")
    primary_reason = order.get("primary_reason", "")
    outbound = order.get("outbound_status", "")
    trade_status = order.get("trade_status", "")
    operate_time = order.get("operate_time", "")

    if etype == "物流问题件":
        # 检查是否已有售后
        has_as = "service_status" in order and service_status

        if refund == "全部退款" or refund == "退款成功":
            classification = "正常换单"
            confidence = 0.85
            explanation = f"物流标记为问题件（{order.get('current_addr', '未知地点')}），但订单已全部退款。货物自动拦截退回，无需额外操作。"
        elif has_as and service_status == "审核不通过":
            classification = "需沟通"
            confidence = 0.80
            explanation = f"物流异常 + 售后审核不通过（原因：{primary_reason}）。客户可能未接受理赔方案，需主动联系确认后续处理。"
        elif has_as and service_status in ("待客户反馈", "待商家审核"):
            classification = "需沟通"
            confidence = 0.90
            explanation = f"物流异常且售后正在进行中（{service_status}，原因：{primary_reason}）。等待客户反馈/商家审核，需关注处理时效。"
        elif operate_time:
            try:
                from datetime import datetime
                op_dt = datetime.fromisoformat(operate_time.replace(" ", "T"))
                hours_since = (datetime.now() - op_dt).total_seconds() / 3600
                if hours_since > 48:
                    classification = "需沟通"
                    confidence = 0.75
                    explanation = f"物流问题件已在{order.get('current_addr', '')}停留超过{int(hours_since)}小时。长时间未更新可能导致客户投诉，建议联系物流确认。"
                elif hours_since > 24:
                    classification = "需沟通"
                    confidence = 0.65
                    explanation = f"物流问题件在{order.get('current_addr', '')}停留超{int(hours_since)}小时，需关注物流进展。"
                else:
                    classification = "正常换单"
                    confidence = 0.70
                    explanation = f"物流问题件，当前位于{order.get('current_addr', '')}。问题发现时间较短，物流可能正在处理中，持续监控即可。"
            except Exception:
                classification = "正常换单"
                confidence = 0.60
                explanation = f"物流问题件，位置：{order.get('current_addr', '未知')}。暂无关联售后，可能已自动解决或正在处理中。"
        else:
            classification = "正常换单"
            confidence = 0.60
            explanation = f"物流问题件，暂无关联售后信息。"

    elif etype == "售后异常":
        if service_status == "审核不通过":
            classification = "需沟通"
            confidence = 0.85
            explanation = f"售后审核不通过（{primary_reason}），客户申请未被批准。需主动联系客户说明原因，协商替代方案或二次申诉。"
        elif service_status == "待客户反馈":
            classification = "需沟通"
            confidence = 0.80
            explanation = f"客户发起售后（{primary_reason}），已出库商品等待客户反馈。根据售后政策，24小时内需提供拆箱视频/照片证明。请关注反馈时效。"
        elif service_status == "待商家审核":
            classification = "需沟通"
            confidence = 0.90
            explanation = f"客户发起售后（{primary_reason}），等待商家审核。需及时处理避免超时自动通过。"
        elif service_status == "待买家退货":
            classification = "需沟通"
            confidence = 0.75
            explanation = f"售后已同意退货，等待买家寄回商品。请提醒客户按退货地址寄回，并关注物流单号。"
        elif service_status == "买家已退货":
            classification = "正常换单"
            confidence = 0.70
            explanation = f"客户已退货，等待仓库验货后处理退款/换货。请确认收货后及时完成售后流程。"
        else:
            classification = "需沟通"
            confidence = 0.60
            explanation = f"售后状态：{service_status}（{primary_reason}），需跟进处理。"
    else:
        classification = "无法查询"
        confidence = 0.50
        explanation = "未知异常类型，无法自动分类。"

    return classification, explanation, confidence


# ===== AI 分析 =====
def ai_analyze(order, rule_result):
    """调用 Claude API 生成 AI 分析"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return rule_result

    rule_class, rule_exp, rule_conf = rule_result

    # 解析商品信息
    item_str = ""
    try:
        items = json.loads(order.get("order_item_list", "[]")) if order.get("order_item_list") else []
        for item in items[:3]:
            sku = item.get("skuName", "") or item.get("spuName", "")
            qty = item.get("skuNum", item.get("quantity", "1"))
            if sku:
                item_str += f"{sku} x{qty}; "
    except Exception:
        item_str = order.get("order_item_list", "")[:100]

    # 构建 AI 上下文
    context_parts = []
    context_parts.append(f"异常类型: {order.get('exception_type', '')}")
    if order.get("exception_type") == "物流问题件":
        context_parts.append(f"物流单号: {order.get('logistics_no', '')}")
        context_parts.append(f"物流公司: {order.get('logistics_name', '')}")
        context_parts.append(f"当前位置: {order.get('current_addr', '')}")
        context_parts.append(f"异常时间: {order.get('operate_time', '')}")
        context_parts.append(f"发货时间: {order.get('consign_time', '')}")
    elif order.get("exception_type") == "售后异常":
        context_parts.append(f"售后单号: {order.get('service_no', '')}")
        context_parts.append(f"售后状态: {order.get('service_status', '')}")
        context_parts.append(f"主要原因: {order.get('primary_reason', '')}")
        context_parts.append(f"次要原因: {order.get('secondary_reason', '')}")
        context_parts.append(f"出库状态: {order.get('outbound_status', '')}")
        context_parts.append(f"退款金额: {order.get('refund_amount', 0)}")
        context_parts.append(f"申请时间: {order.get('apply_time', '')}")
        if order.get("audit_opinion"):
            context_parts.append(f"审核意见: {order.get('audit_opinion', '')}")
        if order.get("description"):
            context_parts.append(f"客户描述: {order.get('description', '')}")

    context_parts.append(f"商品: {item_str}")
    context_parts.append(f"仓库: {order.get('warehouse', '')}")
    context_parts.append(f"收货地: {order.get('receiver_province', '')} {order.get('receiver_city', '')}")
    context_parts.append(f"订单状态: {order.get('trade_status', '')}")
    context_parts.append(f"退款状态: {order.get('refund_status', '')}")
    context_parts.append(f"订单金额: ¥{order.get('goods_amount', 0)}")

    context = "\n".join(context_parts)

    prompt = f"""请分析以下榴愿时刻工厂店的异常订单，并给出分类和解释。

业务背景：
- 我们是京东「榴愿时刻工厂店」，主营泰国进口金枕榴莲（生鲜水果）
- 物流主要由顺丰京东承运
- 售后政策：24小时内需提供拆箱视频/照片；5-10%水分流失属正常；死包/空包/发霉/坏果/缺重可理赔；个人口味/未熟开封/偏远地区不理赔
- 异常分类三种：
  1. 正常换单 — 问题已自动解决或无需人工干预（已退款、物流自行恢复等）
  2. 需沟通 — 需要人工介入处理（售后待处理、物流长时间滞留、客户未反馈等）
  3. 无法查询 — 数据不全或无法匹配，无法判断

订单数据：
{context}

规则引擎预判：{rule_class}（置信度: {rule_conf:.0%}）

请结合业务经验和订单数据，给出你的判断。输出格式（严格三行）：
第1行：分类（仅选一个：正常换单 / 需沟通 / 无法查询）
第2行：置信度（0.0到1.0的数字）
第3行：解释（50字以内，中文，说明原因和建议操作）"""

    try:
        import http.client
        import urllib.parse

        body_data = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 300,
            "temperature": 0.3,
            "system": "你是榴愿时刻工厂店的售后物流分析专家。熟悉榴莲生鲜电商的售后政策和物流异常处理。",
            "messages": [{"role": "user", "content": prompt}]
        }).encode("utf-8")

        conn = http.client.HTTPSConnection("api.anthropic.com", timeout=30)
        conn.request("POST", "/v1/messages", body=body_data, headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "message-batches-2024-09-24",
        })
        resp = conn.getresponse()
        resp_data = json.loads(resp.read().decode("utf-8"))
        conn.close()

        if resp.status == 200:
            content = resp_data.get("content", [{}])[0].get("text", "")
            lines = content.strip().split("\n")
            if len(lines) >= 3:
                classification = lines[0].strip()
                if classification in ("正常换单", "需沟通", "无法查询"):
                    try:
                        confidence = float(lines[1].strip())
                    except ValueError:
                        confidence = rule_conf
                    explanation = lines[2].strip()
                    return classification, explanation, confidence

        # API 失败，回退到规则引擎
        return rule_result

    except Exception as e:
        print(f"  AI API 调用失败: {e}")
        return rule_result


# ===== 主流程 =====
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始异常件 AI 分析...")

    init_db()
    orders = fetch_exception_orders()
    print(f"  采集到 {len(orders)} 条异常件")

    if not orders:
        print("  无异常件，跳过分析")
        return

    # 读取已有分析，跳过已分析的
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    existing = set()
    for row in conn.execute("SELECT exception_id FROM exception_analysis").fetchall():
        existing.add(row[0])
    conn.close()

    new_orders = [o for o in orders if o["exception_id"] not in existing]
    # 同时分析超过1小时的旧数据（重新分析）
    stale_cutoff = datetime.now().replace(hour=0, minute=0, second=0)
    stale_ids = set()
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        for row in conn.execute("SELECT exception_id FROM exception_analysis WHERE analysis_time < ?", (stale_cutoff.isoformat(),)).fetchall():
            stale_ids.add(row[0])
        conn.close()
    except Exception:
        pass

    to_analyze = new_orders + [o for o in orders if o["exception_id"] in stale_ids]
    to_analyze = to_analyze[:MAX_ANALYZE]

    if not to_analyze:
        print(f"  所有 {len(orders)} 条已分析，跳过")
        return

    print(f"  需要分析 {len(to_analyze)} 条")

    analyzed = 0
    api_calls = 0
    for i, order in enumerate(to_analyze):
        rule_result = rule_based_analysis(order)

        # 每5条调用一次AI（节流），或前3条必调
        use_ai = (i < 3) or (i % 5 == 0)
        if use_ai and os.environ.get("ANTHROPIC_API_KEY"):
            api_calls += 1
            result = ai_analyze(order, rule_result)
            time.sleep(2)  # 节流，避免API限流
        else:
            result = rule_result

        classification, explanation, confidence = result
        eid = order["exception_id"]

        try:
            conn = sqlite3.connect(DB_PATH, timeout=30.0)
            conn.execute("""
                INSERT OR REPLACE INTO exception_analysis
                (exception_id, exception_type, tid, logistics_no, classification, ai_explanation, confidence, analysis_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (eid, order.get("exception_type", ""), order.get("tid", ""),
                  order.get("logistics_no", ""), classification, explanation, confidence,
                  datetime.now().isoformat()))
            conn.commit()
            conn.close()
            analyzed += 1
        except Exception as e:
            print(f"  写入失败 {eid}: {e}")

    print(f"  完成：分析 {analyzed} 条，API调用 {api_calls} 次")


if __name__ == "__main__":
    main()
