# Codex 执行任务：策略配置页 — 邮件配置 + 定时任务改列表（弹窗编辑）

> 本文件是给 Codex 的一次性执行指令。请**严格按范围执行**，不要顺手重构无关代码。
> 项目根目录：`/Users/fangcang/new-france`。所有路径均相对该根目录。
> 语言：所有新增注释、文档、提交信息用**中文**；**禁止 emoji**。

---

## 0. 背景与范围边界（先读，避免做多）

策略配置页当前有 5 个面板：策略参数、通知配置、定时任务、因子权重配置、上次修改比对。

**明确不改（已核实功能完备，动它=引入风险）**：
- **策略参数**、**因子权重** 两个面板保持现有表单不变。它们已经是动态生效的：
  - 策略参数：前端 PUT `/config/strategy` → `system_configs` 表 → `resolve_screening_params()`（`backend/services/runtime_config.py:326`）→ 手动筛选/定时任务过滤候选（`backend/services/screening_service.py:407-425`）。
  - 因子权重：`get_factor_weights_decimal()`（`runtime_config.py:347`）→ `SignalEngineAgent` 注入权重（`backend/agents/layer2_signal_engine/agent.py:20-27`）→ 评分 `weighted_sum += r.score * r.weight`（`.../scoring.py:45-56`）。
  - **不要**给这两个面板加列表/弹窗/删除按钮。

**本次只做两件事**：
1. **邮件配置** 改为"列表只读 + 弹窗增删改"，并让后端支持**多收件人**。
2. **定时任务** 改为"只读执行历史列表"，后端补执行历史记录。

**关键决策（已定，直接照做）**：
- 邮件转发方式 = **系统发送时一次性发给默认箱 + 所有已配置收件箱**，不依赖任何邮箱端自动转发。
- 收件箱列表存 `system_configs` 表的 `notification.recipients`（JSON 字符串数组）。
- 列表**只展示**；新增/编辑/删除全部走**弹窗**，弹窗内编辑完有【确认修改】【取消】。
- 邮件列表**每行 = 一个收件箱**。发件邮箱/SMTP 主机/端口是全局固定值，弹窗内**发件邮箱只读不可改**。
- 默认箱 `896256756@qq.com`（即发件人）在列表里**固定占首行，不可删除、不可编辑**；用户新增的其它收件箱 ≤9 个，合计上限 **10**。
- 定时任务列表**只读**（执行时间 / 执行状态 / 执行人），**无**增删改弹窗，保留"手动触发执行"按钮。执行历史存 `reports/task_history.json`（随 data-snapshots 快照持久化，读时可回退 GitHub raw）。

**前端同步铁律**：任何对 `frontend/index.html`、`frontend/js/app.js` 的改动，必须**同步到** `docs/index.html`、`docs/js/app.js`（项目用 docs/ 作为发布镜像）。改完两边内容需一致。

---

## Part A — 后端：邮件多收件人

### A1. `backend/services/runtime_config.py` 增加 `recipients`

1. `_default_config()` 的 `notification` 字典（约 108-114 行）新增一项：
   ```python
   "recipients": [],
   ```
2. `validate_config()` 内 `normalized_notify`（约 247-253 行）新增 `recipients` 的校验，规则：
   - 入参可能是 list 或缺省；对每个元素调用 `_validate_email(item, "收件邮箱")`。
   - 去掉空串、**去重但保序**。
   - 合计数量（**含默认箱**）不得超过 10：即 `len(去重后 recipients ∪ {emailUser})` > 10 时 `raise ConfigValidationError("收件箱数量不能超过 10 个")`。
   - 结果写入 `normalized_notify["recipients"]`。
3. 现有"启用邮件必须有收件人"校验（约 254-255 行）放宽为：`emailEnabled` 且 `emailTo` 与 `recipients` 与 `emailUser` **全部为空** 才报错。
4. `return` 的 `notification`（约 268 行）带上 `recipients`。
5. `sync_strategy_params_file()`（约 402-499 行）：把 `recipients` 写进生成的 `NOTIFY_CONFIG`，新增键 `email_recipients`（Python list 字面量），保证 CLI/cron 读得到。

