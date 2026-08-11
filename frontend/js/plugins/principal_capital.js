/**
 * 主力资金双向监控（plugin 独立 JS 模块）
 *
 * 此文件不依赖 app.js 的内部状态，仅复用 apiFetch / setInlineStatus 两个全局工具函数。
 * 整体可迁移到新项目：拷贝此文件 + index.html 的 #page-principal-capital 一段即可。
 */

// 插件命名空间（避免与 app.js 全局变量冲突）
window.PrincipalCapital = window.PrincipalCapital || {
    state: { initialized: false, lastReport: null },
};

// 业务接口前缀（不含 /api/v1，由 apiFetch 或 _pcResolveApiBase 拼接）
const PC_PATH = '/principal-capital';

/* ============================================================
 * 工具函数（不污染 window）
 * ============================================================ */

// 解析 API base：local / localhost / file:// 都强制走本地后端，避免落到 Render
function _pcResolveApiBase() {
    if (typeof location !== 'undefined') {
        if (location.protocol === 'file:' ||
            location.hostname === 'localhost' ||
            location.hostname === '127.0.0.1') {
            return 'http://localhost:8000/api/v1';
        }
    }
    if (typeof API_BASE === 'string' && API_BASE) return API_BASE;
    return '/api/v1';
}

// _pcFetch: 走绝对 URL，避免与主项目 apiFetch 的 API_BASE 双重拼接 /api/v1
function _pcFetch(path, opts = {}) {
    const url = _pcResolveApiBase() + PC_PATH + path;
    const ms = opts.timeout || 30000;
    const o = { ...opts };
    delete o.timeout; delete o.retries;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);
    return fetch(url, { ...o, signal: controller.signal })
        .finally(() => clearTimeout(timer));
}

function _pcSetStatus(text, isError = false) {
    const el = document.getElementById('pcStatus');
    if (!el) return;
    el.textContent = text || '';
    el.style.color = isError ? '#dc2626' : '#6b7280';
}

function _pcFmtPct(v) {
    if (v === null || v === undefined || v === '') return '--';
    const n = Number(v);
    if (!Number.isFinite(n)) return '--';
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
}

function _pcFmtYi(v) {
    if (v === null || v === undefined || v === '') return '--';
    const n = Number(v);
    if (!Number.isFinite(n)) return '--';
    return (n / 1e8).toFixed(2) + '亿';
}

function _pcEscape(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function _pcFmtTime(iso) {
    if (!iso) return '--';
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return String(iso);
        const pad = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    } catch (e) {
        return String(iso);
    }
}

const PC_STATUS_LABEL = {
    completed: '已扫描',
    no_data: '数据源失败/无数据',
    skipped: '非交易时段',
    error: '运行异常',
    empty: '未运行',
    running: '运行中...',
};

const PC_SOURCE_LABEL = {
    eastmoney: '东方财富(主)',
    eastmoney_backup: '东方财富(备)',
    akshare: 'akshare',
    cache: '本地缓存(降级)',
    none: '全部不可用',
};

/* ============================================================
 * 页面入口（供 app.js 路由调用）
 * ============================================================ */

function setupPrincipalCapitalPage() {
    _pcInjectStyles();
    if (!PrincipalCapital.state.initialized) {
        PrincipalCapital.state.initialized = true;
    }
    pcLoadLatest();
    pcLoadSourceHealth();
}

/* ============================================================
 * 插件自带样式（首次进入页面注入到 <head>，迁移时零外部 CSS 依赖）
 * ============================================================ */
