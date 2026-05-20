#!/bin/bash
# 上传文件自动同步到服务器并触发导入
# 用法：把文件放到 ~/pdd/Claudecode/uploads/，此脚本每 30 分钟检查一次

UPLOAD_DIR="$HOME/pdd/Claudecode/uploads"
ARCHIVE_DIR="$UPLOAD_DIR/archive"
SERVER="macmini4pro@100.101.170.7"
SERVER_UPLOAD_DIR="~/pdd/Claudecode/uploads"

# 查找待上传的文件（排除 archive 子目录）
FILES=$(find "$UPLOAD_DIR" -maxdepth 1 -type f \( -name "*.xlsx" -o -name "*.csv" \) 2>/dev/null)

if [ -z "$FILES" ]; then
    exit 0
fi

echo "[$(date)] 发现新文件，开始同步..."

# 1. 同步文件到服务器
for f in $FILES; do
    fname=$(basename "$f")
    echo "  上传: $fname"
    scp "$f" "$SERVER:$SERVER_UPLOAD_DIR/" 2>/dev/null
done

# 2. 触发服务器上的导入脚本
echo "  触发导入..."
ssh "$SERVER" 'cd ~/pdd/Claudecode && python3 erp_as_import.py 2>&1 | tail -5' 2>/dev/null
ssh "$SERVER" 'cd ~/pdd/Claudecode && python3 erp_finance_match.py 2>&1 | tail -5' 2>/dev/null

# 3. 移动到归档目录
mkdir -p "$ARCHIVE_DIR"
for f in $FILES; do
    fname=$(basename "$f")
    mv "$f" "$ARCHIVE_DIR/${fname}" 2>/dev/null
    echo "  已归档: $fname"
done

echo "[$(date)] 同步完成"
