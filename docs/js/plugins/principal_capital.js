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

const PC_API_BASE = '/api/v1/principal-capital';

/* ============================================================
 * 工具函数（不污染 window）
 * ============================================================ */

function _pcFetch(path, opts = {}) {
    // 优先使用 app.js 的 apiFetch（自带 timeout）；如果未加载则降级为原生 fetch
    if (typeof apiFetch === 'function') {
        return apiFetch(PC_API_BASE + path, opts);
    }
    return fetch(PC_API_BASE + path, opts);
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

/* ============================================================
 * 页面入口（供 app.js 路由调用）
 * ============================================================ */

function setupPrincipalCapitalPage() {
    if (!PrincipalCapital.state.initialized) {
        PrincipalCapital.state.initialized = true;
    }
    pcLoadLatest();
    pcLoadSourceHealth();
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
        if (meta) meta.textContent = '暂无扫描结果，点击"执行扫描"开始';
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
    if (data.status === 'error') {
        if (meta) meta.textContent = '错误: ' + (data.error || '未知错误');
        return;
    }
    const buy = data.buy_triggered || [];
    const sell = data.sell_triggered || [];
    const stale = data.source_status && data.source_status.is_stale;
    const src = (data.source_status && data.source_status.active_source) || '--';
    if (meta) {
        meta.textContent = `${data.now || ''} | 扫描 ${data.scanned || 0} 只 | 数据源: ${src}${stale ? ' (缓存降级)' : ''} | 状态: ${data.status}`;
        meta.style.color = stale ? '#dc2626' : '#6b7280';
    }
    document.getElementById('pcBuyCount').textContent = String(buy.length);
    document.getElementById('pcSellCount').textContent = String(sell.length);
    _pcRenderTable('pcBuyTable', buy);
    _pcRenderTable('pcSellTable', sell, true);
    _pcSetStatus(`完成: 买${buy.length} 卖${sell.length}${data.email_sent ? ' (已邮件)' : ''}`);
}

function _pcRenderTable(containerId, rows, isSell = false) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!rows || rows.length === 0) {
        container.innerHTML = '<p class="empty-state">暂无信号</p>';
        return;
    }
    const headerColor = isSell ? '#eff6ff' : '#fef2f2';
    const headerExtra = isSell ? '<th>严重度</th>' : '';
    const headers = `<tr style="background:${headerColor}"><th>代码</th><th>名称</th><th>占比</th><th>主力净流入</th><th>成交额</th><th>涨幅</th>${headerExtra}</tr>`;
    const html = rows.map(r => {
        const sev = r.severity || '';
        const sevStyle = isSell ? {
            danger: 'background:#dbeafe;color:#0f172a;font-weight:700',
            alert: 'background:#eff6ff;color:#1d4ed8',
            warn: 'background:#f1f5f9;color:#475569',
        }[sev] || '' : '';
        const sevCell = isSell ? `<td style="${sevStyle}">${_pcEscape(sev || '--')}</td>` : '';
        return `<tr>
            <td>${_pcEscape(r.code || '')}</td>
            <td>${_pcEscape(r.name || '')}</td>
            <td style="text-align:right;color:${isSell ? '#1d4ed8' : '#dc2626'};font-weight:600">${_pcFmtPct(r.main_inflow_ratio)}</td>
            <td style="text-align:right">${_pcFmtYi(r.main_net_inflow)}</td>
            <td style="text-align:right">${_pcFmtYi(r.total_amount)}</td>
            <td style="text-align:right">${_pcFmtPct(r.change_pct)}</td>
            ${sevCell}
        </tr>`;
    }).join('');
    container.innerHTML = `<table style="width:100%;border-collapse:collapse" cellpadding="8" border="1">${headers}${html}</table>`;
}

function _pcRenderSourceHealth(data) {
    const el = document.getElementById('pcSourceHealth');
    if (!el) return;
    if (!data || Object.keys(data).length === 0 || data.sources && Object.keys(data.sources).length === 0) {
        el.innerHTML = '<p class="empty-state">尚未有数据源健康记录</p>';
        return;
    }
    const sources = ['eastmoney_push2', 'eastmoney_push2his', 'akshare'];
    const rows = sources.map(name => {
        const item = data[name] || {};
        const fs = item.failure_streak || 0;
        const blocked = item.blocked_until;
        const status = blocked ? `<span style="color:#dc2626">熔断到 ${_pcEscape(blocked)}</span>` :
                       fs > 0 ? `<span style="color:#d97706">失败 ${fs} 次</span>` :
                       '<span style="color:#059669">正常</span>';
        return `<tr><td>${name}</td><td>${status}</td></tr>`;
    }).join('');
    const updated = data.updated_at ? `<p style="margin-top:8px;color:#6b7280;font-size:12px">更新时间: ${_pcEscape(data.updated_at)}</p>` : '';
    el.innerHTML = `<table style="width:100%;border-collapse:collapse" cellpadding="8" border="1">
        <tr style="background:#f3f4f6"><th>数据源</th><th>状态</th></tr>
        ${rows}
    </table>${updated}`;
}
