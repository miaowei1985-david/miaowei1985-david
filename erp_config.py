#!/usr/bin/env python3
"""
ERP 统一配置 + 公共工具
优先级：环境变量 > 默认值
"""
import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.header import Header

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ===== 数据库 =====
DB_PATH = os.path.join(BASE_DIR, "erp_all.db")

# ===== ERP API =====
ERP_API_BASE = "https://erp.huice.com"
SHOP_NAME = "榴愿时刻工厂店"

# ===== 仪表盘 =====
DASHBOARD_PORT = 9999

# ===== 文件上传 =====
UPLOAD_DIRS = {
    "finance": os.path.join(BASE_DIR, "uploads"),
    "after_sales": os.path.join(BASE_DIR, "uploads"),
    "weight": os.path.join(BASE_DIR, "产品规格"),
    "product_cost": os.path.join(BASE_DIR, "产品成本"),
}
TYPE_LABELS = {
    "finance": "财务对账",
    "after_sales": "售后数据",
    "weight": "规格重量",
    "product_cost": "产品成本",
}
for _d in UPLOAD_DIRS.values():
    os.makedirs(_d, exist_ok=True)

# ===== 邮件默认配置 =====
EMAIL_FROM = os.environ.get("ERP_EMAIL_FROM", "88187402@qq.com")
EMAIL_AUTH = os.environ.get("ERP_EMAIL_AUTH", "")  # 必须设置
SMTP_HOST = os.environ.get("ERP_SMTP_HOST", "smtp.qq.com")
SMTP_PORT = int(os.environ.get("ERP_SMTP_PORT", "465"))

# 收件人列表（不同报告不同）
EMAIL_TO_FULL = [
    "88187402@qq.com", "17821279335@163.com", "hb-champion@foxmail.com",
    "136941100@qq.com", "903470571@qq.com", "179710675@qq.com",
    "32507586@qq.com", "michealzheng2000@gmail.com", "398307955@qq.com",
    "552865418@qq.com", "985693739@qq.com", "949547543@qq.com",
]

EMAIL_TO_WAIT_CHECK = [
    "88187402@qq.com", "17821279335@163.com",
    "hb-champion@foxmail.com", "136941100@qq.com", "985693739@qq.com", "949547543@qq.com",
]

# ===== 主题色 =====
THEME = {
    'ACCENT': '#00d4ff', 'GREEN': '#00e676', 'AMBER': '#ffc107',
    'RED': '#ff5252', 'DARK_BG': '#0d1117', 'CARD_BG': '#161b22',
    'TABLE_HEAD': '#1c2333', 'BORDER': '#30363d',
    'TEXT': '#c9d1d9', 'TEXT_DIM': '#8b949e', 'TEXT_BRIGHT': '#ffffff',
}

# ===== 公共函数 =====
def send_email(subject, html_content, to_list=None):
    """发送 HTML 邮件"""
    if to_list is None:
        to_list = EMAIL_TO_FULL
    if not EMAIL_AUTH:
        raise ValueError("EMAIL_AUTH not set. Set ERP_EMAIL_AUTH env var.")

    msg = MIMEText(html_content, 'html', 'utf-8')
    msg['From'] = Header(EMAIL_FROM)
    msg['To'] = ', '.join(to_list)
    msg['Subject'] = Header(subject, 'utf-8')

    server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
    server.login(EMAIL_FROM, EMAIL_AUTH)
    server.sendmail(EMAIL_FROM, to_list, msg.as_string())
    server.quit()
    print("✅ 邮件发送成功")


def setup_logger(name, log_file, level=logging.INFO):
    """统一日志初始化"""
    logging.basicConfig(
        filename=log_file,
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return logging.getLogger(name)


import sqlite3

def get_db_connection(db_path=None, readonly=False):
    """获取数据库连接，自动设置 busy_timeout 防锁冲突"""
    if db_path is None:
        db_path = DB_PATH
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")  # 30 秒等待
    conn.execute("PRAGMA journal_mode = WAL")
    if readonly:
        conn.execute("PRAGMA query_only = ON")
    return conn
