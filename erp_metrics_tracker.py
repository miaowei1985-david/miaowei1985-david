#!/usr/bin/env python3
"""
历史指标追踪器：保存上一轮数据，生成趋势箭头
"""
import json
import os

TRACKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "erp_metrics_tracker.json")

def load_all():
    try:
        with open(TRACKER_PATH, 'r') as f:
            return json.load(f)
    except:
        return {}

def load_prev(namespace):
    data = load_all()
    return data.get(namespace)

def save(namespace, metrics):
    data = load_all()
    data[namespace] = metrics
    with open(TRACKER_PATH, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def badge(current, prev_key, prev_data):
    """生成趋势箭头HTML"""
    if prev_data is None:
        return ""
    prev_val = prev_data.get(prev_key)
    if prev_val is None or not isinstance(prev_val, (int, float)):
        return ""
    delta = current - prev_val
    if abs(delta) < 1e-9:
        return ""

    up = delta > 0
    sign = "+" if up else ""
    if isinstance(delta, float) and delta == int(delta):
        delta_str = f"{int(delta):,}"
    elif isinstance(delta, float):
        delta_str = f"{delta:,.1f}"
    else:
        delta_str = f"{delta:,}"

    if up:
        return f' <span class="trend-arrow trend-up">▲ {sign}{delta_str}</span>'
    else:
        return f' <span class="trend-arrow trend-down">▼ {sign}{delta_str}</span>'
