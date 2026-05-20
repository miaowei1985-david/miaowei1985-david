#!/usr/bin/env python3
"""
简单文件上传服务器
端口: 9999
用法: python3 upload_server.py [--port 9999]
访问: http://<MacMini的IP>:9999
"""

import os
import sys
import json
import html
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
import cgi
import shutil

from erp_config import UPLOAD_DIRS, TYPE_LABELS

HTML_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ERP 文件上传</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #f5f5f7; padding: 20px; }
.container { max-width: 600px; margin: 0 auto; }
h1 { text-align: center; color: #1d1d1f; margin-bottom: 30px; font-size: 24px; }
.card { background: #fff; border-radius: 12px; padding: 24px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.card h3 { color: #1d1d1f; margin-bottom: 12px; font-size: 16px; }
.card p { color: #86868b; font-size: 13px; margin-bottom: 16px; }
.drop-zone {
  border: 2px dashed #d2d2d7; border-radius: 8px; padding: 24px;
  text-align: center; cursor: pointer; transition: border-color 0.2s;
}
.drop-zone:hover, .drop-zone.dragover { border-color: #007aff; background: #f0f7ff; }
.drop-zone input { display: none; }
.drop-zone .icon { font-size: 32px; margin-bottom: 8px; }
.drop-zone .text { color: #007aff; font-size: 14px; }
.drop-zone .file-name { color: #34c759; font-size: 14px; margin-top: 8px; display: none; }
.btn {
  display: block; width: 100%; padding: 12px; border: none; border-radius: 8px;
  background: #007aff; color: #fff; font-size: 16px; cursor: pointer; margin-top: 12px;
}
.btn:hover { background: #0056cc; }
.btn:disabled { background: #ccc; cursor: not-allowed; }
#result { display: none; padding: 12px; border-radius: 8px; margin-top: 12px; font-size: 14px; }
#result.ok { background: #e8f9e8; color: #2d8a2d; }
#result.err { background: #fde8e8; color: #c0392b; }
.file-list { margin-top: 12px; }
.file-list table { width: 100%; border-collapse: collapse; font-size: 13px; }
.file-list th, .file-list td { padding: 6px 8px; text-align: left; border-bottom: 1px solid #f0f0f0; }
.file-list th { color: #86868b; font-weight: 500; }
</style>
</head>
<body>
<div class="container">
<h1>ERP 文件上传</h1>

<div class="card">
  <h3>财务对账 CSV</h3>
  <p>上传"订单结算明细对账.csv"文件</p>
  <div class="drop-zone" id="drop_finance" onclick="document.getElementById('file_finance').click()">
    <div class="icon">📊</div>
    <div class="text">点击或拖拽文件</div>
    <div class="file-name" id="fname_finance"></div>
    <input type="file" id="file_finance" accept=".csv" onchange="showName('finance')">
  </div>
  <button class="btn" onclick="upload('finance')">上传财务文件</button>
</div>

<div class="card">
  <h3>售后数据 Excel</h3>
  <p>上传售后明细 .xlsx 文件</p>
  <div class="drop-zone" id="drop_after_sales" onclick="document.getElementById('file_after_sales').click()">
    <div class="icon">📋</div>
    <div class="text">点击或拖拽文件</div>
    <div class="file-name" id="fname_after_sales"></div>
    <input type="file" id="file_after_sales" accept=".xlsx" onchange="showName('after_sales')">
  </div>
  <button class="btn" onclick="upload('after_sales')">上传售后文件</button>
</div>

<div class="card">
  <h3>产品规格重量 Excel</h3>
  <p>上传"规格品名重量.xlsx"文件</p>
  <div class="drop-zone" id="drop_weight" onclick="document.getElementById('file_weight').click()">
    <div class="icon">⚖️</div>
    <div class="text">点击或拖拽文件</div>
    <div class="file-name" id="fname_weight"></div>
    <input type="file" id="file_weight" accept=".xlsx" onchange="showName('weight')">
  </div>
  <button class="btn" onclick="upload('weight')">上传规格文件</button>
</div>

<div class="card file-list">
  <h3>已上传文件</h3>
  <table>
    <thead><tr><th>文件名</th><th>类型</th><th>时间</th><th>大小</th></tr></thead>
    <tbody id="file_list"></tbody>
  </table>
</div>

<div id="result"></div>
</div>

<script>
const FILES = {};
function showName(type) {
  const f = document.getElementById('file_' + type).files[0];
  if (f) {
    FILES[type] = f;
    const el = document.getElementById('fname_' + type);
    el.textContent = f.name;
    el.style.display = 'block';
  }
}
// Drag & drop
['finance','after_sales','weight'].forEach(type => {
  const dz = document.getElementById('drop_' + type);
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', e => {
    e.preventDefault(); dz.classList.remove('dragover');
    const f = e.dataTransfer.files[0];
    if (f) {
      FILES[type] = f;
      const el = document.getElementById('fname_' + type);
      el.textContent = f.name;
      el.style.display = 'block';
    }
  });
});

async function upload(type) {
  const file = FILES[type];
  if (!file) { showResult('请先选择文件', false); return; }
  const btn = document.querySelectorAll('.btn')[['finance','after_sales','weight'].indexOf(type)];
  btn.disabled = true;
  btn.textContent = '上传中...';
  showResult('正在上传 ' + file.name + '...', true);
  const fd = new FormData();
  fd.append('file', file);
  fd.append('type', type);
  try {
    const r = await fetch('/upload', { method: 'POST', body: fd });
    const text = await r.text();
    try {
      const d = JSON.parse(text);
      showResult(d.msg, d.ok);
      if (d.ok) { loadFiles(); FILES[type] = null; document.getElementById('file_' + type).value = ''; document.getElementById('fname_' + type).style.display = 'none'; }
    } catch(je) { showResult('上传成功: ' + file.name, true); loadFiles(); }
  } catch(e) { showResult('上传失败: ' + e.message, false); }
  btn.disabled = false;
  btn.textContent = ['上传财务文件','上传售后文件','上传规格文件'][['finance','after_sales','weight'].indexOf(type)];
}

function showResult(msg, ok) {
  const el = document.getElementById('result');
  el.textContent = msg;
  el.className = ok ? 'ok' : 'err';
  el.style.display = 'block';
  setTimeout(() => el.style.display = 'none', 15000);
}

async function loadFiles() {
  try {
    const r = await fetch('/files');
    const data = await r.json();
    const tbody = document.getElementById('file_list');
    tbody.innerHTML = data.files.map(f =>
      `<tr><td>${f.name}</td><td>${f.type}</td><td>${f.time}</td><td>${f.size}</td></tr>`
    ).join('');
  } catch(e) {}
}
loadFiles();
</script>
</body>
</html>"""


class UploadHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode('utf-8'))
        elif self.path == '/files':
            files = []
            for dtype, dpath in UPLOAD_DIRS.items():
                if os.path.isdir(dpath):
                    for fn in sorted(os.listdir(dpath), reverse=True):
                        if fn.startswith('._'):
                            continue
                        fp = os.path.join(dpath, fn)
                        if os.path.isfile(fp):
                            st = os.stat(fp)
                            files.append({
                                'name': fn,
                                'type': TYPE_LABELS.get(dtype, dtype),
                                'time': self._fmt_time(st.st_mtime),
                                'size': self._fmt_size(st.st_size),
                            })
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'files': files[:50]}, ensure_ascii=False).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/upload':
            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' not in content_type:
                self._json_response({'ok': False, 'msg': '请使用 multipart/form-data'}, 400)
                return

            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={'REQUEST_METHOD': 'POST'}
            )

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

            self._json_response({
                'ok': True,
                'msg': f'上传成功: {filename} → {dest_dir}',
                'path': dest_path,
            })
        else:
            self.send_response(404)
            self.end_headers()

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _fmt_time(self, ts):
        from datetime import datetime
        return datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')

    def _fmt_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f'{size:.0f}{unit}'
            size /= 1024
        return f'{size:.0f}TB'

    def log_message(self, format, *args):
        if '/files' not in str(args):
            print(f"[{self.log_date_time_string()}] {format % args}")

    def end_headers(self):
        self.send_header('Connection', 'close')
        BaseHTTPRequestHandler.end_headers(self)


def main():
    port = 9999
    for i, arg in enumerate(sys.argv):
        if arg == '--port' and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    server = HTTPServer(('0.0.0.0', port), UploadHandler)
    print(f"文件上传服务器已启动")
    print(f"本机访问: http://localhost:{port}")
    print(f"局域网访问: http://<MacMini的IP>:{port}")
    server.serve_forever()


if __name__ == '__main__':
    main()