### A2. `backend/agents/layer3_recommendation/notifier.py` 多人发送

1. `_get_notify_config()`（约 67-88 行）：
   - 默认 config 里加 `"email_recipients": list(NOTIFY_CONFIG.get("email_recipients", []) or [])`。
   - runtime 覆盖块里加 `"email_recipients": list(runtime.get("recipients") or config["email_recipients"])`。
2. 新增模块级 helper：
   ```python
   def _resolve_recipient_list(config: dict) -> list[str]:
       """默认箱(发件人/emailTo) + 配置的 recipients，去重保序过滤空。"""
       primary = config.get("email_to") or config.get("email_user") or ""
       ordered = []
       for addr in [primary, *(config.get("email_recipients") or [])]:
           addr = (addr or "").strip()
           if addr and addr not in ordered:
               ordered.append(addr)
       return ordered
   ```
3. `_send_via_brevo()`（约 817-850 行）：
   - `recipients = _resolve_recipient_list(notify_config)`；若空返回 `(False, "无有效收件人")`。
   - payload `"to"` 改为 `[{"email": e} for e in recipients]`。
   - 成功日志改为展示 `len(recipients)` 个收件人。
4. `_send_via_smtp()`（约 853-900 行）：
   - `recipients = _resolve_recipient_list(notify_config)`；空则 `(False, "收件邮箱未配置")`。
   - `msg["To"] = ", ".join(recipients)`。
   - 发送改为 `server.send_message(msg, from_addr=user, to_addrs=recipients)`（两个分支 587/25 与 SSL 都要传 `to_addrs`）。
   - 日志展示收件人数量。
5. **不要**改动 `backend/plugins/principal_capital/notifier.py` 与 overnight 插件的邮件（本次范围外，保持单值 `SMTP_TO`）。

### A3. 后端单测（新增，放 `tests/`）
- `_resolve_recipient_list`：默认箱恒在首位、去重、过滤空、顺序稳定。
- `validate_config`：11 个收件人（含默认）报错；非法邮箱报错；≤9 个正常返回且含 recipients。

---

## Part B — 前端：邮件配置列表 + 弹窗

参考现有 modal 实现风格：`#stockDetailModal`（`frontend/js/app.js:1259-1306`，用 `.modal.open` 类切换显隐）；表格复用监控列表 `.data-table` 样式（`frontend/index.html:183` 一带、`frontend/css/styles.css` 的 `.data-table`）。

### B1. `frontend/index.html`：通知配置面板（第 422-433 行 `<div class="panel">…通知配置…</div>`）

替换该 panel 内部为：
- 顶部只读全局信息区：发件邮箱（固定文本 `896256756@qq.com`）、SMTP 主机、SMTP 端口。保留隐藏/只读的 `data-notify-key="emailUser|emailHost|emailPort"` 输入，供保存 payload 使用。
- 一个 `<table class="data-table">`，`<thead>`：发件邮箱 | 收件邮箱 | SMTP主机 | SMTP端口 | 操作；`<tbody id="recipientTableBody">` 由 JS 渲染。
- 表格下方：`<button id="addRecipientBtn" class="btn" onclick="openRecipientModal(null)">新增收件箱</button>`（列表满 10 时置灰）。
- 保留 `<button class="btn" onclick="testEmail(event)">发送测试邮件</button>` 和 `<div id="emailStatus" class="form-status">`。
- 删除原来独立的收件邮箱输入行（收件人改由列表管理）。

在 `#page-settings` 之外、`</main>` 之后新增弹窗（仿 stockDetailModal 结构）：
```html
<div id="recipientModal" class="modal">
  <div class="modal-backdrop" onclick="closeRecipientModal()"></div>
  <div class="modal-card">
    <h3 id="recipientModalTitle">新增收件箱</h3>
    <div class="form-row"><label>发件邮箱</label><input id="rcpUser" class="input" readonly></div>
    <div class="form-row"><label>收件邮箱</label><input id="rcpTo" class="input" type="email"></div>
    <div class="form-row"><label>SMTP主机</label><input id="rcpHost" class="input"></div>
    <div class="form-row"><label>SMTP端口</label><input id="rcpPort" class="input input-sm" type="number"></div>
    <div id="recipientModalError" class="form-status"></div>
    <div class="modal-actions">
      <button class="btn" onclick="closeRecipientModal()">取消</button>
      <button class="btn btn-primary" onclick="confirmRecipientModal()">确认修改</button>
    </div>
  </div>
</div>
```
（若 `.modal-card`/`.modal-actions` 样式不存在，在 `styles.css` 补最小样式，风格对齐现有 modal。）

