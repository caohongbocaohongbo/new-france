# 22 智能选股聚合中枢 — 汇总 diff.md（核验 + 修复点 + 全链路验收）

> 生成：2026-09-08。依据：docs/22_智能选股聚合中枢_diff核验.md（其内容由施工后 git diff 实时生成）+ docs/22_智能选股聚合中枢_验收报告.md。
> 结构：Part 1 = 6 处 DIFF（before/after + 理由 + 风险表）；Part 2 = P1→P6 问题修复点（附复现命令）；§9 = M0→M4→Render 全链路验收命令。

---

# Part 1　全部 6 处 DIFF

## DIFF ① backend/main.py（三处子改动）

**子改动 1：router 注入**

- before：_new_plugins 列表以 ("chip_scanner", "register_router", "/api/v1/chip-scanner", "筹码集中度") 结束
- after：其后追加 ("smart_picker_hub", "register_router", "/api/v1/smart-picker", "智能选股聚合中枢")

**子改动 2：argparse 两个新 flag**

- before：parser 只有 --run-chip-scanner-once（[插件21]）
- after：追加 --run-smart-picker-hub（仅聚合）与 --run-smart-picker-all（18/19/20/21+hub 同进程连跑，共享K线缓存）

**子改动 3：dispatch 组合入口（置于 _new_cli_map 之前，因该 map 命中即 return）**

- before：--run-* 统一走 _new_cli_map 单插件分发
- after：先判 args.run_smart_picker_all → run_smart_picker_all_cli(args) 后 return；再判 args.run_smart_picker_hub → _run_plugin_cli("backend.plugins.smart_picker_hub", "run_smart_picker_hub_cli", ...)

**变更理由**：给 hub 一个 API 前缀（§6 全部端点）与两个运行入口；组合入口让 18/19/20/21+hub 在同一进程顺序执行，get_kline_cached 共享缓存，全市场 K 线只拉 1 次而非 ×3（§2.1/§3.3）。

## DIFF ② backend/db/plugin_models.py（两表 ORM，+46 行，纯增量）

- before：文件以 ChipHit（21 表）结束
- after：追加 SmartPickerHit（smart_picker_hits：date/code/name/price/change_pct/total_amount/hub_score/hub_score_pct/hit_strategies/resonance/tech_hit/trend_hit/pattern_hit/chip_hit/badges_json/raw_json + idx_sp_date/idx_sp_code/idx_sp_reso）与 PickerPerfDaily（picker_perf_daily：signal_date/code/strategies/hub_score/close_entry/t1_close/t1_ret/t1_filled/t3_*/t5_*/data_missing/updated_at + idx_ppd_date/idx_ppd_code）

**变更理由**：本地历史（?date= 查询）与信号质量追踪需要落库；主读路径仍是快照（G10 口径）。纯增量、不动既有表。

## DIFF ③ 4 个 picker router 读入口切换（同一逻辑 ×4 文件，各 8 行）

以 tech_indicators/router.py 为准（trend / pattern / chip 完全同构，仅注释 G3/G5/G6 编号不同）：

- before：
  - from backend.plugins.common import snapshot_mem_get, snapshot_mem_set
  - from .service import read_code_hits, read_code_kline_with_series, read_latest
  - _latest_cached() 内：payload = read_latest()
- after：
  - from backend.plugins.common import read_snapshot_resilient, snapshot_mem_get, snapshot_mem_set
  - from .service import read_code_hits, read_code_kline_with_series（chip 版为 read_code_distribution, read_code_hits, read_code_kline_with_series）
  - payload = read_snapshot_resilient(SNAPSHOT_NAME)；docstring 改为「无则本地文件优先、data-snapshots raw 兜底（22 方案读入口）」

**变更理由**：路径 B 的云端读链路（方案 §3.1）。改造前云端（Render）无本地快照 → 4 个页面恒空；改造后「本地完成态优先 → data-snapshots raw 兜底」，兜底命中标 _source:"snapshot"。本地行为不变（本地文件优先）。

## DIFF ④ frontend/js/plugins/research_plugins.js（智能选股器段重写，440 行变更）

- before 核心（4 请求 → 4 tab 各渲染一张表）：
  - Promise.all 并发 4 个 apiFetch（/tech-indicators/latest 等）
  - renderSmartPicker(techData, trendData, patternData, chipData) + bindSmartPickerTabs()；详情走各插件 /{code}/kline
