"""Dashboard 共享基础设施：缓存、导航、页面模板"""
import os
import sys
import json
import html as html_mod
import subprocess
import time
from datetime import datetime

import sqlite3

# ===== 路径与缓存 =====
BASE = os.path.expanduser("~/pdd/Claudecode")
CACHE_TTL = 1800  # 30 分钟
_cache = {}  # {page_key: (timestamp, html_content)}


def get_cached_html(script_name):
    """有缓存且未过期则返回缓存，否则运行脚本并缓存"""
    now = time.time()
    if script_name in _cache:
        ts, content = _cache[script_name]
        if now - ts < CACHE_TTL:
            return content

    result = subprocess.run(
        [sys.executable, os.path.join(BASE, script_name), '--html'],
        capture_output=True, text=True, timeout=120, cwd=BASE,
    )
    content = result.stdout
    for marker in ['<!DOCTYPE', '<style>', '<html>']:
        idx = content.find(marker)
        if idx >= 0:
            content = content[idx:]
            break
    _cache[script_name] = (now, content)
    return content


def clear_cache():
    _cache.clear()


def cached_page(key):
    """通用页面缓存装饰器，30min TTL + stale fallback"""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            try:
                now = time.time()
                cache_key = f"page:{key}"
                if cache_key in _cache and now - _cache[cache_key][0] < CACHE_TTL:
                    return _cache[cache_key][1]
                html = fn(*args, **kwargs)
                if html:
                    _cache[cache_key] = (now, html)
                return html
            except Exception as e:
                import traceback
                print(f"[cached_page:{key}] Error: {e}")
                traceback.print_exc()
                cache_key = f"page:{key}"
                if cache_key in _cache:
                    return _cache[cache_key][1]
                return f"<h1>Page error: {key}</h1><pre>{e}</pre>"
        return wrapper
    return decorator


# ===== 数据鲜度检测 =====
DB_PATH = os.path.join(BASE, "erp_all.db")