### B2. `frontend/js/app.js`

1. `renderSettingsForms()`（约 2388-2395 行）中 `notificationForm` 分支：
   - 只读全局字段照旧用 `data-notify-key` 回填。
   - 调用新增的 `renderRecipientList()`。
2. 新增函数：
   - `renderRecipientList()`：读 `notificationDraft.recipients`（数组）。首行为默认箱 = `notificationDraft.emailUser`（操作列显示"默认"占位，无编辑/删除按钮）。其余每行【编辑】`onclick="openRecipientModal(i)"`、【删除】`onclick="deleteRecipient(i)"`。发件邮箱/主机/端口列都展示全局固定值。渲染完根据 `recipients.length+1>=10` 置灰 `#addRecipientBtn`。
   - `openRecipientModal(index)`：index=null 为新增；否则回填该 recipient。发件邮箱框固定为 `notificationDraft.emailUser` 且 `readonly`。记录当前编辑 index 到模块变量。加 `.open`。
   - `confirmRecipientModal()`：校验邮箱格式、非空、与现有（含默认箱）不重复、合计 ≤10；通过则写入 `notificationDraft.recipients`（新增 push / 编辑替换），关弹窗，调用 `saveNotificationConfig(event)` 持久化，再 `renderRecipientList()`。校验失败在 `#recipientModalError` 提示，不关弹窗。
   - `closeRecipientModal()`：移除 `.open`，清错误。
   - `deleteRecipient(index)`：从数组删除后 `saveNotificationConfig` 并重渲染。默认箱不走此函数。
3. `collectNotificationConfig()`（约 2447-2453 行）：payload 里带上 `recipients: [...notificationDraft.recipients]`。
4. `applyRuntimeConfig()`（约 2343-2351 行）：确保 `notificationDraft.recipients` 从 `config.notification.recipients` 初始化（缺省 `[]`）。
5. `renderSettingsCompare()`（约 2541 行"收件邮箱"行）：改为展示收件人数量（如 `3 个`）或列表摘要。
6. 同步到 `docs/js/app.js` 与 `docs/index.html`。

---

## Part C — 定时任务：执行历史列表

### C1. 新增 `backend/services/task_history.py`
仿 `backend/plugins/principal_capital/service.py` 的读写 + 远程回退风格：
- 常量：`REPORT_DIR = PROJECT_DIR/"reports"`；`TASK_HISTORY_FILE = REPORT_DIR/"task_history.json"`；`BEIJING_TZ = timezone(timedelta(hours=8))`；`SNAPSHOT_RAW_BASE`（复用与 principal_capital 相同的默认值 `https://raw.githubusercontent.com/caohongbocaohongbo/new-france/data-snapshots`，允许 env `TASK_SNAPSHOT_RAW_BASE` 覆盖）。
- `append_task_record(status: str, operator: str, run_time: datetime|None=None, error: str|None=None, max_records=500)`：
  - `run_time` 缺省 `datetime.now(BEIJING_TZ)`；记录字段 `run_time`(ISO)、`run_time_display`(`YYYY/MM/DD HH:MM:SS`)、`status`、`operator`、`error`。
  - 读现有 → append → 截断 max_records → 写回。
- `read_task_history() -> dict`：本地文件，缺失/损坏返回 `{"records": []}`。
- `_fetch_snapshot_json(filename)`（延迟 import requests，timeout 8，异常返回 None）。
- `read_task_history_resilient() -> dict`：本地有 records 直接返回；否则回退 `data-snapshots/reports/task_history.json`；再失败返回本地。