- after 核心（1 请求 → 统一总榜 + 服务端筛选/分页 + 质量卡 + 详情）：
  - _spFetch() 单请求 /smart-picker/latest?_spQstr()（pool/min_hit/sort/order/market/limit/offset/q）
  - _spRenderAll()：总榜表（🔥共振行 + 命中策略数 + hub_score(排名) + badges）+ 筛选条 + 质量卡容器
  - bindSmartPickerControls()：tab 降级为 pool 筛选器，搜索/排序/分页全走服务端重发
  - loadSmartPickerPerf()：/smart-picker/perf 质量卡；openSmartPickerDetail()：/smart-picker/{code} + /{code}/chart（cached 图表）

**变更理由**：方案 §9——功能使用（共振 1 屏、搜索/排序/分页、命中解释）、速度（4 请求→1 请求、详情走预计算图表）、准确性展示（数据龄/涨停门控降级黄条/来源标注）。删除死代码（_drawTrend/_drawPattern/_drawChip/_spItem 等 5 个函数）。

## DIFF ⑤ scripts/run_daily.sh（+2 行）

- before：只有 /usr/bin/python3 -m backend.main 一行
- after：其后追加注释一行 + /usr/bin/python3 -m backend.main --run-smart-picker-all 2>&1 | tee -a "$LOG_FILE"

**变更理由**：方案 §3.2 每日 15:10 自动化。**⚠ 注意：本机 crontab 现直接调 backend.main 而未经过此脚本，此改动当前未生效——见 Part 2 P1。**

## DIFF ⑥ 新增文件（before = 不存在）

| 新文件 | 行数 | 要点 |
|---|---|---|
| backend/plugins/smart_picker_hub/config.py | 90 | §5 全部参数 + HUB_* env 覆盖 + 权重校验 |
| backend/plugins/smart_picker_hub/indicators.py | 279 | §4 全部纯函数：extract_rows 去重 / union_table / fill_strategy_pct / compute_hub_score / normalize_weights / apply_gates（复用 common.market_filter 口径）/ filter_items（白名单排序+分页+tie-break）/ explain_* / build_chart_series |
| backend/plugins/smart_picker_hub/service.py | 483 | 编排：load_strategy_rows / load_zt_codes / load_badge_sources / attach_badges / precompute_charts / refresh_perf（无前视回填）/ run_smart_picker_hub_once / query_latest / query_code / query_history / query_meta / read_perf |
| backend/plugins/smart_picker_hub/router.py | 98 | §6 全部 5 端点 + 参数白名单 422 校验 |
| backend/plugins/smart_picker_hub/__init__.py | 56 | register_router + run_smart_picker_hub_cli + run_smart_picker_all_cli |
| backend/plugins/smart_picker_hub/tests/ | 279 | 14 个单测（含无前视） |
| scripts/bench_smart_picker.py | 116 | 10 项验收断言 |

**变更理由**：方案核心交付物（§2.3 组件清单）。独立目录 + main.py try/except 可选加载，符合总计划铁律。

## Part 1 末尾 — 风险汇总表（高 → 低）

| 风险等级 | DIFF | 风险点 | 缓解/验证现状 |
|---|---|---|---|
| 🔴 高 | ④ 前端重写（440 行） | 用户可见页面整体替换；后端契约不符即白屏 | node --check 通过；HTTP 冒烟 15 项通过；grep 审计无残留旧函数引用；真实浏览器点击未覆盖（建议上线前人工点一遍） |
| 🟠 中高 | ③ 4 个 router 读入口 | 改变 4 个既有页面在云端的读行为 | 本地行为不变；云端 raw 兜底首次请求 100–500ms + CDN ≤5min 缓存（已文档化）；远端彻底失败返回 empty 空态而非报错 |
| 🟡 中 | ① main.py | 共享入口改动；dispatch 顺序敏感 | 组合入口已置于 _new_cli_map 之前并实测通过；--help/import/py_compile 三重验证；serve 路径不受 CLI 分支影响 |
| 🟢 低 | ② plugin_models 两表 | 表名冲突/迁移 | 纯增量 ORM，init_db create_all 幂等，两表已实际建表并写入（100 行/60 行） |
| 🟢 低 | ⑥ 新插件 + bench | 新代码缺陷 | 14 单测 + 51 插件回归 + 端到端真实行情五阶段 completed + bench 10 项 PASS |
| 🟢 低（但运维风险单列） | ⑤ run_daily.sh | 代码风险低，但 crontab 未走该脚本 → 未生效 | 见 Part 2 P1（阻断每日自动化，需一条 crontab 命令） |

