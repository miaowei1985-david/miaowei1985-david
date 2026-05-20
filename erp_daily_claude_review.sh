#!/bin/bash
# Claude Code 自动分析日报（5:05 运行）
# 两部分：系统运维分析 + 业务流全链路分析

source ~/.nvm/nvm.sh 2>/dev/null
export PATH=$HOME/.nvm/versions/node/v24.15.0/bin:$PATH

LOG=/tmp/erp_daily_review.log
ANALYSIS=/tmp/erp_daily_analysis.md
DB=~/pdd/Claudecode/erp_all.db

if [ ! -f $LOG ]; then
    echo "日报文件不存在，跳过分析"
    exit 0
fi

# ====== 第一部分：系统运行数据 ======
REVIEW=$(tail -100 $LOG)

# ====== 第二部分：业务数据快照 ======
BUSINESS_DATA=$(sqlite3 "$DB" <<'SQL' 2>/dev/null
-- 1. 订单转化漏斗（待审核→待发货→已完成）
SELECT '=== 订单转化漏斗 ===' AS '';
SELECT '待审核: ' || COUNT(*) FROM erp_wait_check WHERE shopName = '榴愿时刻工厂店';
SELECT '待发货: ' || COUNT(*) FROM erp_wait_send_self WHERE shopName = '榴愿时刻工厂店';
SELECT '已完成: ' || COUNT(*) FROM erp_finished WHERE shopName = '榴愿时刻工厂店';

-- 2. 发货时效分布
SELECT '=== 发货时效分布 ===' AS '';
SELECT estimateConsignTime || ': ' || COUNT(*) FROM erp_wait_check WHERE shopName = '榴愿时刻工厂店' GROUP BY estimateConsignTime ORDER BY estimateConsignTime;

-- 3. 仓库发货统计
SELECT '=== 仓库发货统计 ===' AS '';
SELECT warehouseName || ': ' || COUNT(*) || ' 单' FROM erp_wait_send_self WHERE shopName = '榴愿时刻工厂店' GROUP BY warehouseName ORDER BY COUNT(*) DESC;

-- 4. 物流状态分布（最新100条）
SELECT '=== 物流状态分布 ===' AS '';
SELECT operate_category || ': ' || COUNT(DISTINCT logistics_no) FROM logistics_trace GROUP BY operate_category ORDER BY COUNT(DISTINCT logistics_no) DESC;

-- 5. 售后概况
SELECT '=== 售后概况 ===' AS '';
SELECT '售后总单数: ' || COUNT(*) FROM after_sales;

-- 6. 财务盈亏
SELECT '=== 财务匹配概况 ===' AS '';
SELECT '已匹配: ' || COUNT(*) || ' 单' FROM finance_matched;
SELECT '未匹配: ' || COUNT(*) || ' 单' FROM finance_unmatched;

-- 7. 近7天每日发货量趋势
SELECT '=== 近7天发货趋势 ===' AS '';
SELECT SUBSTR(consignTime, 1, 10) || ': ' || COUNT(*) FROM erp_finished WHERE shopName = '榴愿时刻工厂店' AND consignTime >= date('now', '-7 days') GROUP BY SUBSTR(consignTime, 1, 10) ORDER BY 1;
SQL
)

claude -p "你是资深电商运营分析师和 DevOps 工程师。请先阅读以下系统运行数据和业务数据，然后做以下工作：

---

## 第一部分：系统运维分析

1. **问题诊断**：分析当前系统存在的隐患和瓶颈
2. **业界最佳实践搜索**：用 WebSearch 搜索最新的 ERP 系统架构、数据库优化、物流追踪、邮件自动化、SQLite 性能优化等方面的最佳实践
3. **改进建议**：基于业界方案，提出适合我们系统（Python 3.9 + SQLite + cron + 小型电商团队）的具体改进措施

系统运行数据：
$REVIEW

---

## 第二部分：业务流全链路分析

### 订单流分析
- 待审核→待发货→已完成的转化率
- 积压趋势（待审核订单量是否堆积）
- 发货时效分布（24h/48h/72h 各占比）
- 超时未发订单风险预警

### 仓库与物流分析
- 各仓库发货效率对比
- 跨境物流管道（泰国→深圳→签收）各环节瓶颈
- 滞留件、问题件趋势

### 售后与财务分析
- 售后退款率、主要理赔原因
- 财务匹配情况（已匹配 vs 未匹配）
- 近7天发货趋势（增长/下降/平稳）

### 经营建议
- 哪些 SKU 亏损需要调整定价
- 哪些仓库/物流公司需要优化
- 运营流程可以自动化/简化的环节

---

业务数据：
$BUSINESS_DATA

---

要求：
- 输出写到 stdout，中文，精炼
- 紧急问题（Token 过期、数据库损坏、超时未发订单）放在最前面
- 业务分析要有具体数字支撑，不要空话
- 改进建议要可执行、有优先级" >> $ANALYSIS 2>&1

echo "[$(date)] Claude Code 分析完成" >> /tmp/erp_daily_review.log
