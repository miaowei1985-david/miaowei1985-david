#!/bin/bash
# Claude Code 自动分析日报（5:05 运行）
# 侧重：上网学习业界最佳实践，为系统提出改进方案

source ~/.nvm/nvm.sh 2>/dev/null
export PATH=$HOME/.nvm/versions/node/v24.15.0/bin:$PATH

LOG=/tmp/erp_daily_review.log
ANALYSIS=/tmp/erp_daily_analysis.md

if [ ! -f $LOG ]; then
    echo "日报文件不存在，跳过分析"
    exit 0
fi

# 取最新的日志内容
REVIEW=$(tail -100 $LOG)

claude -p "你是资深 DevOps 和电商系统架构师。请先阅读以下系统运行数据，然后做以下工作：

1. **问题诊断**：分析当前系统存在的隐患和瓶颈
2. **业界最佳实践搜索**：用 WebSearch 搜索最新的 ERP 系统架构、数据库优化、物流追踪、邮件自动化、SQLite 性能优化等方面的最佳实践和新技术
3. **改进建议**：基于搜索到的业界方案，提出适合我们系统（Python 3.9 + SQLite + cron + 小型电商团队）的具体改进措施
4. **学习总结**：今天学到了什么新技术/新思路，可以应用到我们的系统中

系统运行数据：
$REVIEW

要求：
- 输出写到 stdout，中文，精炼但不超过 500 字
- 重点在业界方案对比和改进建议，不在重复已有数据
- 如果发现 Token 过期等紧急问题，放在最前面" >> $ANALYSIS 2>&1

echo "[$(date)] Claude Code 分析完成" >> /tmp/erp_daily_review.log
