#!/bin/bash
# ERP 邮件发送脚本（不拉取数据，依赖 fetch cron 提供数据）
cd ~/pdd/Claudecode
if [ -n "$ERP_EMAIL_AUTH" ]; then
    echo "[$(date)] 发送运营日报..."
    python3 erp_full_report_email.py --email 2>&1 | tail -1
    echo "[$(date)] 发送仓库日报..."
    python3 erp_warehouse_demand_email.py --email 2>&1 | tail -1
    echo "[$(date)] 发送待审核订单明细..."
    python3 erp_wait_check_email.py --email 2>&1 | tail -1
else
    echo "[$(date)] 无 ERP_EMAIL_AUTH，跳过邮件发送"
fi
