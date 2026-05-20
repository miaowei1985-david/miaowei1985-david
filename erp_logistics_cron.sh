#!/bin/bash
# 物流轨迹拉取脚本（每 6 小时）
cd ~/pdd/Claudecode
if [ -n "$ERP_TOKEN" ]; then
    echo "[$(date)] 拉取物流轨迹..."
    python3 erp_logistics_trace.py 2>&1 | tail -5
else
    echo "[$(date)] 无 ERP_TOKEN，跳过物流轨迹拉取"
fi
