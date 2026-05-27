#!/usr/bin/env bash
# DaLeTou 事件链健康巡检（无需 gh CLI，只用 curl + sqlite3）
# 用法：bash scripts/health-check.sh
#
# 检查项（对应 docs/AI_NOTES.md 的 C1-C5）：
#   C1. 最近 6h backtest run 数（GitHub API 公开 endpoint，无需 auth）
#   C2. 最近 5 次 backtest conclusion 分布
#   C3. 最近一次 evaluate 在多久前
#   C4. 本地 DB 数据新鲜度（最新一期距今天数）
#   C5. RUN_LOG 是否在长（本周新增行数）

set -u  # 别 set -e，curl 失败时也要看本地数据

REPO="616390260/daletou-predictor"
API="https://api.github.com/repos/$REPO/actions/workflows"
DB="data/daletou.db"

# 颜色
G='\033[0;32m'; R='\033[0;31m'; Y='\033[1;33m'; N='\033[0m'
ok()    { echo -e "${G}✓${N} $*"; }
fail()  { echo -e "${R}✗${N} $*"; }
warn()  { echo -e "${Y}⚠${N} $*"; }

echo "============================================================"
echo "DaLeTou 事件链健康巡检 · $(date '+%Y-%m-%d %H:%M:%S %A')"
echo "============================================================"

# ============================================================
# C1. backtest 心跳（最近 6h）
# ============================================================
echo
echo "[C1] backtest 心跳（最近 6h 应 ≥ 1 次 run）"
CUTOFF=$(date -u -v-6H '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null \
       || date -u -d "-6 hours" '+%Y-%m-%dT%H:%M:%SZ')

BT_JSON=$(curl -sS "$API/backtest.yml/runs?per_page=10&created=>$CUTOFF" 2>&1)
BT_COUNT=$(echo "$BT_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total_count', 0))" 2>/dev/null || echo "ERR")

if [ "$BT_COUNT" = "ERR" ]; then
  fail "无法获取 backtest 运行历史（网络/API 限速？）"
elif [ "$BT_COUNT" -ge 1 ]; then
  ok  "backtest 6h 内 $BT_COUNT 次 run，心跳活着"
else
  fail "backtest 6h 内 0 次 run，事件链可能停摆"
  echo "    急救：去 GitHub UI dispatch heartbeat.yml 或 backtest.yml"
fi

# ============================================================
# C2. backtest 最近 5 次 conclusion
# ============================================================
echo
echo "[C2] backtest 最近 5 次 conclusion（不能连续 ≥ 3 次 failure）"
curl -sS "$API/backtest.yml/runs?per_page=5" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f'    解析失败: {e}'); sys.exit(1)
runs = d.get('workflow_runs', [])
if not runs:
    print('    无任何历史 run（workflow 从未启动？）')
    sys.exit()
fails = 0; max_fails = 0
for r in runs:
    s = r.get('conclusion') or r.get('status') or '?'
    ts = r.get('created_at', '?')
    print(f'    {ts}  {s}')
    if s == 'failure':
        fails += 1; max_fails = max(max_fails, fails)
    else:
        fails = 0
if max_fails >= 3:
    print(f'    ⚠️  发现连续 {max_fails} 次失败')
"

# ============================================================
# C3. 最近一次 evaluate 时间
# ============================================================
echo
echo "[C3] evaluate 最近一次运行（开奖日 21:30 后应有）"
curl -sS "$API/evaluate.yml/runs?per_page=3" | python3 -c "
import sys, json, datetime as dt
try:
    d = json.load(sys.stdin)
except Exception as e:
    print(f'    解析失败: {e}'); sys.exit(1)
runs = d.get('workflow_runs', [])
if not runs:
    print('    ✗ evaluate 从未运行过！')
else:
    last = runs[0]
    ts = last.get('created_at', '?')
    s = last.get('conclusion') or last.get('status') or '?'
    if ts != '?':
        ago = dt.datetime.now(dt.timezone.utc) - dt.datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
        hrs = ago.total_seconds() / 3600
        print(f'    最近: {ts} ({hrs:.1f}h 前) {s}')
        if hrs > 72:
            print(f'    ⚠️  超过 72h，已错过至少 1 个开奖日')
"

# ============================================================
# C4. 本地 DB 数据新鲜度
# ============================================================
echo
echo "[C4] 本地 DB 最新数据"
if [ -f "$DB" ]; then
  sqlite3 "$DB" "SELECT '    最新一期: ' || MAX(issue) || '  开奖日: ' || MAX(draw_date) || '  距今: ' || ROUND(julianday('now') - julianday(MAX(draw_date)), 1) || ' 天' FROM draws;"
  PRED_NEXT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM predictions WHERE issue=(SELECT MAX(issue)+1 FROM draws);")
  if [ "$PRED_NEXT" -gt 0 ]; then
    ok  "下一期已有 $PRED_NEXT 条预测"
  else
    warn "下一期还没有预测"
  fi
else
  fail "$DB 不存在"
fi

# ============================================================
# C5. RUN_LOG 状态
# ============================================================
echo
echo "[C5] RUN_LOG 反思引擎数据源"
if [ -f docs/RUN_LOG.jsonl ]; then
  LINES=$(wc -l < docs/RUN_LOG.jsonl)
  WEEK_LINES=$(git log --since="7 days ago" --oneline -- docs/RUN_LOG.jsonl 2>/dev/null | wc -l | tr -d ' ')
  ok "RUN_LOG 共 $LINES 行，本周新增 commit $WEEK_LINES 条"
else
  fail "docs/RUN_LOG.jsonl 不存在（反思系统无数据）"
  echo "    → 等任一 workflow 跑通后会首次入库"
fi

# ============================================================
# C6. 本地 vs origin 同步
# ============================================================
echo
echo "[C6] 本地 vs origin/main 同步"
git fetch origin --quiet 2>&1
AHEAD=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo "?")
BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "?")
if [ "$AHEAD" = "0" ] && [ "$BEHIND" = "0" ]; then
  ok "本地与 origin/main 完全同步"
else
  warn "ahead=$AHEAD  behind=$BEHIND"
fi

echo
echo "============================================================"
echo "结论 quick reference："
echo "  C1=0  → dispatch heartbeat.yml + backtest.yml"
echo "  C2 连续 ≥3 failure → 看最新失败 run logs，可能 yml 改坏"
echo "  C3 > 72h → 错过开奖，手动 dispatch evaluate.yml"
echo "  C4 > 4 天 → 抓取问题，看 fetch_history"
echo "  C5 不存在 → 等 workflow 跑通后自然入库"
echo "============================================================"
