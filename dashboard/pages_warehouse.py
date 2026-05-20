#!/usr/bin/env python3
"""仓库需求页面 — 调用预生成的邮件 HTML"""
from dashboard import page, cached_page, get_cached_html

def render_warehouse():
    html = get_cached_html('erp_warehouse_demand_email.py')
    return page('仓库需求', 'warehouse', html)
