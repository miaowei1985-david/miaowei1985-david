#!/bin/bash
# ERP 数据拉取脚本（每 15 分钟）
cd ~/pdd/Claudecode
if [ -n "$ERP_TOKEN" ]; then
    echo "[$(date)] 拉取 ERP 数据..."
    python3 erp_fetch.py 2>&1 | tail -2
else
    echo "[$(date)] 无 ERP_TOKEN，跳过数据拉取"
fi