function _pcInjectStyles() {
    if (document.getElementById('principal-capital-styles')) return;
    const css = `
/* 主力资金顶部三栏:左控制 / 中买入 / 右卖出 */
#page-principal-capital .pc-top-grid {
    display: grid;
    grid-template-columns: 280px minmax(0, 1fr) minmax(0, 1fr);
    gap: var(--sp-base, 16px);
    margin-bottom: var(--sp-base, 16px);
    align-items: stretch;
}
/* 中等屏幕:控制区独占一行,买入卖出两栏并列 */
@media (max-width: 1280px) {
    #page-principal-capital .pc-top-grid {
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    }
    #page-principal-capital .pc-control-panel {
        grid-column: 1 / -1;
    }
    #page-principal-capital .pc-controls-stack {
        flex-direction: row;
        flex-wrap: wrap;
        align-items: flex-end;
        gap: 12px;
    }
    #page-principal-capital .pc-controls-stack .btn-full {
        flex: 0 0 auto;
        width: auto;
    }
}
/* 窄屏:全部单列堆叠 */
@media (max-width: 720px) {
    #page-principal-capital .pc-top-grid {
        grid-template-columns: 1fr;
    }
}

/* 三栏 panel 等高 + 子内容能伸展 */
#page-principal-capital .pc-control-panel,
#page-principal-capital .pc-signal-panel {
    display: flex;
    flex-direction: column;
    min-width: 0;
}

/* 左:控制区头部 (无 h2 标题, 仅显示 meta 信息) */
#page-principal-capital .pc-control-header {
    padding: 12px 16px;
    min-height: 0;
}
#page-principal-capital .pc-meta {
    color: #949999;
    font-size: 12px;
    line-height: 1.5;
    margin: 0;
}

/* 左:控制区表单堆叠 */
#page-principal-capital .pc-controls-stack {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 16px;
}
#page-principal-capital .pc-checkbox-row label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--body, #202124);
    cursor: pointer;
}
#page-principal-capital .pc-controls-stack .input-sm {
    width: 100%;
}
#page-principal-capital .pc-controls-stack .filter-group label {
    display: block;
    font-size: 12px;
    color: var(--muted, #6b7280);
    margin-bottom: 4px;
}
#page-principal-capital .pc-controls-stack .pc-checkbox-row label {
    margin-bottom: 0;
}

/* 信号头部色调 */
#page-principal-capital .pc-buy-header { background: #fef2f2; }
#page-principal-capital .pc-buy-header h2 { color: #b91c1c; }
#page-principal-capital .pc-sell-header { background: #eff6ff; }
#page-principal-capital .pc-sell-header h2 { color: #1d4ed8; }

/* 信号 panel 表格滚动 shell:固定最小高度,撑开整个 panel */
#page-principal-capital .pc-table-shell {
    flex: 1 1 auto;
    min-height: 300px;
    max-height: 520px;
    min-width: 0;
    display: flex;
    flex-direction: column;
}
#page-principal-capital .pc-table-shell .table-scroll {
    flex: 1;
    overflow: auto;
}

/* 信号表格 min-width:确保窄屏触发横向滚动,左右按钮才有意义 */
#page-principal-capital .pc-table-shell .data-table {
    min-width: 520px;
}
#page-principal-capital .pc-table-shell .empty-state {
    padding: 40px 16px;
    text-align: center;
    color: var(--muted-soft, #9ca3af);
}

/* 数据源状态:全宽 panel,与上方间距 */
#page-principal-capital .pc-source-panel {
    margin-top: 0;
}
#page-principal-capital .pc-source-body {
    padding: 12px 16px 16px;
}
#page-principal-capital .pc-source-body .data-table {
    min-width: 320px;
}

/* 控制区按钮样式与"手动筛选-执行筛选"按钮对齐(蓝紫色主按钮 + 47px 高度 + 16px 字号) */
#page-principal-capital .pc-control-panel .btn-full {
    height: 47px;
    font-size: 16px;
    width: 100%;
    margin: 0;
}
#page-principal-capital .pc-control-panel .btn-primary {
    background: var(--primary, #2932e1);
    border-color: var(--primary, #2932e1);
    color: var(--on-primary, #fff);
}
#page-principal-capital .pc-control-panel .btn-primary:hover {
    background: var(--primary-active, #1e27b5);
    border-color: var(--primary-active, #1e27b5);
}
`;
    const style = document.createElement('style');
    style.id = 'principal-capital-styles';
    style.textContent = css;
    document.head.appendChild(style);
}

/* ============================================================
 * 表格横向滚动(响应监控列表"‹/›"按钮交互)
 * ============================================================ */
