# 主力资金监控可靠性修复实施计划

> **面向代理执行者：** 按任务逐项实施，每项先写失败测试，再做最小实现，并在任务完成后独立提交。

**目标：** 修复主力资金健康状态快照缺失、远程状态被隐藏、旧提交静默空跑和任务无告警问题，同时保留 GitHub Actions 扫描、Render 只读快照的部署边界。

**架构：** 全市场扫描继续只在 GitHub Actions 执行；扫描结果和健康状态写入 `data-snapshots` 分支，Render API 仅远程读取。新增独立看门狗读取快照并发送一次性故障邮件，不触发任何生产扫描。

**技术栈：** Python、pytest、GitHub Actions、Shell、原生 JavaScript。

---

### 任务 1：健康文件进入数据快照

**文件：**
- 修改：`scripts/commit_screening_data.sh`
- 修改：`tests/test_github_workflows.py`

- [ ] 增加失败测试，断言提交脚本的复制、清理和暂存清单均包含 `data/principal_capital_source_health.json`。
- [ ] 运行目标测试，确认因文件缺失而失败。
- [ ] 在三处文件清单加入主力资金健康文件。
- [ ] 运行目标测试并提交。

### 任务 2：展示远程真实状态

**文件：**
- 修改：`backend/plugins/principal_capital/service.py`
- 修改：`backend/plugins/principal_capital/tests/test_service.py`
- 修改：`frontend/js/plugins/principal_capital.js`
- 修改：`docs/js/plugins/principal_capital.js`

- [ ] 增加失败测试，覆盖远程 `no_data`、`skipped`、`error` 状态和稳定字段。
- [ ] 修改 `read_report_resilient()`，接受所有带 `status` 的远程快照，并补齐稳定字段。
- [ ] 补充前端状态文案：数据源失败、非交易时段、已扫描暂无信号、未运行。
- [ ] 运行后端和前端镜像测试并提交。

### 任务 3：旧提交跳过可见化

**文件：**
- 修改：`.github/workflows/principal-capital-scan.yml`
- 修改：`tests/test_github_workflows.py`

- [ ] 增加失败测试，断言 stale 分支包含 GitHub warning 注解，且原门禁条件保留。
- [ ] 增加 warning 注解。
- [ ] 运行目标测试并提交。

### 任务 4：新增主力资金看门狗

**文件：**
- 新增：`scripts/principal_capital_watchdog.py`
- 新增：`.github/workflows/principal-capital-watchdog.yml`
- 新增：`tests/test_principal_capital_watchdog.py`
- 修改：`tests/test_github_workflows.py`

- [ ] 增加失败测试，覆盖昨日快照、今日 `no_data`、今日 `completed` 和同类告警去重。
- [ ] 实现快照读取、北京时区判定、告警内容、邮件发送和本地去重标记。
- [ ] 增加手动触发加 9:45/13:15 冗余 schedule 的工作流，并注入邮件配置。
- [ ] 运行目标测试并提交。

### 任务 5：留下受限数据源兜底说明

**文件：**
- 修改：`backend/plugins/principal_capital/sources/multi_source.py`

- [ ] 在全市场 fallback 位置增加中文 TODO，明确未来只能使用小票池或候选二次确认，并要求限流。
- [ ] 运行主力资金数据源测试并提交。

### 任务 6：全量验收

- [ ] 运行 `python3 -m pytest`，确认全部测试通过。
- [ ] 检查前端与 `docs/` 镜像一致。
- [ ] 检查每个提交只包含对应任务文件。
- [ ] 汇总未执行的远程分支验收项及原因。
