/**
 * 隔夜套利插件（独立 JS 模块）
 *
 * 设计原则：本文件不强依赖主项目的全局函数，所有工具函数都做了 fallback。
 * 整体可迁移到新项目：拷贝此文件 + index.html 的 #page-overnight-arbitrage 一段即可。
 */

// 命名空间，避免全局变量冲突
window.OvernightArbitrage = window.OvernightArbitrage || {
    state: { lastDecision: null },
};

/* ============================================================
 * 内部工具函数（自带 fallback，便于插件迁移）
 * ============================================================ */

// API base：优先使用主项目 API_BASE，否则用默认
const _OA_API_BASE = (typeof API_BASE !== 'undefined' && API_BASE)
    ? API_BASE
    : ((typeof location !== 'undefined' &&
        (location.hostname === 'localhost' || location.hostname === '127.0.0.1'))
        ? 'http://localhost:8000/api/v1'
        : '/api/v1');

// apiFetch：优先用主项目实现（带重试和超时），否则自带原生 fetch
const _oaApiFetch = (typeof apiFetch === 'function')
    ? apiFetch
    : async function (path, opts = {}) {
        const ms = opts.timeout || 30000;
        const o = { ...opts };
        delete o.timeout; delete o.retries;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), ms);
        try {
            return await fetch(`${_OA_API_BASE}${path}`, { ...o, signal: controller.signal });
        } finally {
            clearTimeout(timer);
        }
    };

// escapeHtml：HTML 实体转义
const _oaEscapeHtml = (typeof escapeHtml === 'function')
    ? escapeHtml
    : function (value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[ch]));
    };

function _oaIsRealNumber(value) {
    return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
}

const _oaFormatNumber = (typeof formatNumber === 'function')
    ? formatNumber
    : function (value, digits = 2, suffix = '') {
        return _oaIsRealNumber(value) ? `${Number(value).toFixed(digits)}${suffix}` : '--';
    };

const _oaFormatMaybePct = (typeof formatMaybePct === 'function')
    ? formatMaybePct
    : function (value, digits = 2) {
        return _oaIsRealNumber(value) ? `${Number(value).toFixed(digits)}%` : '--';
    };

const _oaSetInlineStatus = (typeof setInlineStatus === 'function')
    ? setInlineStatus
    : function (id, message, type = '') {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = message || '';
        el.dataset.statusType = type || '';
        el.style.color = (type === 'error') ? '#dc2626'
                       : (type === 'ok') ? '#059669'
                       : '#6b7280';
    };

/* ============================================================
 * 页面入口（供 app.js 路由调用）
 * ============================================================ */

function setupOvernightArbitragePage() {
    loadLatestOvernightDecision();
}

async function runOvernightArbitrage() {
    const body = document.getElementById('overnightDecisionBody');
    const btn = document.querySelector('.btn-primary[onclick="runOvernightArbitrage()"]');
    const oldText = btn ? btn.textContent : '';
    if (btn) {
        btn.disabled = true;
        btn.textContent = '决策生成中...';
    }
    _oaSetInlineStatus('overnightStatus', '已提交尾盘套利任务，等待 14:43 决策结果...');
    if (body) {
        body.innerHTML = '<div class="empty-state"><p>尾盘套利任务已提交</p><p style="font-size:12px;color:#999">正在拉取全市场行情、涨停池与5分钟K线增强</p></div>';
    }

    try {
        const startResp = await _oaApiFetch('/overnight-arbitrage/run', { method: 'POST', timeout: 60000, retries: 1 });
        const startData = await startResp.json();
        if (startData.status !== 'started') {
            throw new Error(startData.message || '启动失败');
        }
        for (let i = 0; i < 80; i++) {
            await new Promise(r => setTimeout(r, 3000));
            const pollResp = await _oaApiFetch('/overnight-arbitrage/latest', { timeout: 12000, retries: 0 });
            const data = await pollResp.json();
            if (data.status === 'completed') {
                renderOvernightDecision(data);
                _oaSetInlineStatus('overnightStatus', `决策已生成：BUY ${data.buy_count || 0}，WATCH ${data.watch_count || 0}`, 'ok');
                return;
            }
            if (data.status === 'error') {
                throw new Error(data.message || '尾盘套利任务异常');
            }
        }
        throw new Error('尾盘套利任务超时，请稍后刷新最新决策');
    } catch (e) {
        _oaSetInlineStatus('overnightStatus', e.message || '请求异常，请检查后端服务', 'error');
        if (body) body.innerHTML = `<div class="empty-state"><p style="color:#e60012">${_oaEscapeHtml(e.message || '请求异常，请检查后端服务')}</p></div>`;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = oldText;
        }
    }
}

