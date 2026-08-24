# 盘中雷达本地常驻部署

Phase 4 采集器只在国内网络的本地电脑运行，Render 服务不启动常驻进程。

## launchd 模板

保存为 `~/Library/LaunchAgents/com.new-france.smart-money-radar.plist`，按实际项目路径修改后执行 `launchctl load`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.new-france.smart-money-radar</string>
<key>ProgramArguments</key><array><string>/usr/bin/env</string><string>python3</string><string>-m</string><string>backend.main</string><string>--run-radar-daemon</string></array>
<key>WorkingDirectory</key><string>/Users/fangcang/new-france</string>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>Nice</key><integer>10</integer>
<key>StandardOutPath</key><string>/Users/fangcang/new-france/logs/radar.log</string>
<key>StandardErrorPath</key><string>/Users/fangcang/new-france/logs/radar.log</string>
</dict></plist>
```

手动运行：`python3 -m backend.main --run-radar-daemon`。

## 可选 SQLite 回放

设置 `RADAR_ENABLE_SQLITE_DUMP=true` 后，每轮每只股票的指标、阶段和双评分会写入 `data/smart_money_radar.sqlite3`。默认关闭；Render 不启用。代码可通过 `RadarReplay.query(code, start, end)` 按代码和时间范围读取快照。
