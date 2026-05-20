#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ERP 仪表盘 — 路由 + HTTPServer + 缓存框架
页面渲染逻辑已拆分到 dashboard/pages_*.py
端口: 9999
"""
import os
import sys
import json
import cgi
import shutil
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import logging
logging.basicConfig(filename="/tmp/erp_dashboard_app.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ===== 配置 =====
from erp_config import UPLOAD_DIRS, DASHBOARD_PORT
from dashboard import get_cached_html, clear_cache, BASE, CACHE_TTL

# ===== 导入所有页面渲染函数 =====
from dashboard.pages_home import render_home
from dashboard.pages_daily import render_daily
from dashboard.pages_warehouse import render_warehouse
from dashboard.pages_waitcheck import render_waitcheck
from dashboard.pages_forecast import render_forecast
from dashboard.pages_finance import render_finance
from dashboard.pages_aftersales import render_aftersales
from dashboard.pages_aftersales_v2 import render_aftersales_v2
from dashboard.pages_logistics import render_logistics_cached
from dashboard.pages_exception import render_exceptionorders
from dashboard.pages_replace import render_replaceorder
from dashboard.pages_upload import render_upload, upload_files_list

# ===== 扩展缓存清除（含子模块私有缓存） =====
_orig_clear_cache = clear_cache
def clear_all_cache():
    _orig_clear_cache()
    try:
        import dashboard.pages_logistics as lg
        lg._logistics_cache = None
    except Exception:
        pass
    try:
        import dashboard.pages_exception as exc
        exc._exception_cache = None
    except Exception:
        pass


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ERPHandler(BaseHTTPRequestHandler):
    allow_reuse_address = True

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        if path == '/':
            self._send_html(render_home())
        elif path == '/daily':
            shop = qs.get('shop', [None])[0]
            self._send_html(render_daily(shop))
        elif path == '/warehouse':
            self._send_html(render_warehouse())
        elif path == '/waitcheck':
            self._send_html(render_waitcheck())
        elif path == '/forecast':
            self._send_html(render_forecast())
        elif path == '/finance':
            self._send_html(render_finance())
        elif path == '/aftersales':
            self._send_html(render_aftersales())
        elif path == '/afterdashboard':
            self._send_html(render_aftersales_v2())
        elif path == '/logistics':
            self._send_html(render_logistics_cached())
        elif path == '/exceptionorders':
            self._send_html(render_exceptionorders())
        elif path == '/replaceorder':
            self._send_html(render_replaceorder())
        elif path == '/upload':
            self._send_html(render_upload())
        elif path == '/files':
            self._json_response({'files': upload_files_list()})
        elif path == '/refresh-cache':
            clear_all_cache()
            self._json_response({'ok': True, 'msg': '缓存已清除，下次访问将重新生成'})
        elif path.startswith('/templates/'):
            self._serve_template(path)
        else:
            self._404()

    def do_POST(self):
        if self.path == '/chunk':
            try:
                length = int(self.headers.get('Content-Length', 0))
                data = self.rfile.read(length)
                req = json.loads(data.decode('utf-8'))
                chunk_id = req.get('chunkId')
                filename = req.get('filename')
                chunk_data = req.get('data')
                total_chunks = req.get('totalChunks')
                type_item = req.get('type')

                if chunk_id is None or not filename or not chunk_data or not type_item:
                    self._json_response({'ok': False, 'msg': '缺少参数'}, 400)
                    return

                if type_item not in UPLOAD_DIRS:
                    self._json_response({'ok': False, 'msg': f'未知类型: {type_item}'}, 400)
                    return

                import base64
                chunk_dir = os.path.join(UPLOAD_DIRS[type_item], '.chunks_' + filename)
                os.makedirs(chunk_dir, exist_ok=True)
                chunk_path = os.path.join(chunk_dir, str(chunk_id))
                with open(chunk_path, 'wb') as f:
                    f.write(base64.b64decode(chunk_data))

                uploaded = len(os.listdir(chunk_dir))
                if uploaded == total_chunks:
                    final_path = os.path.join(UPLOAD_DIRS[type_item], filename)
                    with open(final_path, 'wb') as outfile:
                        for i in range(total_chunks):
                            chunk_file = os.path.join(chunk_dir, str(i))
                            with open(chunk_file, 'rb') as infile:
                                outfile.write(infile.read())
                    shutil.rmtree(chunk_dir)
                    self._json_response({'ok': True, 'msg': f'上传完成: {filename}', 'merged': True})
                else:
                    self._json_response({'ok': True, 'msg': f'块 {chunk_id+1}/{total_chunks} 上传成功', 'merged': False})
            except Exception as e:
                self._json_response({'ok': False, 'msg': f'错误: {str(e)}'}, 500)
            return
        if self.path == '/upload':
            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' not in content_type:
                self._json_response({'ok': False, 'msg': '请使用 multipart/form-data'}, 400)
                return
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers,
                environ={'REQUEST_METHOD': 'POST'})
            file_item = form['file']
            type_item = form.getvalue('type')
            if file_item is None or not type_item:
                self._json_response({'ok': False, 'msg': '缺少文件或类型参数'}, 400)
                return
            if type_item not in UPLOAD_DIRS:
                self._json_response({'ok': False, 'msg': f'未知类型: {type_item}'}, 400)
                return
            filename = os.path.basename(file_item.filename)
            dest_dir = UPLOAD_DIRS[type_item]
            dest_path = os.path.join(dest_dir, filename)
            with open(dest_path, 'wb') as f:
                shutil.copyfileobj(file_item.file, f)
            self._json_response({'ok': True, 'msg': f'上传成功: {filename}'})
        else:
            self._404()

    def _send_html(self, html_str):
        body = html_str.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Connection', 'keep-alive')
        self.send_header('Keep-Alive', 'timeout=15, max=100')
        self.end_headers()
        self.wfile.write(body)

    def _json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(body))
        self.send_header('Connection', 'keep-alive')
        self.send_header('Keep-Alive', 'timeout=15, max=100')
        self.end_headers()
        self.wfile.write(body)

    def _404(self):
        self.send_response(404)
        self.send_header('Connection', 'keep-alive')
        self.send_header('Keep-Alive', 'timeout=15, max=100')
        self.end_headers()

    def _serve_template(self, path):
        import mimetypes
        from urllib.parse import quote, unquote
        filename = unquote(os.path.basename(path))
        safe_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.')
        if not all(c in safe_chars or ord(c) > 127 for c in filename):
            self._404()
            return
        tmpl_dir = os.path.join(BASE, 'templates')
        filepath = os.path.join(tmpl_dir, filename)
        if not os.path.isfile(filepath):
            self._404()
            return
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        with open(filepath, 'rb') as f:
            data = f.read()
        safe_name = quote(filename.encode('utf-8'))
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Disposition', f'attachment; filename*=UTF-8\'\'{safe_name}')
        self.send_header('Content-Length', len(data))
        self.send_header('Connection', 'keep-alive')
        self.send_header('Keep-Alive', 'timeout=15, max=100')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        if '/files' not in str(args):
            print(f"[{self.log_date_time_string()}] {format % args}")


def main():
    port = DASHBOARD_PORT
    for i, arg in enumerate(sys.argv):
        if arg == '--port' and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    # 启动时预生成缓存
    print("正在预生成页面缓存...")
    for script in ['erp_full_report_email.py', 'erp_warehouse_demand_email.py', 'erp_wait_check_email.py']:
        try:
            get_cached_html(script)
            print(f"  ✓ {script}")
        except Exception as e:
            print(f"  ✗ {script}: {e}")
    server = ThreadingHTTPServer(('0.0.0.0', port), ERPHandler)
    print(f"ERP 仪表盘已启动 — http://100.101.170.7:{port}")
    print(f"缓存有效期: {CACHE_TTL}秒，访问 /refresh-cache 可手动刷新")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