### C2. 写入点
- `backend/main.py` 每日流水线 `_run_daily_pipeline`（约 376-404 行 `run_full_pipeline` 调用处）：用 try/except 包裹，成功后 `append_task_record("success", "admin(cron)")`，异常时 `append_task_record("failed", "admin(cron)", error=str(exc))` 并继续抛出/记录。
- 手动触发：`backend/api/router_screening.py` 的后台任务 `_run_screening_task`（找到执行 pipeline 的函数）成功/失败时 `append_task_record("success"/"failed", "admin", error=...)`。

### C3. 接口 + 快照
- `backend/api/router_system.py` 新增：
  ```python
  @router.get("/task-history")
  async def task_history(limit: int = Query(50, ge=1, le=500)):
      from ..services.task_history import read_task_history_resilient
      records = (read_task_history_resilient().get("records") or [])[-limit:]
      return {"status": "ok", "records": list(reversed(records))}
  ```
  （倒序：最新在前。）
- `scripts/restore_screening_data.sh`：在 restore 列表追加 `restore_file "reports/task_history.json"`。
- `scripts/commit_screening_data.sh` 已整目录复制 `reports/`，无需改（确认它 `cp -R reports/.` 覆盖了新文件即可）。

### C4. 前端定时任务面板（`frontend/index.html:434-448`）
- 保留 cron 只读展示 + `下次执行` + "手动触发执行"按钮 + `#runStatus`。
- 下方加 `<table class="data-table">`：执行时间 | 执行状态 | 执行人；`<tbody id="taskHistoryBody">`。
- `frontend/js/app.js`：
  - 新增 `loadTaskHistory()`：`GET /system/task-history`，渲染到 `#taskHistoryBody`；状态用颜色区分（success 绿 / failed 红 / running 灰）。
  - 进入设置页时调用（找到设置页激活/`navigateTo('settings')` 的加载点，与 `loadRuntimeConfig()` 并列调用）。
  - `manualRun()` 成功后调用 `loadTaskHistory()` 刷新。
- 同步 docs/。

---

## 涉及文件清单（Codex 改动范围白名单）

后端：
- `backend/services/runtime_config.py`
- `backend/agents/layer3_recommendation/notifier.py`
- `backend/services/task_history.py`（新建）
- `backend/main.py`
- `backend/api/router_screening.py`
- `backend/api/router_system.py`
- `scripts/restore_screening_data.sh`
- `tests/`（新增单测）

前端（frontend + docs 双份）：
- `frontend/index.html` / `docs/index.html`
- `frontend/js/app.js` / `docs/js/app.js`
- `frontend/css/styles.css` / `docs/css/styles.css`（仅在缺 modal 样式时补）

**不得改动**：策略参数面板、因子权重面板相关逻辑；principal_capital / overnight 插件；render.yaml；GitHub workflow（除非验证发现 restore 脚本遗漏）。

---

## 验证清单（Codex 执行完必须自查）

1. 语法：`python -m py_compile` 所有改动的 .py；`bash -n scripts/restore_screening_data.sh`。
2. 后端单测：`_resolve_recipient_list` 与 `validate_config` recipients 用例全绿。
3. 配置校验：`PUT /config/strategy` 传 11 个 recipients → HTTP 400；非法邮箱 → 400；≤9 个正常保存并可回读 `GET /config/strategy` 含 recipients。
4. 发送：`POST /system/test-email` 日志显示多收件人；默认箱 `896256756@qq.com` 恒在首位。
5. 执行历史：手动 `POST /screening/run` 后 `GET /system/task-history` 出现 `operator=admin` 记录；cron 路径产生 `admin(cron)`。
6. 前端：设置页通知配置为列表；新增/编辑/删除弹窗（确认/取消）正常；默认箱首行无操作按钮；满 10 个禁用新增；定时任务列表展示历史。
7. `frontend/` 与 `docs/` 对应文件内容一致（`diff` 应为空或仅路径差异）。
8. 回归：策略参数、因子权重面板保存仍正常，未被破坏。

## 提交（如需）
- 分支名：`feat/settings-email-recipients-and-task-history`
- commit message 用中文，结尾附：
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
