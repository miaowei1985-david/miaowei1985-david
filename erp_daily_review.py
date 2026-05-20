#!/usr/bin/env python3
"""每日系统自动巡检脚本，凌晨5点运行，输出到日志。"""
import sqlite3
import os
import subprocess
import json
import base64
import re
from datetime import datetime

# BASE moved to erp_config
DB = os.path.join(BASE, 'erp_all.db')
LOG = '/tmp/erp_daily_review.log'

def run(cmd, timeout=30):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f'ERROR: {e}'

def check_token_expiry():
    cron = run('crontab -l')
    tokens = re.findall(r'eyJ[^"\s]+', cron)
    for tok in tokens:
        parts = tok.split('.')
        if len(parts) >= 2:
            try:
                payload = parts[1]
                payload += '=' * (4 - len(payload) % 4)
                data = json.loads(base64.urlsafe_b64decode(payload))
                exp = datetime.fromtimestamp(data['exp'])
                days = (exp - datetime.now()).days
                if days < 0:
                    return f'EXPIRED (过期 {abs(days)} 天)'
                elif days < 3:
                    return f'即将过期 (剩余 {days} 天)'
                else:
                    return f'正常 (剩余 {days} 天)'
            except Exception:
                continue
    return '未找到 Token'

def main():
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = []
    lines.append(f'=== ERP 系统日报 ({now}) ===')
    lines.append('')

    # 1. Dashboard
    ps = run('ps aux | grep erp_dashboard | grep -v grep')
    lines.append(f'Dashboard: {"RUNNING" if "erp_dashboard" in ps else "NOT RUNNING"}')

    # 2. DB 表行数
    lines.append('')
    lines.append('数据库表:')
    try:
        conn = sqlite3.connect(DB)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for t in tables:
            try:
                cnt = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                lines.append(f'  {t:25s} {cnt:>10,}')
            except:
                lines.append(f'  {t:25s} ERROR')
        conn.close()
    except:
        lines.append('  DB 无法连接')

    # 3. 磁盘
    df = run('df -h /')
    lines.append(f'磁盘: {df.split(chr(10))[-1].strip()}')

    # 4. Token
    lines.append(f'Token: {check_token_expiry()}')

    # 5. 最近错误
    lines.append('')
    lines.append('最近错误 (fetch日志):')
    errs = run('grep -i "error" /tmp/erp_fetch.log | tail -5')
    if errs:
        for e in errs.split('\n'):
            lines.append(f'  {e}')
    else:
        lines.append('  无错误')

    # 6. 外接硬盘
    vols = run('ls /Volumes/ 2>/dev/null')
    has_ext = 'DATE2' in vols
    lines.append(f'外接硬盘: {"已挂载" if has_ext else "未挂载"}')

    # 7. 最近日志
    lines.append('')
    lines.append('最近邮件发送:')
    email_log = run('tail -3 /tmp/erp_email.log')
    if email_log:
        for e in email_log.split('\n'):
            lines.append(f'  {e}')

    lines.append('')
    lines.append('最近物流轨迹:')
    log_log = run('tail -3 /tmp/erp_logistics_trace.log')
    if log_log:
        for e in log_log.split('\n'):
            lines.append(f'  {e}')

    lines.append('')
    lines.append('--- 结束 ---')
    lines.append('')

    report = '\n'.join(lines)
    with open(LOG, 'a') as f:
        f.write(report)
    print(report)

if __name__ == '__main__':
    main()