def get_sync_status():
    """读取 sync_metadata，返回 HTML 状态卡片"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        rows = conn.execute("SELECT sync_time, table_name, row_count FROM sync_metadata ORDER BY sync_time DESC LIMIT 10").fetchall()
        conn.close()
        if not rows:
            return _render_sync_card("red", "从未同步", "无数据", "")
        sync_time = rows[0][0]
        tables = [(r[1], r[2]) for r in rows if r[1] != "__total__"]
        total = next((r[2] for r in rows if r[1] == "__total__"), None)
        try:
            st = datetime.fromisoformat(sync_time)
            hours = (datetime.now() - st).total_seconds() / 3600
        except Exception:
            hours = 999
        if hours < 3:
            color = "green"
            label = "数据正常"
        elif hours < 6:
            color = "yellow"
            label = "数据可能过期"
        else:
            color = "red"
            label = "数据陈旧"
        time_str = st.strftime("%m-%d %H:%M") if hours < 999 else sync_time
        table_spans = []
        for t, n in tables:
            table_spans.append("<span style=\"color:#8b949e;font-size:12px;\">" + t + ": " + "{:,}".format(n) + "</span>")
        try:
            ao_conn = sqlite3.connect(DB_PATH, timeout=30.0)
            ao_count = ao_conn.execute('SELECT COUNT(*) FROM erp_all_orders').fetchone()[0]
            ao_conn.close()
            table_spans.append('<span style="color:#8b949e;font-size:12px;">erp_all_orders: ' + '{:,}'.format(ao_count) + '</span>')
        except Exception:
            pass
        table_html = "".join(table_spans)
        return _render_sync_card(color, label, "最后同步: " + time_str, table_html)
    except Exception as e:
        return _render_sync_card("red", "读取失败", str(e), "")


def _render_sync_card(color, label, detail, table_html):
    colors = {"green": "#238636", "yellow": "#d29922", "red": "#f85149"}
    c = colors.get(color, "#f85149")
    parts = []
    parts.append('<div style="background:#161b22;border:1px solid ' + c + ';border-top:3px solid ' + c + ';border-radius:10px;padding:12px 16px;margin-bottom:20px;">')
    parts.append('  <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">')
    parts.append('    <span style="color:' + c + ';font-weight:700;font-size:14px;">' + label + '</span>')
    parts.append('    <span style="color:#8b949e;font-size:12px;margin-left:auto;">' + detail + '</span>')
    parts.append("  </div>")
    parts.append('  <div style="display:flex;gap:12px;flex-wrap:wrap;">' + table_html + "</div>")
    parts.append("</div>")
    return "\n".join(parts)


_sync_time_cache = None
def get_sync_time_cached():
    global _sync_time_cache
    if _sync_time_cache is None or time.time() - _sync_time_cache[0] > 60:
        _sync_time_cache = (time.time(), _get_last_sync_time())
    return _sync_time_cache[1]


def _get_last_sync_time():
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30.0)
        row = conn.execute("SELECT sync_time FROM sync_metadata ORDER BY sync_time DESC LIMIT 1").fetchone()
        conn.close()
        if row:
            st = datetime.fromisoformat(row[0])
            return st.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return "未知"


# ===== 导航 =====
NAV_CSS = """<style>
body{margin:0;padding:0;background:#0d1117;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#c9d1d9}
.nav{background:#161b22;border-bottom:1px solid #30363d;padding:0 20px;display:flex;align-items:center;position:sticky;top:0;z-index:100;height:52px;backdrop-filter:blur(8px)}
.nav-logo{font-size:16px;font-weight:700;color:#fff;margin-right:28px}
.nav-logo span{color:#00d4ff}
.nav-links{display:flex;gap:2px}
.nav-links a{padding:8px 14px;border-radius:6px;font-size:13px;color:#8b949e;text-decoration:none;transition:all .15s;white-space:nowrap}
.nav-links a:hover,.nav-links a.active{background:#21262d;color:#fff}
.nav-links a.active{background:rgba(0,212,255,0.1);color:#00d4ff}
.nav-time{margin-left:auto;font-size:12px;color:#484f58}
.content{max-width:95%;margin:0 auto;padding:20px}
.drop-zone{border:2px dashed #30363d;border-radius:8px;padding:20px;text-align:center;cursor:pointer;transition:border-color .2s}
.drop-zone:hover,.drop-zone.dragover{border-color:#00d4ff;background:rgba(0,212,255,0.05)}
::-webkit-scrollbar{width:8px;height:8px}
::-webkit-scrollbar-track{background:#0d1117}
::-webkit-scrollbar-thumb{background:#30363d;border-radius:4px}
::-webkit-scrollbar-thumb:hover{background:#484f58}
</style>"""


def nav(active_page):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sync_time = get_sync_time_cached()
    pages = [
        ('/', '首页', 'home'), ('/daily', '运营日报', 'daily'),
        ('/warehouse', '仓库需求', 'warehouse'), ('/waitcheck', '待审核订单', 'waitcheck'),
        ('/forecast', '销量预测', 'forecast'), ('/finance', '财务结算', 'finance'),
        ('/aftersales', '售后专题', 'aftersales'), ('/afterdashboard', '售后看板', 'aftersales_v2'),
        ('/logistics', '物流报告', 'logistics'), ('/exceptionorders', '异常件', 'exceptionorders'),
        ('/replaceorder', '换单补单', 'replaceorder'), ('/upload', '文件上传', 'upload'),
    ]
    links = ''
    for path, label, key in pages:
        cls = 'active' if key == active_page else ''
        links += f'<a href="{path}" class="{cls}">{label}</a>'
    return f'<div class="nav"><div class="nav-logo">数据中心</div><div class="nav-links">{links}</div><div class="nav-time">数据更新: {sync_time}</div></div>'


def page(title, active, content_html):
    return f'<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>{NAV_CSS}</head><body>{nav(active)}<div class="content">{content_html}</div></body></html>'
