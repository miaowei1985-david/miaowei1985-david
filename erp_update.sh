#!/bin/bash
# 一键更新 ERP 数据 + 发送邮件
# 用法: ./erp_update.sh <token>
# 例如: ./erp_update.sh "eyJ0eXA..."

if [ -z "$1" ]; then
    echo "用法: ./erp_update.sh <ERP_TOKEN>"
    echo ""
    echo "从浏览器获取 token 的方法："
    echo "  1. 打开 ERP 网页，F12 开发者工具"
    echo "  2. Network 标签，刷新页面"
    echo "  3. 找到任意 /api/main/ 请求，右键 Copy as cURL"
    echo "  4. 粘贴到聊天中，我来提取 token"
    exit 1
fi

export ERP_TOKEN="$1"
cd ~/pdd/Claudecode

echo "=== 拉取 ERP 数据 ==="
python3 erp_fetch.py 2>&1 | grep -E "^[  💾]"

echo "=== 发送运营日报 ==="
python3 erp_full_report_email.py 2>&1 | tail -1

echo "=== 发送仓库日报 ==="
python3 erp_warehouse_demand_email.py 2>&1 | tail -1

echo "✅ 更新完成"