function scrollPcBuyTable(direction) {
    _pcScrollById('pcBuyTableScroll', direction);
}
function scrollPcSellTable(direction) {
    _pcScrollById('pcSellTableScroll', direction);
}
function _pcScrollById(id, direction) {
    const scroller = document.getElementById(id);
    if (!scroller) return;
    const distance = Math.max(200, Math.round(scroller.clientWidth * 0.75));
    scroller.scrollBy({ left: direction < 0 ? -distance : distance, behavior: 'smooth' });
}

/* ============================================================
 * API 调用
 * ============================================================ */

async function pcTriggerScan() {
    const btn = document.getElementById('pcRunBtn');
    if (btn) btn.disabled = true;
    _pcSetStatus('扫描中...');
    try {
        const buy = document.getElementById('pcBuyThreshold').value || 50;
        const sell = document.getElementById('pcSellThreshold').value || 30;
        const excludeStar = document.getElementById('pcExcludeStar').checked;
        const enableVerify = document.getElementById('pcEnableVerify').checked;
        const force = document.getElementById('pcForce').checked;
        const params = new URLSearchParams({
            buy_threshold: buy,
            sell_threshold: sell,
            exclude_star: excludeStar,
            force: force,
            enable_verify: enableVerify,
            dry_run: false,
        });
        const resp = await _pcFetch('/trigger?' + params.toString(), { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'started') {
            _pcSetStatus('任务已在后台启动，3 秒后刷新结果');
            setTimeout(() => pcLoadLatest(), 3500);
        } else {
            _pcSetStatus('启动失败: ' + (data.error || '未知错误'), true);
        }
    } catch (err) {
        _pcSetStatus('扫描调用失败: ' + err.message, true);
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function pcLoadLatest() {
    try {
        const resp = await _pcFetch('/latest');
        const data = await resp.json();
        PrincipalCapital.state.lastReport = data;
        _pcRenderReport(data);
    } catch (err) {
        _pcSetStatus('加载结果失败: ' + err.message, true);
    }
}

async function pcLoadSourceHealth() {
    try {
        const resp = await _pcFetch('/source-health');
        const data = await resp.json();
        _pcRenderSourceHealth(data);
    } catch (err) {
        const el = document.getElementById('pcSourceHealth');
        if (el) el.innerHTML = '<p class="empty-state" style="color:#dc2626">加载失败: ' + _pcEscape(err.message) + '</p>';
    }
}

/* ============================================================
 * 渲染函数
 * ============================================================ */

function _pcRenderReport(data) {
    const meta = document.getElementById('pcMeta');
    if (data.status === 'empty') {
        if (meta) meta.textContent = '主力资金监控尚未产生快照';
        _pcRenderTable('pcBuyTable', []);
        _pcRenderTable('pcSellTable', [], true);
        document.getElementById('pcBuyCount').textContent = '0';
        document.getElementById('pcSellCount').textContent = '0';
        return;
    }
    if (data.status === 'running') {
        if (meta) meta.textContent = '扫描任务进行中...';
        return;
    }
    const buy = data.buy_triggered || [];
    const sell = data.sell_triggered || [];
    const stale = data.source_status && data.source_status.is_stale;
    const srcRaw = (data.source_status && data.source_status.active_source) || 'none';
    const srcLabel = (PC_SOURCE_LABEL[srcRaw] || srcRaw) + (stale ? ' (缓存降级)' : '');
    const statusLabel = PC_STATUS_LABEL[data.status] || data.status || '--';
    if (meta) {
        meta.textContent = `${_pcFmtTime(data.now)} · 扫描 ${data.scanned || 0} 只 · 数据源: ${srcLabel} · 状态: ${statusLabel}`;
    }
    document.getElementById('pcBuyCount').textContent = String(buy.length);
    document.getElementById('pcSellCount').textContent = String(sell.length);
    _pcRenderTable('pcBuyTable', buy);
    _pcRenderTable('pcSellTable', sell, true);

    // 状态行差异化提示
    if (data.status === 'no_data') {
        _pcSetStatus('数据源失败/无数据（详见底部数据源状态）', true);
    } else if (data.status === 'error') {
        _pcSetStatus(`运行异常：${data.reason || data.error || '未知错误'}`, true);
    } else if (data.status === 'skipped') {
        _pcSetStatus(`非交易时段：${data.reason || '本轮已跳过'}`);
    } else if (buy.length === 0 && sell.length === 0) {
        _pcSetStatus(`已扫描·暂无信号（共 ${data.scanned || 0} 只）`);
    } else {
        _pcSetStatus(`已扫描：买 ${buy.length} 卖 ${sell.length}${data.email_sent ? '（已邮件）' : ''}`);
    }
}

function _pcRenderTable(containerId, rows, isSell = false) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!rows || rows.length === 0) {
        container.innerHTML = '<p class="empty-state">暂无信号</p>';
        return;
    }
    const headerExtra = isSell ? '<th>严重度</th>' : '';
    const headers = `<thead><tr><th>代码</th><th>名称</th><th>时间</th><th style="text-align:right">占比</th><th style="text-align:right">主力净流入</th><th style="text-align:right">成交额</th><th style="text-align:right">涨幅</th>${headerExtra}</tr></thead>`;
    const ratioColor = isSell ? '#1d4ed8' : '#dc2626';
    const bodyRows = rows.map(r => {
        const sev = r.severity || '';
        const sevStyleMap = {
            danger: 'color:#0f172a;font-weight:700',
            alert:  'color:#1d4ed8',
            warn:   'color:#475569',
        };
        const sevStyle = isSell ? (sevStyleMap[sev] || '') : '';
        const sevCell = isSell ? `<td style="${sevStyle}">${_pcEscape(sev || '--')}</td>` : '';
        return `<tr>
            <td>${_pcEscape(r.code || '')}</td>
            <td>${_pcEscape(r.name || '')}</td>
            <td style="white-space:nowrap">${_pcEscape(_pcFmtTime(r.flow_time))}</td>
            <td style="text-align:right;color:${ratioColor};font-weight:600">${_pcFmtPct(r.main_inflow_ratio)}</td>
            <td style="text-align:right">${_pcFmtYi(r.main_net_inflow)}</td>
            <td style="text-align:right">${_pcFmtYi(r.total_amount)}</td>
            <td style="text-align:right">${_pcFmtPct(r.change_pct)}</td>
            ${sevCell}
        </tr>`;
    }).join('');
    container.innerHTML = `<table class="data-table">${headers}<tbody>${bodyRows}</tbody></table>`;
}

function _pcRenderSourceHealth(data) {
    const el = document.getElementById('pcSourceHealth');
    if (!el) return;
    const sources = ['eastmoney_push2', 'eastmoney_push2his', 'akshare'];
    const hasAnyKnown = data && sources.some(k => data[k] && typeof data[k] === 'object');
    if (!hasAnyKnown) {
        // 后端返回 {detail:"Not Found"} / {} / 错误响应 时，避免误显示"正常"
        el.innerHTML = '<p class="empty-state">尚未有数据源健康记录</p>';
        return;
    }
    const rows = sources.map(name => {
        const item = data[name] || {};
        const fs = item.failure_streak || 0;
        const blocked = item.blocked_until;
        let statusHtml;
        if (blocked) {
            statusHtml = `<span style="color:#dc2626">熔断到 ${_pcEscape(blocked)}</span>`;
        } else if (fs > 0) {
            statusHtml = `<span style="color:#d97706">失败 ${fs} 次</span>`;
        } else {
            statusHtml = `<span style="color:#059669">正常</span>`;
        }
        return `<tr><td>${_pcEscape(name)}</td><td>${statusHtml}</td></tr>`;
    }).join('');
    const updated = data.updated_at
        ? `<div class="table-note" style="margin-top:8px">更新时间: ${_pcEscape(data.updated_at)}</div>`
        : '';
    el.innerHTML = `<table class="data-table"><thead><tr><th>数据源</th><th>状态</th></tr></thead><tbody>${rows}</tbody></table>${updated}`;
}