async function loadLatestOvernightDecision() {
    try {
        const resp = await _oaApiFetch('/overnight-arbitrage/latest', { timeout: 12000, retries: 0 });
        const data = await resp.json();
        renderOvernightDecision(data);
    } catch (e) {
        _oaSetInlineStatus('overnightStatus', e.message || '最新决策读取失败', 'error');
    }
}

function renderOvernightDecision(data) {
    OvernightArbitrage.state.lastDecision = data || {};
    const body = document.getElementById('overnightDecisionBody');
    const countEl = document.getElementById('overnightResultCount');
    const metaEl = document.getElementById('overnightMeta');
    if (!body) return;
    const results = Array.isArray(data?.results) ? data.results : [];
    if (countEl) countEl.textContent = `${results.length} 条结果`;
    if (metaEl) {
        metaEl.textContent = data?.status === 'completed'
            ? `${data.date || '--'} ${data.generated_at || '--'} | 有效窗口 ${data.valid_window || '14:43-14:55'}`
            : (data?.message || '等待 14:43 决策');
    }
    if (data?.status !== 'completed') {
        body.innerHTML = `<div class="empty-state"><p>${_oaEscapeHtml(data?.message || '暂无尾盘套利决策')}</p><p style="font-size:12px;color:#999">交易日14:43自动运行，也可手动触发。</p></div>`;
        return;
    }
    if (!results.length) {
        body.innerHTML = '<div class="empty-state"><p>本次无可执行尾盘套利候选</p><p style="font-size:12px;color:#999">空仓也是策略输出。</p></div>';
        return;
    }
    const groups = [
        ['BUY', '可买'],
        ['WATCH', '观察'],
    ];
    body.innerHTML = groups.map(([action, label]) => renderOvernightGroup(
        label,
        results.filter(item => item.action === action),
        action.toLowerCase()
    )).join('') + renderOvernightSourceStatus(data);
}

function renderOvernightGroup(label, items, actionClass) {
    if (!items.length) {
        return `<div class="overnight-section"><h3>${_oaEscapeHtml(label)}</h3><div class="empty-state compact">暂无${_oaEscapeHtml(label)}标的</div></div>`;
    }
    const cards = items.map(item => {
        const reasons = (item.reasons || []).map(v => `<span>${_oaEscapeHtml(v)}</span>`).join('');
        const risks = (item.risks || []).map(v => `<span>${_oaEscapeHtml(v)}</span>`).join('') || '<span>--</span>';
        return `<article class="overnight-card ${actionClass}">
            <div class="overnight-card-head">
                <div><b>${_oaEscapeHtml(item.name || '--')}</b><span class="stock-code">${_oaEscapeHtml(item.code || '--')}</span></div>
                <span class="tag tag-${actionClass}">${_oaEscapeHtml(item.action || '--')}</span>
            </div>
            <div class="overnight-score">${_oaFormatNumber(item.decision_score, 2)}<span>决策分</span></div>
            <div class="overnight-metrics">
                <span>现价 <b>${_oaFormatNumber(item.current_price, 2)}</b></span>
                <span>涨幅 <b class="up-text">${_oaFormatMaybePct(item.change_pct, 2)}</b></span>
                <span>换手 <b>${_oaFormatMaybePct(item.turnover, 2)}</b></span>
                <span>量比 <b>${_oaFormatNumber(item.volume_ratio, 2)}</b></span>
            </div>
            <div class="overnight-chip-row">${reasons}</div>
            <div class="overnight-risk">风险: ${risks}</div>
        </article>`;
    }).join('');
    return `<div class="overnight-section"><h3>${_oaEscapeHtml(label)}</h3><div class="overnight-decision-grid">${cards}</div></div>`;
}

function renderOvernightSourceStatus(data) {
    const sources = data?.source_status || {};
    const rows = Object.entries(sources).map(([key, source]) => {
        const status = source?.status || '--';
        const count = source?.count ?? '';
        return `<span>${_oaEscapeHtml(key)}: <b>${_oaEscapeHtml(status)}</b>${count !== '' ? ` ${_oaEscapeHtml(count)}条` : ''}</span>`;
    }).join('');
    return `<div class="overnight-source-status">${rows}</div>`;
}