---

# Part 2　问题修复点（P1→P6 风险降序，附复现命令）

## P1【未修复 · 每日自动化断流】crontab 未接 --run-smart-picker-all

- 现状：crontab -l 实测只有 python3 -m backend.main >> logs/cron.log，不经过 run_daily.sh，18/19/20/21/hub 每日不会自动跑。
- 修复（一行替换，需授权改本机 crontab）：

    crontab -l | sed 's#python3 -m backend.main >>#python3 -m backend.main --run-smart-picker-all >>#' | crontab -

- 复现/验证：crontab -l | grep smart-picker 应有输出；或手动跑 python3 -m backend.main --run-smart-picker-all，日志结尾应见 智能选股聚合中枢链路完成: {tech:completed,...}。

## P2【未修复 · 云端无新功能】main 分支未提交/未推送

- 现状：全部改动停留在本地工作区（8 修改 + 5 新增，未 commit）；Render web 与静态站都还是旧代码——云端没有 hub 接口、没有新前端、4 个页面仍无兜底读。
- 修复（**核验 diff 之后**，需授权 push；本机 ls-remote 读权限已验证）：

    cd /Users/fangcang/new-france
    git add backend/db/plugin_models.py backend/main.py \
      backend/plugins/tech_indicators/router.py backend/plugins/trend_strength/router.py \
      backend/plugins/pattern_scanner/router.py backend/plugins/chip_scanner/router.py \
      frontend/js/plugins/research_plugins.js scripts/run_daily.sh \
      backend/plugins/smart_picker_hub scripts/bench_smart_picker.py \
      "docs/22_智能选股聚合中枢.md"
    git commit -m "feat: 22 智能选股聚合中枢（路径B：聚合总榜+共振+信号质量追踪）"
    git push origin main

- 复现/验证：git ls-remote origin main 返回新 SHA；Render 仪表盘 blueprints 显示 deploy active；随后执行 §9 的 Render 抽查命令。

## P3【未执行 · 云端无当日数据】data-snapshots 分支无聚合快照

- 现状：本地 reports/ 已有 2026-09-08 全套真实快照（策略×4 + smart_picker + smart_picker_charts），但未提交到 data-snapshots，Render web 兜底读不到数据。
- 修复（需 push 写权限）：

    cd /Users/fangcang/new-france && bash scripts/commit_screening_data.sh

- 复现/验证：

    git ls-remote origin data-snapshots   # SHA 应前进
    curl -s https://raw.githubusercontent.com/caohongbocaohongbo/new-france/data-snapshots/reports/smart_picker_latest.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["date"])'

## P4【已修复 · 载荷预算】120KB → 150KB（实测 124,304B）

- 原因：items 全字段（hits/badges/explain）实测超原估算 4KB；bench 与方案 §1/§7.3 已同步修正。
- 复现/验证：python3 scripts/bench_smart_picker.py | grep payload → [PASS] payload_bytes: 124304B (预算 ≤150000B)。

## P5【已修复 · 图表两处偏差】charts 独立快照 + days 截尾

- 原因：① 40 只 × 80 bar 全序列实测 ~2.2MB，与主快照预算冲突 → 独立双写 smart_picker_charts_latest.json，主快照只存 charts_precomputed:{count,n,codes}；② get_kline_cached 按 code 缓存（首拉 130 bar），预计算直接复用返回 130 bar → build_chart_payload 已按 days 截尾。
- 复现/验证：

    python3 -m backend.main --serve &   # 起服务后
    curl -s 'http://127.0.0.1:8000/api/v1/smart-picker/601086/chart?days=80' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["cached"], len(d["records"]))'   # 期望 True 80
    ls -la reports/smart_picker_charts_latest.json   # 独立文件存在

## P6【观察项 · 自动积累】信号质量卡样本（T+1 起回填）

