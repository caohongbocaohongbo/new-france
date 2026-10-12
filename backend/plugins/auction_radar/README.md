# 低位竞价雷达（插件 28）本地部署与演练

P0 为本地单节点预览期运行；只做观察/筛选/查询/通知，不自动下单、不承诺收益。

## CLI

```bash
cd /Users/fangcang/new-france
python3 -m backend.main --run-auction-radar-once     # 单轮（dry-run 默认 True）
python3 -m backend.main --run-auction-radar-daemon   # 本地常驻
python3 -m backend.main --run-auction-radar-probe    # 竞价源采样
python3 -m backend.main --run-auction-radar-review   # 盘后结果标签回填
```

## 环境变量（RADAR_AUCTION_*）

见 `docs/27_低位竞价雷达_施工方案_V1.8.md` §13 建议配置。关键项：

- `RADAR_AUCTION_POOL_MAX=400`
- `RADAR_AUCTION_BASE_POLL_S=5`
- `RADAR_AUCTION_FINAL_FAST_DEADLINE_S=15`
- `RADAR_AUCTION_FINAL_CONFIRM_DEADLINE_S=60`
- `RADAR_AUCTION_OUTBOX_ENABLED=0`（预览期默认关闭）
- `RADAR_AUCTION_PREVIEW_NOTIFY_ENABLED=1`（预览期发观察期预览）
- `RADAR_AUCTION_STRATEGY_VERSION=probe_only`
- `RADAR_AUCTION_BRANCH=UNVERIFIED`

## launchd 常驻模板

保存为 `~/Library/LaunchAgents/com.new-france.auction-radar.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.new-france.auction-radar</string>
<key>ProgramArguments</key><array>
<string>/usr/bin/python3</string><string>-m</string><string>backend.main</string>
<string>--run-auction-radar-daemon</string>
</array>
<key>WorkingDirectory</key><string>/Users/fangcang/new-france</string>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>/Users/fangcang/new-france/logs/auction_radar.log</string>
<key>StandardErrorPath</key><string>/Users/fangcang/new-france/logs/auction_radar.log</string>
</dict></plist>
```

## 故障恢复

- 启动时扫描 `PENDING_PUBLISH / WAITING_PUBLISH` 补完或标失败。
- `SENDING` 超时任务由 `recover_incomplete_publications()` 重置为 `READY`。
- 9:25 后启动无法补最终数据时不回放旧预测当最终。

## 验收入口

- 纯函数单测：`python3 -m pytest tests/test_auction_radar.py -v`
- 快照：`reports/auction_radar_latest.json`
- run 报告：`auction_runs.milestones_json`（M0–M5 六里程碑）