- 说明：今日 60 条已入库 picker_perf_daily；T+1 起每日回填收益，满 20 样本才显示均值/胜率，此前显示「样本偏少」属设计行为，无需人工操作。
- 复现/验证（每日 15:10 后）：

    sqlite3 data/new_france.db "SELECT signal_date, COUNT(*), SUM(t1_filled) FROM picker_perf_daily GROUP BY signal_date;"
    curl -s 'http://127.0.0.1:8000/api/v1/smart-picker/perf?days=20&window=t1' | python3 -m json.tool | head -20

---

# §9　完整链路验收命令（M0 → M4 → Render 抽查）

    cd /Users/fangcang/new-france

    # ===== M0 git diff 范围自证（铁律：仅授权清单）=====
    git status --short          # 期望：8 个修改 + 5 个新增，无其它
    git diff --name-only | grep -v -E '^(backend/(main.py|db/plugin_models.py)|backend/plugins/(tech_indicators|trend_strength|pattern_scanner|chip_scanner)/router.py|frontend/js/plugins/research_plugins.js|scripts/run_daily.sh)$' && echo 'M0 FAIL：存在越界改动' || echo 'M0 OK：tracked 改动全部在授权清单内'

    # ===== M1 单测（hub + 受影响插件）=====
    python3 -m pytest backend/plugins/smart_picker_hub backend/plugins/tech_indicators backend/plugins/trend_strength backend/plugins/pattern_scanner backend/plugins/chip_scanner -q
    # 期望：51 passed

    # ===== M2 无前视专项（t+5 bar 存在但未到期 → 不填）=====
    python3 -m pytest backend/plugins/smart_picker_hub/tests/test_service.py -k no_lookahead -v
    # 期望：test_refresh_perf_no_lookahead PASSED

    # ===== M3 接口冒烟 =====
    python3 -m backend.main --serve &   # 或另开终端
    curl -s 'http://127.0.0.1:8000/api/v1/smart-picker/latest' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["date"], d["total"], d["returned"])'
    curl -s 'http://127.0.0.1:8000/api/v1/smart-picker/latest?pool=resonance&limit=10' | python3 -c 'import json,sys; d=json.load(sys.stdin); print([(i["code"],i["hit_strategies"]) for i in d["items"]])'
    curl -s -o /dev/null -w 'invalid sort => %{http_code}
' 'http://127.0.0.1:8000/api/v1/smart-picker/latest?sort=bogus'   # 期望 422
    curl -s 'http://127.0.0.1:8000/api/v1/smart-picker/601086' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], (d["item"] or {}).get("hit_strategies"))'
    curl -s 'http://127.0.0.1:8000/api/v1/smart-picker/601086/chart?days=80' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["cached"], len(d["records"]), sorted(d["series"]))'
    curl -s 'http://127.0.0.1:8000/api/v1/smart-picker/perf?days=20&window=t1' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["sample_counts"])'
    curl -s 'http://127.0.0.1:8000/api/v1/smart-picker/meta' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["version"], d["data_age_days"])'
    # 受控改造回归：4 个既有插件页
    for p in tech-indicators trend-strength pattern-scanner chip-scanner; do
      curl -s "http://127.0.0.1:8000/api/v1/$p/latest" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d.get("date"))'
    done

    # ===== M4 压测脚本（10 项断言）=====
    python3 scripts/bench_smart_picker.py
    # 期望：结论 全部硬指标 PASS（P95=0.128ms、重复行 0、数据龄 0、top40 图表全 cached、SQLite 一致）

    # ===== Render 云端抽查（P2/P3 完成后执行）=====
    # 将 <RENDER_WEB_URL> 替换为 Render web 服务域名（blueprint 服务名 new-france-api，形如 https://new-france-api.onrender.com）
    curl -s 'https://<RENDER_WEB_URL>/api/v1/smart-picker/latest' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["date"], d.get("source"), d.get("total"))'
    # 期望：status=completed、date=当日、source=snapshot（raw 兜底）、total>0
    curl -s -o /dev/null -w 'frontend => %{http_code}
' 'https://<RENDER_WEB_URL>/'

---

## 结论速览

- **代码侧已全部完成并实测通过**：M1（51 passed）/ M2（无前视）/ M3（15 项接口冒烟）/ M4（bench 10 项 PASS）均绿。
- **待办仅 3 项、全部在部署侧**：P1 改 crontab 一行（授权后我 30 秒内可完成）→ P2 核验后 commit+push main → P3 跑 commit_screening_data.sh。P1/P2/P3 任一未做，明天的自动化与云端页面都不会有新功能/新数据。
