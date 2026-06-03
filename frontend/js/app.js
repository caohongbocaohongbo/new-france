/* New France — 尾盘涨停选股系统 前端逻辑 */
const API_BASE = (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
    ? 'http://localhost:8000/api/v1'
    : 'https://new-france-api.onrender.com/api/v1';

// 带超时和重试的 fetch（Render 免费层冷启动需要 30-60s）
async function apiFetch(path, opts = {}) {
    const ms = opts.timeout || 30000;  // 默认30s，覆盖Render冷启动
    const maxRetries = opts.retries || 1;
    delete opts.timeout;
    delete opts.retries;

    let lastError;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
        if (attempt > 0) {
            // 重试前等待（指数退避）
            await new Promise(r => setTimeout(r, Math.min(2000 * Math.pow(2, attempt - 1), 10000)));
        }
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), ms);
        try {
            const resp = await fetch(`${API_BASE}${path}`, { ...opts, signal: controller.signal });
            return resp;
        } catch (e) {
            lastError = e;
        } finally {
            clearTimeout(timeout);
        }
    }
    throw lastError;
}

// 所有数据均来自后台API，无本地DEMO数据

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[ch]));
}

function jsString(value) {
    return String(value ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, ' ');
}

function toNumber(value, fallback = 0) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
}

function isRealNumber(value) {
    return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
}

function formatNumber(value, digits = 2, suffix = '') {
    return isRealNumber(value) ? `${Number(value).toFixed(digits)}${suffix}` : '--';
}

function formatMaybePct(value, digits = 2) {
    return isRealNumber(value) ? `${Number(value).toFixed(digits)}%` : '--';
}

function valueClass(value) {
    if (!isRealNumber(value)) return '';
    return Number(value) >= 0 ? 'up-text' : 'down-text';
}

function parseDecoratedNumber(value, fallback = 0) {
    const raw = String(value ?? '').replace(/[,%％亿只分\s]/g, '');
    const n = Number(raw);
    return Number.isFinite(n) ? n : fallback;
}

function setInlineStatus(id, message, type = '') {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = message || '';
    el.className = ['inline-status', type].filter(Boolean).join(' ');
}

function setFormStatus(id, message, type = '') {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = message || '';
    el.className = ['form-status', type].filter(Boolean).join(' ');
}

function emptyChart(el, message) {
    if (!el) return;
    el.classList.remove('loading');
    el.innerHTML = `<div class="detail-empty">${escapeHtml(message)}</div>`;
}

function formatDateShort(value) {
    if (!value) return '--';
    const s = String(value);
    return s.length >= 10 ? s.slice(5, 10) : s;
}

function formatDateTimeShort(value) {
    if (!value) return '--';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return String(value).replace('T', ' ').slice(0, 16);
    }
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function pad2(value) {
    return String(value ?? 0).padStart(2, '0');
}

function clampInt(value, min, max) {
    const n = Number.parseInt(value, 10);
    if (!Number.isFinite(n)) return min;
    return Math.min(max, Math.max(min, n));
}

function buildClockOptions(max, selected, label) {
    return Array.from({ length: max + 1 }, (_, value) => {
        const text = pad2(value);
        const isSelected = value === selected ? ' selected' : '';
        return `<option value="${pad2(value)}"${isSelected}>${text}</option>`;
    }).join('');
}

function getLatestFbtParts(value) {
    const digits = String(Math.trunc(Number(value) || 0)).replace(/\D/g, '').padStart(6, '0').slice(-6);
    return {
        hour: clampInt(digits.slice(0, 2), 0, 23),
        minute: clampInt(digits.slice(2, 4), 0, 59),
        second: clampInt(digits.slice(4, 6), 0, 59),
    };
}

function readLatestFbtFromForm() {
    const hour = clampInt(document.querySelector('#strategyForm [data-config-key="latestFbtHour"]')?.value, 0, 23);
    const minute = clampInt(document.querySelector('#strategyForm [data-config-key="latestFbtMinute"]')?.value, 0, 59);
    const second = clampInt(document.querySelector('#strategyForm [data-config-key="latestFbtSecond"]')?.value, 0, 59);
    return hour * 10000 + minute * 100 + second;
}

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initWatchlistTabs();
    initNationalTeamTabs();
    initRecommendationControls();
    updateDateTime();
    checkSystemStatus();
    // loadDashboard() 由 initNavigation() → navigateTo('dashboard') 触发, 避免重复调用
    setInterval(updateDateTime, 60000);
});

// ---- Navigation ----
function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            navigateTo(item.dataset.page);
        });
    });
    window.addEventListener('hashchange', () => {
        navigateTo(location.hash.replace('#', '') || 'dashboard', false);
    });
    navigateTo(location.hash.replace('#', '') || 'dashboard', false);
}

let _currentPage = '';
function navigateTo(route, pushState = true, force = false) {
    const [page, queryString] = String(route || 'dashboard').split('?');
    if (page === 'watchlist' && queryString) applyWatchlistQuery(queryString);
    if (!force && page === _currentPage && !queryString) return;
    _currentPage = page;
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const nav = document.querySelector(`[data-page="${page}"]`);
    if (nav) nav.classList.add('active');
    const pageEl = document.getElementById(`page-${page}`);
    if (pageEl) pageEl.classList.add('active');
    if (pushState) history.replaceState(null, '', '#' + route);
    document.getElementById('pageTitle').textContent = {
        dashboard:'Dashboard 市场总览',watchlist:'监控列表',
        recommendations:'推荐结果',screening:'手动筛选',
        'national-team':'国家队动向',settings:'策略配置'
    }[page] || page;
    setTopbarMeta(page);
    if (page === 'dashboard') loadDashboard();
    if (page === 'watchlist') loadWatchlist();
    if (page === 'recommendations') loadRecommendations();
    if (page === 'screening') setupScreeningPage();
    if (page === 'national-team') loadNationalTeam();
    if (page === 'settings') setupSettingsPage();
}

let wlCurrentStatus = ''; // current tab filter
let recommendationLastData = null;
let recommendationCurrentLevel = '';
let recommendationEnrichedResults = [];
let ntCurrentEntity = '';
let ntCurrentPage = 1;
let ntEventsOffset = 0;
let ntEventsTotal = 0;
let ntEventsLoading = false;
const NT_EVENTS_PAGE_SIZE = 5;

function applyWatchlistQuery(queryString) {
    const params = new URLSearchParams(queryString);
    const search = params.get('search') || params.get('code') || params.get('name') || '';
    const status = params.get('status') || '';
    const searchInput = document.getElementById('wlSearch');
    const statusSelect = document.getElementById('wlStatus');
    if (searchInput) searchInput.value = search;
    if (statusSelect) statusSelect.value = status;
    wlCurrentStatus = status;
    syncWatchlistTabs();
    if (search) {
        const name = params.get('name');
        setInlineStatus('wlQueryHint', `已按 ${name ? `${name} ` : ''}${search} 过滤`);
    }
}

function initWatchlistTabs() {
    document.querySelectorAll('#wlTabs .tab').forEach(tab => {
        tab.addEventListener('click', () => {
            wlCurrentStatus = tab.dataset.status;
            document.getElementById('wlStatus').value = wlCurrentStatus;
            syncWatchlistTabs();
            loadWatchlist(1);
        });
    });

    const statusSelect = document.getElementById('wlStatus');
    if (statusSelect) {
        statusSelect.addEventListener('change', () => {
            wlCurrentStatus = statusSelect.value;
            syncWatchlistTabs();
            loadWatchlist(1);
        });
    }

    const searchInput = document.getElementById('wlSearch');
    if (searchInput) {
        searchInput.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') loadWatchlist(1);
        });
    }
}

function initNationalTeamTabs() {
    document.querySelectorAll('#ntTabs .tab').forEach(tab => {
        tab.addEventListener('click', () => {
            ntCurrentEntity = tab.dataset.entity || '';
            document.querySelectorAll('#ntTabs .tab').forEach(item => item.classList.remove('active'));
            tab.classList.add('active');
            loadNationalTeam(1);
        });
    });
    const eventsScroll = document.getElementById('ntEventsScroll');
    if (eventsScroll) {
        eventsScroll.addEventListener('scroll', () => {
            if (eventsScroll.scrollTop + eventsScroll.clientHeight >= eventsScroll.scrollHeight - 64) {
                loadMoreNationalTeamEvents();
            }
        });
    }
}

function syncWatchlistTabs() {
    document.querySelectorAll('#wlTabs .tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.status === wlCurrentStatus);
    });
}

function initRecommendationControls() {
    const levelSelect = document.getElementById('recLevel');
    if (levelSelect) {
        levelSelect.addEventListener('change', () => setRecommendationLevel(levelSelect.value, false));
    }
}

// ---- System Status ----
async function checkSystemStatus() {
    const dot = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    const nextRun = document.getElementById('nextRun');
    const now = new Date();
    const isWeekday = now.getDay() >= 1 && now.getDay() <= 5;
    const isHours = now.getHours() >= 9 && now.getHours() < 15;

    try {
        const resp = await apiFetch('/system/status');
        const data = await resp.json();
        text.textContent = data.is_trading_hours ? '交易中' : (data.is_trading_day ? '已收盘' : '休市中');
        dot.className = 'status-dot' + (data.is_trading_hours || data.is_trading_day ? '' : ' off');
        nextRun.textContent = '下次执行: ' + data.cron_expression;
    } catch (e) {
        // Fallback: 本地判断
        if (isWeekday && isHours) { text.textContent = '交易中'; dot.className = 'status-dot'; }
        else if (isWeekday) { text.textContent = '已收盘'; dot.className = 'status-dot'; }
        else { text.textContent = '休市中'; dot.className = 'status-dot off'; }
        nextRun.textContent = '下次执行: 交易日 15:10 (北京时间)';
    }
}

function updateDateTime() {
    const now = new Date();
    const weekdays = ['周日','周一','周二','周三','周四','周五','周六'];
    if (_currentPage === 'dashboard' || !_currentPage) {
        document.getElementById('dateDisplay').textContent =
            `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')} ${weekdays[now.getDay()]}`;
    }
}

function setTopbarMeta(page, text = '') {
    const el = document.getElementById('dateDisplay');
    if (!el) return;
    if (text) {
        el.textContent = text;
        return;
    }
    if (page === 'watchlist') el.textContent = '数据源: 东方财富涨停股池';
    else if (page === 'recommendations') el.textContent = '筛选日期: -- | 审核通过率: --';
    else if (page === 'screening') el.textContent = '';
    else updateDateTime();
}

// ---- Dashboard ----
async function loadDashboard() {
    // 重置为空状态
    document.getElementById('metricWatchlist').textContent = '--';
    document.getElementById('metricNewZT').textContent = '--';
    document.getElementById('metricRecs').textContent = '--';
    document.getElementById('metricIndex').textContent = '--';
    document.getElementById('metricIndexMeta').textContent = '等待收盘快照';
    document.getElementById('recentRecsBody').innerHTML = '<tr><td colspan="6" class="empty-cell">加载中...</td></tr>';
    document.getElementById('recentRecAudit').textContent = '';
    document.getElementById('ztCount').textContent = '--';
    document.getElementById('limitSource').textContent = '数据源: --';
    document.getElementById('limitSummary').textContent = '';
    document.getElementById('sectorDist').innerHTML = '<p style="color:#8B95A8;text-align:center;padding:20px">加载中...</p>';
    document.getElementById('marketTrend').innerHTML = '<div class="detail-empty">加载中...</div>';
    renderDashboardOptionalSources(null);

    try {
        // 并行请求 watchlist stats 和最新筛选结果
        const [wlResp, recResp, statusResp] = await Promise.all([
            apiFetch('/watchlist/stats'),
            apiFetch('/screening/latest'),
            apiFetch('/system/status')
        ]);
        const wl = await wlResp.json();
        const rec = await recResp.json();
        const status = await statusResp.json();
        const indexSnapshot = status?.index_snapshot || rec.index_snapshot || {};
        const indexData = {
            ...rec,
            index_snapshot: indexSnapshot,
            index_value: indexSnapshot.value ?? rec.index_value,
            index_gain: indexSnapshot.gain_pct ?? rec.index_gain,
        };

        // 监控股票数
        document.getElementById('metricWatchlist').textContent = wl.total || 0;
        document.getElementById('metricNewZT').textContent = wl.new_today || 0;
        renderDashboardIndex(indexData);

        // 推荐数和指数
        if (rec.results && rec.results.length > 0) {
            const approvedRecs = rec.results.filter(r => ['STRONG_BUY', 'BUY', 'WATCH'].includes(r.recommendation));
            document.getElementById('metricRecs').textContent = approvedRecs.length;
            if (approvedRecs.length) renderRecentRecommendations(approvedRecs.slice(0, 5));
            else document.getElementById('recentRecsBody').innerHTML = '<tr><td colspan="6" class="empty-cell">暂无审核通过推荐</td></tr>';
            document.getElementById('ztCount').textContent = rec.zt_list?.length || rec.total_scored || rec.results.length;
            renderRecentAudit(rec);
        } else {
            document.getElementById('metricRecs').textContent = '0';
            document.getElementById('ztCount').textContent = '0';
            document.getElementById('recentRecsBody').innerHTML = '<tr><td colspan="6" class="empty-cell">暂无推荐数据</td></tr>';
            // 尝试获取上证指数
            try {
                const idxResp = await apiFetch('/system/status');
                const idx = await idxResp.json();
                document.getElementById('metricIndex').textContent = '--';
                document.getElementById('metricIndexMeta').textContent = idx?.is_trading_day ? '等待筛选快照' : '休市无快照';
            } catch(e) {}
        }

        renderLimitOverview(rec);
        renderMarketTrend(rec);
        renderDashboardOptionalSources(status.optional_sources || rec.optional_sources);

    } catch(e) {
        // API 不可用（可能是 Render 冷启动），提供重试
        document.getElementById('metricWatchlist').textContent = '--';
        document.getElementById('metricNewZT').textContent = '--';
        document.getElementById('metricRecs').textContent = '--';
        document.getElementById('metricIndex').textContent = '--';
        document.getElementById('metricIndexMeta').textContent = '行情源暂不可用';
        document.getElementById('recentRecsBody').innerHTML =
            '<tr><td colspan="6" class="empty-cell" style="color:#e60012">'
            + 'API 连接失败，后端可能正在启动中（冷启动约30-60秒）<br>'
            + '<button class="btn" onclick="refreshData()" style="margin-top:8px">点击重试</button>'
            + '</td></tr>';
        document.getElementById('ztCount').textContent = '--';
        document.getElementById('limitSource').textContent = '数据源: 后端服务不可用';
        document.getElementById('limitSummary').textContent = '';
        document.getElementById('sectorDist').innerHTML =
            '<p style="color:#e60012;text-align:center;padding:20px">后端服务不可用</p>';
        document.getElementById('marketTrend').innerHTML =
            '<div class="detail-empty" style="color:#e60012">后端服务不可用</div>';
        renderDashboardOptionalSources(null);
    }
}

function renderDashboardOptionalSources(optionalSources) {
    const panel = document.getElementById('dashboardOptionalSources');
    const grid = document.getElementById('dashboardOptionalSourceGrid');
    if (!panel || !grid) return;

    const sources = Object.values(optionalSources?.sources || {})
        .filter(source => source?.surfaces?.dashboard_enabled);
    if (!sources.length) {
        panel.hidden = true;
        grid.innerHTML = '';
        return;
    }

    panel.hidden = false;
    grid.innerHTML = sources.map(source => {
        const statusClass = source.ok && source.ready_for_promotion ? 'ok' : (source.ok ? 'pending' : 'error');
        const statusText = source.ok && source.ready_for_promotion ? '已达稳定阈值' : (source.ok ? '旁路观察中' : '来源异常');
        const fetchedAt = source.source_status?.fetched_at ? formatDateTimeShort(source.source_status.fetched_at) : '--';
        const count = source.data?.record_count ?? '--';
        const successes = source.consecutive_successes ?? 0;
        const required = source.required_successes ?? '--';
        const sourceName = source.source_status?.source || '--';
        return `
            <div class="optional-source-card ${statusClass}">
                <div class="optional-source-top">
                    <span>${escapeHtml(source.label || source.key || '--')}</span>
                    <b>${escapeHtml(statusText)}</b>
                </div>
                <div class="optional-source-value">${escapeHtml(count)} 条</div>
                <div class="optional-source-meta">连续成功 ${escapeHtml(successes)} / ${escapeHtml(required)} · ${escapeHtml(fetchedAt)}</div>
                <div class="optional-source-meta">来源：${escapeHtml(sourceName)}</div>
            </div>
        `;
    }).join('');
}

function renderDashboardIndex(data) {
    const snapshot = data?.index_snapshot || {};
    const value = isRealNumber(snapshot.value) ? Number(snapshot.value) : (isRealNumber(data?.index_value) ? Number(data.index_value) : null);
    const gain = isRealNumber(snapshot.gain_pct) ? Number(snapshot.gain_pct) : (isRealNumber(data?.index_gain) ? Number(data.index_gain) : null);
    const valueEl = document.getElementById('metricIndex');
    const metaEl = document.getElementById('metricIndexMeta');
    if (!valueEl || !metaEl) return;

    valueEl.textContent = value === null ? '--' : value.toFixed(2);
    valueEl.className = 'metric-value';
    if (gain !== null) valueEl.classList.add(gain >= 0 ? 'up' : 'down');

    if (value === null && gain === null) {
        metaEl.textContent = '行情源暂不可用';
        return;
    }

    const gainText = gain === null ? '涨跌幅--' : `${gain >= 0 ? '+' : ''}${gain.toFixed(2)}%`;
    const source = snapshot.source || '真实行情快照';
    const fetched = snapshot.fetched_at ? ` · ${formatDateTimeShort(snapshot.fetched_at)}` : '';
    metaEl.textContent = `${gainText} · ${source}${fetched}`;
}

function renderSectorDist(sectors) {
    if (!sectors || Object.keys(sectors).length === 0) {
        document.getElementById('sectorDist').innerHTML = '<p style="color:#8B95A8;text-align:center;padding:20px">暂无板块分布数据</p>';
        return;
    }
    const total = Object.values(sectors).reduce((a,b)=>a+b,0);
    const colors = ['#3897f0','#2eb9ee','#f5a400','#6273f5','#009a70','#8B95A8'];
    let i = 0;
    document.getElementById('sectorDist').innerHTML = Object.entries(sectors)
        .sort((a,b) => b[1]-a[1])
        .map(([name, count]) => {
            const pct = total > 0 ? Math.round(count/total*100) : 0;
            const c = colors[i++ % colors.length];
            return `<div class="sector-bar"><div class="sector-bar-label"><span>${name}: ${count}只</span><span>${pct}%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:${pct}%;background:${c}"></div></div></div>`;
        }).join('');
}

function renderRecentAudit(data) {
    const el = document.getElementById('recentRecAudit');
    if (!el) return;
    const strong = data.strong_buy || 0;
    const buy = data.buy || 0;
    const watch = data.watch || 0;
    const total = data.total_scored || (data.results?.length || 0);
    const passRate = total ? Math.round((strong + buy + watch) / total * 100) : 0;
    const downgraded = (data.results || []).filter(s => s.audit?.downgraded).length;
    el.textContent = `审核后: STRONG_BUY=${strong} · BUY=${buy} · WATCH=${watch} | 审核通过率 ${passRate}% | 降级 ${downgraded}只`;
}

function renderLimitOverview(data) {
    const ztList = data.zt_list || [];
    const meta = data.zt_meta || {};
    const count = ztList.length || data.total_scored || 0;
    document.getElementById('ztCount').textContent = `${count} 只`;
    const fetched = meta.fetched_at ? String(meta.fetched_at).replace('T', ' ').slice(0, 16) : (data.date || '--');
    document.getElementById('limitSource').textContent = `数据源: ${meta.source || '东方财富 push2ex'} · 采集: ${fetched}`;

    const sectors = data.sector_dist || inferBoardDistribution(ztList);
    renderSectorDist(sectors);
    trimSectorBarsForDashboard();
    document.getElementById('limitSummary').textContent = `总计 ${count} 只涨停股 · 覆盖 ${Object.keys(sectors || {}).length || '--'} 个行业板块`;
}

function trimSectorBarsForDashboard() {
    const container = document.getElementById('sectorDist');
    if (!container) return;
    [...container.querySelectorAll('.sector-bar')].forEach((bar, index) => {
        bar.style.display = index < 4 ? '' : 'none';
    });
}

function inferBoardDistribution(items) {
    const dist = {};
    for (const item of items || []) {
        const code = String(item.code || '');
        let name = '深圳主板';
        if (code.startsWith('688')) name = '科创板';
        else if (code.startsWith('6')) name = '上海主板';
        else if (code.startsWith('3')) name = '创业板';
        else if (code.startsWith('8') || code.startsWith('9')) name = '北交所';
        dist[name] = (dist[name] || 0) + 1;
    }
    return dist;
}

function renderMarketTrend(data) {
    const el = document.getElementById('marketTrend');
    if (!el) return;
    const ztList = data.zt_list || [];
    const dtList = data.dt_list || data.limit_down_list || [];
    const timeSlots = [
        ['09:25', 92500], ['09:30', 93000], ['09:45', 94500], ['10:00', 100000],
        ['10:30', 103000], ['11:00', 110000], ['13:00', 130000], ['13:30', 133000],
        ['14:00', 140000], ['14:30', 143000], ['14:45', 144500], ['15:00', 150000]
    ];
    const limitUpBuckets = Object.fromEntries(timeSlots.map(([label]) => [label, 0]));
    const limitDownBuckets = Object.fromEntries(timeSlots.map(([label]) => [label, 0]));
    for (const item of ztList) {
        if (!isRealNumber(item.seal_time)) continue;
        const seal = Number(item.seal_time);
        const slot = [...timeSlots].reverse().find(([, threshold]) => seal >= threshold)?.[0] || timeSlots[0][0];
        limitUpBuckets[slot] = (limitUpBuckets[slot] || 0) + 1;
    }
    for (const item of dtList) {
        const rawTime = item.seal_time ?? item.open_time ?? item.time;
        if (!isRealNumber(rawTime)) continue;
        const seal = Number(rawTime);
        const slot = [...timeSlots].reverse().find(([, threshold]) => seal >= threshold)?.[0] || timeSlots[0][0];
        limitDownBuckets[slot] = (limitDownBuckets[slot] || 0) + 1;
    }
    const points = timeSlots.map(([label]) => ({
        label,
        up: limitUpBuckets[label] || 0,
        down: limitDownBuckets[label] || 0,
    }));
    if ((!ztList.length && !dtList.length) || points.every(item => !item.up && !item.down)) {
        emptyChart(el, '接口未返回封板时间明细，未使用本地假数据补绘');
        return;
    }
    el.innerHTML = '';
    el.classList.remove('loading');
    disposeChart(dashboardTrendChart);
    dashboardTrendChart = echarts.init(el, getEChartsLightTheme(), { renderer: 'canvas' });
    dashboardTrendChart.setOption(mergeChartOption({
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'line' },
            backgroundColor: 'rgba(255,255,255,0.98)',
            borderColor: '#e5e7eb',
            textStyle: { color: '#1f2937' },
            formatter: params => {
                const row = points[params?.[0]?.dataIndex] || {};
                return [
                    `<strong>时间：${row.label || '--'}</strong>`,
                    `涨停：${row.up || 0} 只`,
                    `跌停：${row.down || 0} 只`,
                ].join('<br>');
            },
        },
        grid: chartGrid({ top: 24, bottom: 42, left: 44, right: 20 }),
        xAxis: {
            type: 'category',
            data: points.map(item => item.label),
            axisLabel: { interval: 0, fontSize: 11, color: '#6b7280' },
            axisTick: { alignWithLabel: true },
        },
        yAxis: {
            type: 'value',
            minInterval: 1,
            axisLabel: {
                fontSize: 11,
                color: '#6b7280',
                formatter: value => Math.abs(Number(value) || 0),
            },
            splitLine: { lineStyle: { color: '#eef0f4' } },
        },
        series: [
            {
                type: 'bar',
                name: '涨停',
                data: points.map(item => item.up),
                barWidth: '38%',
                itemStyle: {
                    color: '#D60A22',
                    borderRadius: [4, 4, 0, 0],
                },
            },
            {
                type: 'bar',
                name: '跌停',
                data: points.map(item => item.down ? -item.down : 0),
                barWidth: '38%',
                itemStyle: {
                    color: '#027B66',
                    borderRadius: [0, 0, 4, 4],
                },
            },
            {
                type: 'line',
                name: '趋势',
                data: points.map(item => item.up - item.down),
                smooth: true,
                symbol: 'circle',
                symbolSize: 4,
                lineStyle: { color: '#8a8f99', width: 1.2, opacity: 0.45 },
                itemStyle: { color: '#8a8f99', opacity: 0.55 },
                emphasis: { disabled: true },
            },
        ],
    }));
    requestAnimationFrame(() => {
        try { dashboardTrendChart?.resize(); } catch (e) {}
    });
}

function renderRecentRecommendations(results) {
    const names = {strong_buy:'STRONG BUY',buy:'BUY',watch:'WATCH',pass:'PASS'};
    document.getElementById('recentRecsBody').innerHTML = results.map(s => `
        <tr>
            <td><span class="stock-code">${s.code}</span></td>
            <td>${s.name}</td>
            <td>${formatNumber(s.adjusted_score, 0)}分</td>
            <td><span class="${valueClass(s.drop_pct)}">${formatMaybePct(s.drop_pct, 2)}</span></td>
            <td><span class="tag tag-${(s.recommendation||'pass').toLowerCase()}">${names[(s.recommendation||'pass').toLowerCase()]}</span></td>
            <td>${s.zt_date||'--'}</td>
        </tr>`).join('');
}

// ---- Watchlist ----
let wlCurrentPage = 1;
let wlFullCache = null; // 全量数据缓存（用于状态分类计数）
let wlLastItems = [];
let wlSortKey = '';
let wlSortOrder = 'asc';
let wlRequestSeq = 0;
let dashboardTrendChart = null;
let recommendationTrendCharts = {};
let chartResizeTimer = null;

function setWatchlistSort(key) {
    if (wlSortKey === key) {
        wlSortOrder = wlSortOrder === 'asc' ? 'desc' : 'asc';
    } else {
        wlSortKey = key;
        wlSortOrder = 'asc';
    }
    updateWatchlistSortHeaders();
    loadWatchlist(1);
}

function formatFbtToClock(value) {
    if (value === null || value === undefined || value === '') return '';
    const digits = String(Math.trunc(Number(value) || 0)).replace(/\D/g, '').padStart(6, '0').slice(-6);
    return `${digits.slice(0, 2)}:${digits.slice(2, 4)}:${digits.slice(4, 6)}`;
}

function parseClockToFbt(value) {
    if (value === null || value === undefined || value === '') return 0;
    const match = String(value).trim().match(/^(\d{2}):(\d{2}):(\d{2})$/);
    if (match) {
        const hours = Number(match[1]);
        const mins = Number(match[2]);
        const secs = Number(match[3]);
        return hours * 10000 + mins * 100 + secs;
    }
    const digits = String(value).replace(/\D/g, '');
    if (digits.length === 6) return Number(digits);
    return toNumber(value, 0);
}

function makeChartId(prefix, value) {
    return `${prefix}-${String(value ?? '').replace(/[^a-zA-Z0-9_-]/g, '_')}`;
}

function disposeChart(chart) {
    if (!chart) return;
    try {
        chart.dispose();
    } catch (e) {}
}

function scheduleChartResize() {
    clearTimeout(chartResizeTimer);
    chartResizeTimer = setTimeout(() => {
        if (dashboardTrendChart) {
            try { dashboardTrendChart.resize(); } catch (e) {}
        }
        Object.values(recommendationTrendCharts).forEach(chart => {
            try { chart.resize(); } catch (e) {}
        });
    }, 120);
}

window.addEventListener('resize', scheduleChartResize);

function updateWatchlistSortHeaders() {
    document.querySelectorAll('.sort-header[data-sort-key]').forEach(button => {
        const isActive = button.dataset.sortKey === wlSortKey;
        button.classList.toggle('active', isActive);
        button.classList.toggle('asc', isActive && wlSortOrder === 'asc');
        button.classList.toggle('desc', isActive && wlSortOrder === 'desc');
        const th = button.closest('th');
        if (th) th.setAttribute('aria-sort', isActive ? (wlSortOrder === 'asc' ? 'ascending' : 'descending') : 'none');
    });
}

function buildWatchlistParams(page, includeStatus = true) {
    const params = new URLSearchParams({ page: String(page), size: '15' });
    const search = document.getElementById('wlSearch')?.value.trim();
    const selectedStatus = document.getElementById('wlStatus')?.value ?? wlCurrentStatus;
    wlCurrentStatus = selectedStatus;

    if (includeStatus && wlCurrentStatus) params.set('status', wlCurrentStatus);
    if (search) params.set('search', search);
    if (includeStatus && wlSortKey) {
        params.set('sort_by', wlSortKey);
        params.set('sort_order', wlSortOrder);
    }
    return params;
}

async function loadWatchlist(page = 1) {
    const requestSeq = ++wlRequestSeq;
    wlCurrentPage = page;
    syncWatchlistTabs();
    updateWatchlistSortHeaders();
    const currentSearch = document.getElementById('wlSearch')?.value.trim();
    if (!currentSearch) setInlineStatus('wlQueryHint', '');

    // 重置为空状态
    document.getElementById('wlTableBody').innerHTML = '<tr><td colspan="14" class="empty-cell">加载中...</td></tr>';
    document.getElementById('wlCountAll').textContent = '0';
    document.getElementById('wlCountActive').textContent = '0';
    document.getElementById('wlCountRec').textContent = '0';
    document.getElementById('wlCountExpired').textContent = '0';

    try {
        const pageParams = buildWatchlistParams(page, true);
        // 同时获取分页数据和真实状态统计
        const [pageResp, statsResp] = await Promise.all([
            apiFetch(`/watchlist?${pageParams.toString()}`),
            apiFetch('/watchlist/stats')
        ]);
        if (!pageResp.ok) {
            const err = await pageResp.json().catch(() => ({}));
            throw new Error(err.detail || '监控列表加载失败');
        }
        const data = await pageResp.json();
        const statsData = await statsResp.json();
        if (requestSeq !== wlRequestSeq) return;
        wlLastItems = data.items || [];

        if (data.items && data.items.length > 0) {
            renderWatchlistTable(data.items);
            renderPagination('wlPagination', data.total, data.page, data.size, loadWatchlist);
            if (currentSearch) setInlineStatus('wlQueryHint', `当前按「${currentSearch}」过滤，共 ${data.total} 条`);
        } else {
            document.getElementById('wlTableBody').innerHTML = '<tr><td colspan="14" class="empty-cell">暂无监控数据。每日15:10自动从涨停股池添加。</td></tr>';
            document.getElementById('wlPagination').innerHTML = '';
            if (currentSearch) setInlineStatus('wlQueryHint', `当前按「${currentSearch}」过滤，无匹配股票`, 'error');
        }

        const counts = statsData.status_counts || {};
        document.getElementById('wlCountAll').textContent = statsData.total || data.total || 0;
        document.getElementById('wlCountActive').textContent = counts.active || 0;
        document.getElementById('wlCountRec').textContent = counts.recommended || 0;
        document.getElementById('wlCountExpired').textContent = counts.expired || 0;

    } catch(e) {
        if (requestSeq !== wlRequestSeq) return;
        wlLastItems = [];
        const message = e?.message || 'API 连接失败，后端可能正在启动中（冷启动约30-60秒）';
        document.getElementById('wlTableBody').innerHTML =
            '<tr><td colspan="14" class="empty-cell" style="color:#e60012">'
            + escapeHtml(message)
            + ' <a href="#" onclick="refreshData();return false" style="color:#3B82F6">点击重试</a></td></tr>';
        document.getElementById('wlPagination').innerHTML = '';
    }
}

function renderWatchlistTable(items) {
    const tags = {active:'回撤中',recommended:'已推荐',expired:'已过期'};
    document.getElementById('wlTableBody').innerHTML = items.map(item => {
        const dpColor = isRealNumber(item.drop_pct) ? (Number(item.drop_pct) < 0 ? '#009a70' : '#f5222d') : '#999999';
        // 封板时间格式化
        const sealTime = item.seal_time && item.seal_time !== '0' && item.seal_time !== 0
            ? (() => { const s = String(item.seal_time); const h = parseInt(s.substring(0, s.length-4)||'0'); const m = parseInt(s.substring(s.length-4, s.length-2)||'0'); return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`; })()
            : '--';
        const ztCount = item.zt_count !== undefined && item.zt_count !== null && item.zt_count !== ''
            ? item.zt_count
            : '--';
        return `<tr>
            <td><span class="stock-code">${escapeHtml(item.code)}</span></td>
            <td>${escapeHtml(item.name)}${item.zt_time ? '<div style="font-size:10px;color:#999999">封板'+escapeHtml(item.zt_time)+(item.zbc>0?' 炸板'+escapeHtml(item.zbc)+'次':'')+'</div>' : ''}</td>
            <td>${escapeHtml(item.zt_date)}</td>
            <td>${formatNumber(item.ref_price, 2)}</td>
            <td>${formatNumber(item.current_price, 2)}</td>
            <td style="color:${dpColor};font-weight:600">${formatMaybePct(item.drop_pct, 2)}</td>
            <td>${formatMaybePct(item.turnover, 2)}</td>
            <td>${formatNumber(item.vol_ratio, 2)}</td>
            <td>${formatNumber(item.pe, 2)}</td>
            <td style="font-size:12px">${sealTime}</td>
            <td style="font-size:12px">${ztCount}</td>
            <td>${escapeHtml(item.mcap||'--')}</td>
            <td><span class="tag tag-${item.status||'active'}">${tags[item.status || 'active']||'回撤中'}</span></td>
            <td style="white-space:nowrap">
                <button class="btn btn-sm" style="margin-right:4px" onclick="event.stopPropagation();showStockDetail('${item.code}')">详情</button>
                <button class="btn btn-sm" onclick="removeWatchlistItem('${item.code}')">移除</button>
            </td>
        </tr>`;
    }).join('');
}

async function removeWatchlistItem(code) {
    setInlineStatus('wlQueryHint', `正在移除 ${code}...`);
    try {
        await apiFetch(`/watchlist/${code}`, { method: 'DELETE' });
        setInlineStatus('wlQueryHint', `已移除 ${code}`);
    } catch(e) {
        setInlineStatus('wlQueryHint', `移除 ${code} 失败，请检查后端服务`, 'error');
    }
    loadWatchlist(wlCurrentPage);
}
function exportWatchlist() {
    if (!wlLastItems.length) {
        setInlineStatus('wlQueryHint', '当前没有可导出的列表数据', 'error');
        return;
    }
    const headers = ['代码','名称','涨停日期','参考价','现价','回撤%','换手率','量比','PE','封板时间','涨停次数','流通市值','状态'];
    const rows = wlLastItems.map(item => [
        item.code, item.name, item.zt_date, item.ref_price, item.current_price,
        item.drop_pct, item.turnover, item.vol_ratio, item.pe, item.seal_time,
        item.zt_count, item.mcap, item.status
    ]);
    const csv = [headers, ...rows].map(row => row.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `new-france-watchlist-${new Date().toISOString().slice(0,10)}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setInlineStatus('wlQueryHint', `已导出当前 ${wlLastItems.length} 条列表数据`);
}

// ---- National Team ----
async function loadNationalTeam(page = ntCurrentPage || 1) {
    ntCurrentPage = page;
    const body = document.getElementById('ntHoldingBody');
    if (body) body.innerHTML = '<tr><td colspan="14" class="empty-cell">加载中...</td></tr>';
    setInlineStatus('ntStatus', '');
    ntEventsOffset = 0;
    ntEventsTotal = 0;
    ntEventsLoading = false;
    updateNationalTeamEventMore();

    try {
        const params = new URLSearchParams({ page: String(page), size: '15' });
        if (ntCurrentEntity) params.set('entity', ntCurrentEntity);
        const [summaryResp, holdingsResp, eventsResp, capitalFlowsResp, holdingValuesResp] = await Promise.all([
            apiFetch('/national-team/summary'),
            apiFetch(`/national-team/holdings?${params.toString()}`),
            apiFetch(buildNationalTeamEventPath()),
            apiFetch('/national-team/capital-flows?limit=10'),
            apiFetch('/national-team/holding-values?limit=10'),
        ]);
        if (!summaryResp.ok) throw new Error('国家队摘要加载失败');
        if (!holdingsResp.ok) throw new Error('国家队持仓加载失败');
        if (!eventsResp.ok) throw new Error('国家队事件加载失败');
        if (!capitalFlowsResp.ok) throw new Error('国家队买卖资金统计加载失败');
        if (!holdingValuesResp.ok) throw new Error('国家队持股金额统计加载失败');
        const summary = await summaryResp.json();
        const holdings = await holdingsResp.json();
        const events = await eventsResp.json();
        const capitalFlows = await capitalFlowsResp.json();
        const holdingValues = await holdingValuesResp.json();

        renderNationalTeamSummary(summary);
        renderNationalTeamCapitalFlows(capitalFlows, holdingValues);
        renderNationalTeamHoldings(holdings);
        ntEventsTotal = Number(events.total || 0);
        ntEventsOffset = (events.items || []).length;
        renderNationalTeamEvents(events.items || [], false);
        updateNationalTeamEventMore();
        renderPagination('ntPagination', holdings.total || 0, holdings.page || page, holdings.size || 15, loadNationalTeam);
    } catch (e) {
        const message = e?.message || '国家队数据加载失败，请确认后端服务可用';
        if (body) body.innerHTML = `<tr><td colspan="14" class="empty-cell" style="color:#e60012">${escapeHtml(message)}</td></tr>`;
        document.getElementById('ntPagination').innerHTML = '';
        document.getElementById('ntEventsList').innerHTML = `<p class="empty-state" style="color:#e60012">${escapeHtml(message)}</p>`;
        document.getElementById('ntCapitalFlows').innerHTML = `<div class="empty-state" style="color:#e60012">${escapeHtml(message)}</div>`;
        ntEventsTotal = 0;
        ntEventsOffset = 0;
        updateNationalTeamEventMore();
        setInlineStatus('ntStatus', message, 'error');
    }
}

function buildNationalTeamEventPath() {
    return `/national-team/events?${ntCurrentEntity ? `entity=${encodeURIComponent(ntCurrentEntity)}&` : ''}limit=${NT_EVENTS_PAGE_SIZE}&offset=${ntEventsOffset}`;
}

function renderNationalTeamSummary(summary) {
    const cards = document.getElementById('ntSummaryCards');
    const entities = summary.entities || [];
    const byKey = Object.fromEntries(entities.map(item => [item.entity_key, item]));
    const source = summary.source_status || {};
    const cardData = [
        { label: '最近命中样本', value: summary.total_holdings || 0, sub: `事件 ${summary.total_events || 0} 条` },
        { label: '中央汇金', value: byKey.huijin?.holding_count || 0, sub: nationalTeamCardSub(byKey.huijin) },
        { label: '社保基金', value: byKey.social_security?.holding_count || 0, sub: nationalTeamCardSub(byKey.social_security) },
        { label: '证金公司', value: byKey.csf?.holding_count || 0, sub: nationalTeamCardSub(byKey.csf) },
    ];
    cards.innerHTML = cardData.map(item => `
        <div class="metric-card national-team-card">
            <div class="metric-label">${escapeHtml(item.label)}</div>
            <div class="metric-value">${escapeHtml(item.value)}</div>
            <div class="metric-sub">${escapeHtml(item.sub || '--')}</div>
        </div>
    `).join('');
    const sourceMeta = document.getElementById('ntSourceMeta');
    if (sourceMeta) {
        const sourceText = source.ok === false
            ? `数据源暂不可用：${source.error || source.note || '--'}`
            : `数据源：${source.source || '--'} · 采集：${formatDateTimeShort(source.fetched_at)} · ${summary.scope_note || '公开披露样本'}`;
        sourceMeta.textContent = sourceText;
    }
}

function nationalTeamCardSub(entity) {
    if (!entity) return '暂无披露';
    const c = entity.change_counts || {};
    return `报告期 ${entity.latest_report_period || '--'} · 增${c.increase || 0}/减${c.decrease || 0}/退${c.exit || 0}`;
}

function renderNationalTeamCapitalFlows(data, holdingValues = {}) {
    const container = document.getElementById('ntCapitalFlows');
    if (!container) return;
    const order = { huijin: 0, social_security: 1, csf: 2 };
    const holdingByKey = Object.fromEntries((holdingValues.entities || []).map(item => [item.entity_key, item]));
    const entities = (data.entities || []).slice().sort((a, b) => (order[a.entity_key] ?? 9) - (order[b.entity_key] ?? 9));
    if (!entities.length) {
        container.innerHTML = '<div class="empty-state">暂无可查买卖资金披露</div>';
        return;
    }
    container.innerHTML = entities.map(entity => {
        const buy = entity.buy || { items: [], total: 0 };
        const sell = entity.sell || { items: [], total: 0 };
        const holding = holdingByKey[entity.entity_key] || { items: [] };
        return `<div class="capital-flow-card">
            <div class="capital-flow-head">
                <h3>${escapeHtml(entity.entity_name || '--')}${renderNationalTeamOrgInfo(entity, holding)}</h3>
                <span>买${buy.total || 0} / 卖${sell.total || 0}</span>
            </div>
            <div class="capital-flow-sides">
                ${renderNationalTeamFlowSide('买入前10', 'buy', buy)}
                ${renderNationalTeamFlowSide('卖出前10', 'sell', sell)}
            </div>
            ${renderNationalTeamHoldingValues(holding)}
        </div>`;
    }).join('');
}

function renderNationalTeamOrgInfo(entity, holding) {
    const fullName = holding.full_name || entity.full_name || orgFullNameFallback(entity.entity_key);
    const englishName = holding.english_name || entity.english_name || orgEnglishNameFallback(entity.entity_key);
    if (!fullName && !englishName) return '';
    const title = [fullName, englishName].filter(Boolean).join('\n');
    return `<span class="org-info-wrap">
        <button class="org-info-trigger" type="button" aria-label="${escapeHtml(entity.entity_name || '')}组织机构全称" title="${escapeHtml(title)}">i</button>
        <span class="org-info-popover"><b>全称：${escapeHtml(fullName || '--')}</b>${englishName ? `<em>${escapeHtml(englishName)}</em>` : ''}</span>
    </span>`;
}

function orgFullNameFallback(entityKey) {
    return {
        huijin: '中央汇金投资有限责任公司',
        social_security: '全国社会保障基金理事会',
        csf: '中国证券金融股份有限公司',
    }[entityKey] || '';
}

function orgEnglishNameFallback(entityKey) {
    return {
        huijin: 'Central Huijin Investment Ltd.',
        social_security: 'National Council for Social Security Fund',
        csf: '',
    }[entityKey] || '';
}

function renderNationalTeamFlowSide(title, direction, flow) {
    const items = (flow.items || []).slice(0, 10);
    const maxAmount = items.reduce((max, item) => Math.max(max, Number(item.change_amount || 0)), 0);
    if (!items.length) {
        return `<div class="capital-flow-side ${direction}">
            <div class="capital-flow-side-title"><span>${escapeHtml(title)}</span><b>0</b></div>
            <div class="detail-empty">暂无可查${direction === 'buy' ? '增持' : '减持'}披露</div>
        </div>`;
    }
    return `<div class="capital-flow-side ${direction}">
        <div class="capital-flow-side-title"><span>${escapeHtml(title)}</span><b>${items.length}</b></div>
        <div class="capital-flow-list">
            ${items.map(item => {
                const amount = Number(item.change_amount || 0);
                const width = maxAmount > 0 ? Math.max(4, amount / maxAmount * 100) : 0;
                return `<a class="capital-flow-row" href="${escapeHtml(item.source_url || '#')}" target="_blank" rel="noopener" title="${escapeHtml(item.shareholder_name || '')}">
                    <div class="capital-flow-row-main">
                        <span>${escapeHtml(item.stock_name || '--')} <small>${escapeHtml(item.stock_code || '')}</small></span>
                        <em>${formatYi(amount)}</em>
                    </div>
                    <div class="capital-flow-track"><i class="capital-flow-bar" style="--bar-width:${width.toFixed(1)}%"></i></div>
                    <div class="capital-flow-meta">
                        <span>${escapeHtml(item.change_name || (direction === 'buy' ? '增加' : '减少'))} ${escapeHtml(formatWanShare(item.shares_change))}</span>
                        <span>${escapeHtml(item.report_period || '--')}</span>
                    </div>
                </a>`;
            }).join('')}
        </div>
    </div>`;
}

function renderNationalTeamHoldingValues(holding) {
    const items = (holding.items || []).slice(0, 10);
    const maxAmount = items.reduce((max, item) => Math.max(max, Number(item.market_value || 0)), 0);
    if (!items.length) {
        return `<div class="holding-value-box">
            <div class="holding-value-title"><span>持股金额前10</span><b>0</b></div>
            <div class="detail-empty">暂无可查持股金额披露</div>
        </div>`;
    }
    return `<div class="holding-value-box">
        <div class="holding-value-title"><span>持股金额前10</span><b>${items.length}</b></div>
        <div class="holding-value-list">
            ${items.map(item => {
                const amount = Number(item.market_value || 0);
                const width = maxAmount > 0 ? Math.max(4, amount / maxAmount * 100) : 0;
                return `<a class="holding-value-row" href="${escapeHtml(item.source_url || '#')}" target="_blank" rel="noopener" title="${escapeHtml((item.shareholder_names || []).join(' / '))}">
                    <div class="holding-value-main">
                        <span>${escapeHtml(item.stock_name || '--')} <small>${escapeHtml(item.stock_code || '')}</small></span>
                        <em>${formatYi(amount)}</em>
                    </div>
                    <div class="holding-value-track"><i style="--bar-width:${width.toFixed(1)}%"></i></div>
                    <div class="holding-value-meta">
                        <span>${escapeHtml(item.holder_count || 0)}个账户</span>
                        <span>${escapeHtml(item.report_period || '--')}</span>
                    </div>
                </a>`;
            }).join('')}
        </div>
    </div>`;
}

function renderNationalTeamHoldings(data) {
    const body = document.getElementById('ntHoldingBody');
    const items = data.items || [];
    if (!items.length) {
        body.innerHTML = '<tr><td colspan="14" class="empty-cell">暂无可查持仓披露。请点击「刷新公开源」或等待每日任务刷新。</td></tr>';
        return;
    }
    body.innerHTML = items.map(item => {
        const changeClass = String(item.change_name || '').includes('减少') ? 'down-text' : (String(item.change_name || '').includes('增加') ? 'up-text' : '');
        return `<tr>
            <td><span class="stock-code">${escapeHtml(item.stock_code)}</span></td>
            <td>${escapeHtml(item.stock_name || '--')}</td>
            <td><span class="tag tag-active">${escapeHtml(item.entity_name || '--')}</span></td>
            <td class="holder-name">${escapeHtml(item.shareholder_name || '--')}</td>
            <td>${escapeHtml(item.holding_type_name || '--')}</td>
            <td>${escapeHtml(item.report_period || '--')}</td>
            <td>${escapeHtml(item.notice_date || '--')}</td>
            <td>${escapeHtml(item.holder_rank || '--')}</td>
            <td>${formatWanShare(item.shares)}</td>
            <td class="${changeClass}">${escapeHtml(item.change_name || '--')}</td>
            <td>${formatMaybePct(item.change_ratio, 2)}</td>
            <td>${formatYi(item.market_value)}</td>
            <td><a href="${escapeHtml(item.source_url || '#')}" target="_blank" rel="noopener" class="link">${escapeHtml(item.source || '--')}</a></td>
            <td>${escapeHtml(formatDateTimeShort(item.fetched_at))}</td>
        </tr>`;
    }).join('');
}

function renderNationalTeamEvents(events, append = false) {
    const list = document.getElementById('ntEventsList');
    if (!list) return;
    if (!events.length && !append) {
        list.innerHTML = '<p class="empty-state">未发现新的可查事件。十大股东数据以公开披露为准。</p>';
        return;
    }
    if (!events.length) return;
    const html = events.map(event => {
        const sourceName = event.source || '--';
        const sourceUrl = event.source_url || '';
        return `
        <div class="national-event">
            <div class="event-date">${escapeHtml(event.event_date || '--')}</div>
            <div class="event-main">
                <div class="event-title">${escapeHtml(event.title || '--')}</div>
                ${event.summary ? `<div class="event-summary">${escapeHtml(event.summary)}</div>` : ''}
                <div class="event-stock">${escapeHtml(event.related_stock_name || '--')} ${escapeHtml(event.related_stock_code || '')}</div>
                <div class="event-source">
                    <span>来源：${escapeHtml(sourceName)}</span>
                    ${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener" class="event-source-link">官网查看</a>` : ''}
                </div>
            </div>
        </div>`;
    }).join('');
    if (append) {
        list.insertAdjacentHTML('beforeend', html);
    } else {
        list.innerHTML = html;
    }
}

function updateNationalTeamEventMore() {
    const button = document.getElementById('ntEventLoadMore');
    if (!button) return;
    const hasMore = ntEventsOffset < ntEventsTotal;
    button.hidden = !hasMore;
    button.disabled = ntEventsLoading || !hasMore;
    button.textContent = ntEventsLoading ? '加载中...' : '加载更多';
}

async function loadMoreNationalTeamEvents() {
    if (ntEventsLoading || ntEventsOffset >= ntEventsTotal) return;
    ntEventsLoading = true;
    updateNationalTeamEventMore();
    try {
        const resp = await apiFetch(buildNationalTeamEventPath());
        if (!resp.ok) throw new Error('国家队事件加载失败');
        const events = await resp.json();
        const items = events.items || [];
        ntEventsTotal = Number(events.total || ntEventsTotal);
        ntEventsOffset += items.length;
        renderNationalTeamEvents(items, true);
    } catch (e) {
        const button = document.getElementById('ntEventLoadMore');
        if (button) {
            button.hidden = false;
            button.disabled = false;
            button.textContent = '加载失败，点击重试';
        }
        setInlineStatus('ntStatus', e?.message || '国家队事件加载失败', 'error');
    } finally {
        ntEventsLoading = false;
        updateNationalTeamEventMore();
    }
}

async function refreshNationalTeam(event) {
    const btn = event?.currentTarget;
    const oldText = btn?.textContent;
    if (btn) {
        btn.disabled = true;
        btn.textContent = '刷新中...';
    }
    setInlineStatus('ntStatus', '正在请求东方财富公开源...');
    try {
        const resp = await apiFetch('/national-team/refresh', { method: 'POST', timeout: 120000, retries: 0 });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail?.source_status?.errors?.[0] || data.detail || '刷新失败');
        setInlineStatus('ntStatus', `刷新完成：持仓 ${data.holding_count || 0} 条，变动 ${data.change_count || 0} 条`, 'ok');
        await loadNationalTeam(1);
    } catch (e) {
        setInlineStatus('ntStatus', e?.message || '刷新失败，请稍后重试', 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = oldText;
        }
    }
}

function scrollNationalTeamTable(direction) {
    const scroller = document.getElementById('ntTableScroll');
    if (!scroller) return;
    scroller.scrollBy({ left: direction * Math.max(360, scroller.clientWidth * 0.75), behavior: 'smooth' });
}

function formatWanShare(value) {
    return isRealNumber(value) ? `${(Number(value) / 10000).toFixed(2)}万` : '--';
}

function formatYi(value) {
    return isRealNumber(value) ? `${(Number(value) / 1e8).toFixed(2)}亿` : '--';
}

// ---- Stock Detail Modal ----
let _detailCharts = {};

function showStockDetail(code) {
    const modal = document.getElementById('stockDetailModal');
    if (!modal) return;
    modal.classList.add('open');
    resetDetailChartPanels();
    document.getElementById('modalStockName').textContent = '加载中...';
    document.getElementById('modalStockCode').textContent = code;
    document.getElementById('modalMeta').innerHTML = '';
    document.getElementById('modalDataSources').innerHTML = '';

    // 标记所有图表为加载中
    ['chartPrice','chartVolume','chartMA','chartChange','chartDrawdownDist','chartTurnover','chartVolRatio','chartPE','chartSeal'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('loading');
    });
    const timeline = document.getElementById('chartLimitTimeline');
    if (timeline) timeline.innerHTML = '<div class="detail-empty">加载中...</div>';

    loadStockDetailData(code);
}

function resetDetailChartPanels() {
    document.querySelectorAll('#stockDetailModal .chart-panel').forEach(panel => {
        panel.style.display = '';
    });
    document.querySelectorAll('#stockDetailModal .chart-row').forEach(row => {
        row.style.display = '';
    });
}

function setDetailChartPanelVisible(id, visible) {
    const el = document.getElementById(id);
    if (!el) return;
    const panel = el.closest('.chart-panel');
    if (panel) panel.style.display = visible ? '' : 'none';
    const row = panel?.closest('.chart-row');
    if (row) {
        const panels = Array.from(row.querySelectorAll('.chart-panel'));
        row.style.display = panels.length && panels.every(item => item.style.display === 'none') ? 'none' : '';
    }
}

function closeStockDetail() {
    const modal = document.getElementById('stockDetailModal');
    if (modal) modal.classList.remove('open');
    // 销毁所有图表实例释放内存
    Object.values(_detailCharts).forEach(c => { try { c.dispose(); } catch(e) {} });
    _detailCharts = {};
}

async function loadStockDetailData(code) {
    try {
        const resp = await apiFetch(`/watchlist/${code}/detail`, { timeout: 120000 });
        const data = await resp.json();
        if (resp.status === 404) {
            document.getElementById('modalStockName').textContent = '未找到该股票';
            return;
        }
        renderStockDetail(data);
    } catch (e) {
        document.getElementById('modalStockName').textContent = '数据加载失败';
        console.error('Stock detail error:', e);
    }
}

function renderStockDetail(data) {
    // 标题区
    document.getElementById('modalStockName').textContent = data.name;
    document.getElementById('modalStockCode').textContent = data.code;
    const dp = data.drop_pct;
    const dpClass = isRealNumber(dp) ? (Number(dp) < 0 ? 'down' : 'up') : '';
    const dpVal = formatMaybePct(dp, 2);
    document.getElementById('modalMeta').innerHTML =
        `<span>涨停日期: <b class="val">${data.zt_date}</b></span>` +
        `<span>参考价: <b class="val">${formatNumber(data.ref_price, 2)}</b></span>` +
        `<span>现价: <b class="val">${formatNumber(data.current_price, 2)}</b></span>` +
        `<span>回撤: <b class="val ${dpClass}">${dpVal}</b></span>` +
        `<span>封板时间: <b class="val">${formatSealTimeForDetail(data.seal_time)}</b></span>` +
        `<span>连板数: <b class="val">${data.consecutive || '--'}</b></span>` +
        `<span>加入时间: <b class="val">${data.added_date || '--'}</b></span>`;
    renderDetailSources(data);

    // 销毁旧图表
    Object.values(_detailCharts).forEach(c => { try { c.dispose(); } catch(e) {} });
    _detailCharts = {};

    const cd = data.chart_data || {};
    const klineText = sourceEmptyText(data.data_sources?.kline, '历史K线数据');
    if (!cd.dates?.length || !hasFiniteSeries(cd.closes)) {
        ['chartPrice','chartVolume','chartMA','chartChange','chartDrawdownDist'].forEach(id => {
            emptyChart(document.getElementById(id), klineText);
        });
        renderSupplementCharts(data);
        return;
    }

    // 清除loading状态
    ['chartPrice','chartVolume','chartMA','chartChange','chartDrawdownDist'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('loading');
    });

    const chartTheme = getEChartsLightTheme();

    // 1. 价格走势 + 回撤双轴图
    _detailCharts.price = echarts.init(document.getElementById('chartPrice'), chartTheme);
    _detailCharts.price.setOption(mergeChartOption({
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'cross', crossStyle: { color: '#999' } },
        },
        legend: chartLegend(['收盘价','回撤%','参考价']),
        grid: chartGrid({ right: 56, top: 44 }),
        xAxis: detailCategoryAxis(cd.dates),
        yAxis: [
            {
                type: 'value',
            },
            {
                type: 'value',
                axisLabel: { formatter: '{value}%', margin: 8 },
                splitLine: { show: false },
            }
        ],
        series: [
            {
                name: '收盘价', type: 'line', data: cd.closes,
                smooth: true, symbol: 'none',
                lineStyle: { color: '#006fd6', width: 2 },
            },
            {
                name: '参考价', type: 'line', yAxisIndex: 0,
                markLine: { silent: true, symbol: 'none',
                    lineStyle: { color: '#9aa0a6', type: 'dashed', width: 1.2 },
                    label: { show: false },
                    data: [{ yAxis: data.ref_price }] },
                data: []
            },
            {
                name: '回撤%', type: 'line', yAxisIndex: 1, data: cd.drawdowns,
                smooth: true, symbol: 'none',
                lineStyle: { color: '#6375ff', width: 1.5 },
            }
        ]
    }));

    // 2. 成交量图
    _detailCharts.volume = echarts.init(document.getElementById('chartVolume'), chartTheme);
    _detailCharts.volume.setOption(mergeChartOption({
        tooltip: { trigger: 'axis' },
        grid: chartGrid({ top: 28 }),
        xAxis: detailCategoryAxis(cd.dates),
        yAxis: { type: 'value', axisLabel: { formatter: v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e4 ? (v/1e4).toFixed(0)+'万' : v } },
        series: [{ type: 'bar', data: cd.volumes,
            barWidth: 12,
            itemStyle: { color: params => cd.changes && cd.changes[params.dataIndex] >= 0 ? 'rgba(245,34,45,0.72)' : 'rgba(82,196,26,0.72)' } }]
    }));

    // 3. 均线图
    _detailCharts.ma = echarts.init(document.getElementById('chartMA'), chartTheme);
    const maSeries = [
        { name: '收盘价', type: 'line', data: cd.closes, smooth: true, symbol: 'none', lineStyle: { color: '#9aa0a6', width: 1, type: 'dotted' } }
    ];
    const maColors = { 'MA5': '#006fd6', 'MA10': '#24b8ef', 'MA20': '#28c7c9', 'MA60': '#27d6a3' };
    ['ma5','ma10','ma20','ma60'].forEach((k, i) => {
        if (cd[k] && cd[k].length > 0) {
            const name = 'MA' + [5,10,20,60][i];
            maSeries.push({ name, type: 'line', data: cd[k], smooth: true, symbol: 'none', lineStyle: { color: maColors[name], width: 1.5 } });
        }
    });
    _detailCharts.ma.setOption(mergeChartOption({
        tooltip: { trigger: 'axis' },
        legend: chartLegend(maSeries.map(s => s.name)),
        grid: chartGrid({ top: 44 }),
        xAxis: detailCategoryAxis(cd.dates),
        yAxis: { type: 'value' },
        series: maSeries
    }));

    // 4. 每日涨跌幅
    if (cd.changes && cd.changes.length > 0) {
        _detailCharts.change = echarts.init(document.getElementById('chartChange'), chartTheme);
        _detailCharts.change.setOption(mergeChartOption({
            tooltip: { trigger: 'axis' },
            grid: chartGrid({ top: 28 }),
            xAxis: detailCategoryAxis(cd.dates),
            yAxis: { type: 'value', axisLabel: { formatter: '{value}%' } },
            series: [{ type: 'bar', data: cd.changes,
                barWidth: 12,
                itemStyle: { color: params => params.value >= 0 ? '#f5222d' : '#7ed367' } }]
        }));
    } else {
        emptyChart(document.getElementById('chartChange'), '接口未返回涨跌幅序列');
    }

    // 5. 回撤分布横向柱图
    const dist = cd.drawdown_distribution;
    if (dist && Object.keys(dist).length > 0) {
        _detailCharts.dist = echarts.init(document.getElementById('chartDrawdownDist'), chartTheme);
        const distEntries = Object.entries(dist);
        _detailCharts.dist.setOption(mergeChartOption({
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            grid: { left: 86, right: 38, top: 22, bottom: 18, containLabel: true },
            xAxis: { type: 'value', show: false },
            yAxis: { type: 'category', data: distEntries.map(([k]) => k), axisLabel: { color: '#666', fontSize: 11 }, axisTick: { show: false }, axisLine: { show: false } },
            series: [{ type: 'bar', data: distEntries.map(([,v]) => v), barWidth: 12, itemStyle: { color: params => ['#2d88d8','#29b6df','#25c9c8','#28d69f','#6273f5'][params.dataIndex % 5], borderRadius: 6 }, label: { show: true, position: 'right', formatter: '{c}天', color: '#202124', fontSize: 11 } }]
        }));
    } else {
        emptyChart(document.getElementById('chartDrawdownDist'), '接口未返回回撤分布');
    }

    renderSupplementCharts(data);
}

function formatSealTimeForDetail(val) {
    if (!val || val === '0' || val === 0) return '--';
    const s = String(val);
    try {
        const num = parseInt(s);
        if (num <= 0) return '--';
        const t = String(num).padStart(6, '0');
        return t.substring(0,2) + ':' + t.substring(2,4);
    } catch(e) { return '--'; }
}

function getEChartsLightTheme() {
    return {
        backgroundColor: 'transparent',
        textStyle: { color: '#666' },
    };
}

function chartGrid(overrides = {}) {
    return { top: 34, right: 22, bottom: 34, left: 42, containLabel: true, ...overrides };
}

function chartLegend(data, overrides = {}) {
    return {
        data,
        top: 2,
        left: 0,
        itemWidth: 18,
        itemHeight: 8,
        icon: 'circle',
        textStyle: { color: '#666', fontSize: 11, lineHeight: 14 },
        ...overrides
    };
}

function detailCategoryAxis(dates) {
    const values = Array.isArray(dates) ? dates : [];
    const interval = values.length > 18 ? Math.ceil(values.length / 12) - 1 : 0;
    return {
        type: 'category',
        data: values,
        axisLabel: {
            interval: Math.max(0, interval),
            rotate: values.length > 18 ? 40 : 0,
            formatter: value => formatDateShort(value),
            margin: 10,
        },
    };
}

function mergeChartOption(opt) {
    return {
        backgroundColor: 'transparent',
        textStyle: { fontFamily: 'PingFang SC, Microsoft YaHei, sans-serif', color: '#666' },
        ...opt,
        grid: { top: 16, right: 16, bottom: 24, left: 48, ...(opt.grid || {}) },
        tooltip: {
            backgroundColor: '#fff',
            borderColor: 'rgba(0,0,0,0.07)',
            borderWidth: 1,
            borderRadius: 6,
            padding: [8,12],
            textStyle: { color: 'rgba(0,0,0,0.85)', fontSize: 12 },
            ...(opt.tooltip || {})
        },
        xAxis: normalizeAxis(opt.xAxis, true),
        yAxis: Array.isArray(opt.yAxis) ? opt.yAxis.map(axis => normalizeAxis(axis, false)) : normalizeAxis(opt.yAxis, false),
    };
}

function normalizeAxis(axis = {}, isX) {
    return {
        ...axis,
        axisLine: { lineStyle: { color: 'rgba(0,0,0,0.12)' }, ...(axis.axisLine || {}) },
        axisTick: { show: false, ...(axis.axisTick || {}) },
        axisLabel: { color: 'rgba(0,0,0,0.45)', fontSize: 11, ...(axis.axisLabel || {}) },
        splitLine: isX ? { show: false, ...(axis.splitLine || {}) } : { lineStyle: { color: 'rgba(0,0,0,0.06)', type: 'dashed' }, ...(axis.splitLine || {}) },
    };
}

function renderSupplementCharts(data) {
    const cd = data.chart_data || {};
    const sources = data.data_sources || {};
    renderMetricLineChart('chartTurnover', 'turnover', cd, '换手率 %', '#149b9a', sourceEmptyText(sources.turnover, '换手率历史序列'));
    renderMetricLineChart('chartVolRatio', 'vol_ratio', cd, '量比', '#006fd6', sourceEmptyText(sources.vol_ratio, '量比历史序列'));
    renderMetricLineChart('chartPE', 'pe', cd, 'PE', '#4e6ef2', sourceEmptyText(sources.pe, 'PE 历史序列'));
    renderSealTrend(cd, sources.seal_time);
    renderLimitTimeline(data);
}

function renderDetailSources(data) {
    const el = document.getElementById('modalDataSources');
    if (!el) return;
    const sources = data.data_sources || {};
    const rows = [
        ['K线', sources.kline],
        ['实时行情', sources.quote],
        ['换手率', sources.turnover],
        ['量比', sources.vol_ratio],
        ['PE', sources.pe],
        ['封板/涨停', sources.limit_events],
    ].filter(([, item]) => item && item.available && item.source);
    el.innerHTML = rows.map(([label, item]) => {
        const source = item.source;
        const note = item?.note || '';
        return `<span class="ok" title="${escapeHtml(note)}">${escapeHtml(label)}: ${escapeHtml(source)}</span>`;
    }).join('');
}

function sourceEmptyText(source, label) {
    if (!source) return `接口未返回${label}`;
    return `${source.note || `接口未返回${label}`}${source.source ? `（来源: ${source.source}）` : ''}`;
}

function hasFiniteSeries(values) {
    return Array.isArray(values) && values.some(value => isRealNumber(value));
}

function renderMetricLineChart(id, key, cd, name, color, emptyText) {
    const el = document.getElementById(id);
    const values = cd[key] || cd[`${key}s`] || [];
    if (!el || !values.length || !cd.dates?.length || !hasFiniteSeries(values)) {
        if (el) {
            el.innerHTML = '';
            el.classList.remove('loading');
        }
        setDetailChartPanelVisible(id, false);
        return;
    }
    setDetailChartPanelVisible(id, true);
    el.innerHTML = '';
    el.classList.remove('loading');
    const chart = echarts.init(el, getEChartsLightTheme());
    _detailCharts[id] = chart;
    chart.setOption(mergeChartOption({
        tooltip: { trigger: 'axis' },
        grid: chartGrid({ left: 38, right: 18, top: 22, bottom: 30 }),
        xAxis: detailCategoryAxis(cd.dates),
        yAxis: { type: 'value' },
        series: [{ type: 'line', name, data: values, symbolSize: 5, lineStyle: { color, width: 2 }, itemStyle: { color }, smooth: false }]
    }));
}

function renderSealTrend(cd, source) {
    const el = document.getElementById('chartSeal');
    const values = cd.seal_times || [];
    const finiteCount = Array.isArray(values)
        ? values.filter(value => isRealNumber(value)).length
        : 0;
    if (!el || !values.length || !cd.dates?.length || finiteCount < 1) {
        if (el) {
            el.innerHTML = '';
            el.classList.remove('loading');
        }
        setDetailChartPanelVisible('chartSeal', false);
        return;
    }
    setDetailChartPanelVisible('chartSeal', true);
    el.innerHTML = '';
    el.classList.remove('loading');
    const chart = echarts.init(el, getEChartsLightTheme());
    _detailCharts.seal = chart;
    chart.setOption(mergeChartOption({
        tooltip: { trigger: 'axis', valueFormatter: v => formatSealTimeForDetail(v) },
        grid: chartGrid({ left: 40, right: 18, top: 22, bottom: 30 }),
        xAxis: detailCategoryAxis(cd.dates),
        yAxis: { type: 'value', inverse: true, axisLabel: { formatter: v => formatSealTimeForDetail(v) } },
        series: [{ type: 'line', name: '封板时间', data: values, symbolSize: 5, lineStyle: { color: '#f5a400', width: 2 }, itemStyle: { color: '#f5a400' } }]
    }));
}

function renderLimitTimeline(data) {
    const el = document.getElementById('chartLimitTimeline');
    if (!el) return;
    const cd = data.chart_data || {};
    let events = cd.limit_events || data.limit_events || [];
    if (!events.length && data.zt_date) {
        events = [{
            date: data.zt_date,
            seal_time: data.seal_time,
            label: data.consecutive && data.consecutive !== '0' ? `${data.consecutive}板` : '涨停'
        }];
    }
    if (!events.length) {
        emptyChart(el, '接口未返回历史涨停日期序列');
        return;
    }
    el.classList.remove('loading');
    const items = events.slice(-5);
    const repeat = items.length === 1 ? ' single' : '';
    el.innerHTML = `<div class="timeline-track">${items.map((item, i) => {
        const colors = ['#f5222d','#f5222d','#f5a400','#1890ff','#4e6ef2'];
        return `<div class="timeline-item">
            <span class="timeline-dot" style="background:${colors[i % colors.length]}">${i + 1}</span>
            <span class="timeline-date">${escapeHtml(formatDateShort(item.date || item.zt_date))}</span>
            <span class="timeline-time">${escapeHtml(formatSealTimeForDetail(item.seal_time))}</span>
            <span class="timeline-tag">${escapeHtml(item.label || item.tag || `${item.board || item.consecutive || 1}板`)}</span>
        </div>`;
    }).join('')}</div>`;
    const track = el.querySelector('.timeline-track');
    if (track) track.className += repeat;
}

// ---- Recommendations ----
async function loadRecommendations() {
    const level = document.getElementById('recLevel')?.value || '';
    recommendationCurrentLevel = level;
    document.getElementById('recList').innerHTML = '<p class="empty-state">加载中...</p>';
    document.getElementById('recStats').innerHTML = '';

    try {
        const resp = await apiFetch('/screening/latest');
        const data = await resp.json();
        recommendationLastData = data;
        if (data.date) {
            const dateInput = document.getElementById('recDate');
            if (dateInput && !dateInput.value) dateInput.value = data.date;
        }
        if (data.results && data.results.length > 0) {
            const visible = data.results.filter(r => ['STRONG_BUY', 'BUY', 'WATCH'].includes(r.recommendation));
            renderRecStats(data);
            recommendationEnrichedResults = await enrichRecommendationResults(visible);
            renderRecommendationView();
            return;
        }
        setTopbarMeta('recommendations', `筛选日期: ${data.date || '--'}  |  审核通过率: 0%`);
        recommendationEnrichedResults = [];
        // 无筛选结果
        document.getElementById('recList').innerHTML = '<p class="empty-state">暂无推荐数据。每日15:10自动筛选，请耐心等待。</p>';
        document.getElementById('recStats').innerHTML = `
            <div class="metric-cards" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">
                <div class="metric-card"><div class="metric-label">总推荐数</div><div class="metric-value">0</div></div>
                <div class="metric-card"><div class="metric-label">STRONG BUY</div><div class="metric-value">0</div></div>
                <div class="metric-card"><div class="metric-label">BUY</div><div class="metric-value">0</div></div>
                <div class="metric-card"><div class="metric-label">WATCH</div><div class="metric-value">0</div></div>
            </div>`;
    } catch(e) {
        document.getElementById('recList').innerHTML =
            '<p class="empty-state" style="color:#e60012">API 连接失败，后端可能正在启动中（冷启动约30-60秒）'
            + ' <a href="#" onclick="refreshData();return false" style="color:#3B82F6">点击重试</a></p>';
        document.getElementById('recStats').innerHTML = '';
    }
}

async function enrichRecommendationResults(stocks) {
    if (!stocks.length) return [];
    const enriched = await Promise.all(stocks.map(async (stock) => {
        const item = { ...stock };
        const detail = await fetchRecommendationDetail(item.code);
        if (detail) {
            item.detail_source = '/api/v1/watchlist/{code}/detail';
            item.price_history = item.price_history?.length ? item.price_history : buildPriceHistoryFromDetail(detail);
            const events = detail.chart_data?.limit_events || detail.limit_events || [];
            if (events.length) item.limit_events = events;
            item.added_date = item.added_date || detail.added_date;
            const detailCount = detail.follow_limit_up_count ?? detail.zt_count;
            if (isRealNumber(detailCount)) {
                if (!isRealNumber(item.follow_limit_up_count) || Number(detailCount) > Number(item.follow_limit_up_count)) {
                    item.follow_limit_up_count = detailCount;
                }
                if (!isRealNumber(item.zt_count) || Number(detailCount) > Number(item.zt_count)) {
                    item.zt_count = detailCount;
                }
            }
            item.seal_time = item.seal_time || detail.seal_time;
            item.consecutive = item.consecutive || detail.consecutive;
            if (!isRealNumber(item.current_price) && isRealNumber(detail.current_price)) item.current_price = detail.current_price;
            if (!isRealNumber(item.drop_pct) && isRealNumber(detail.drop_pct)) item.drop_pct = detail.drop_pct;
        }

        const watch = await fetchRecommendationWatchlistRow(item.code);
        if (watch) {
            item.watchlist_source = '/api/v1/watchlist?search=' + encodeURIComponent(item.code);
            item.added_date = item.added_date || watch.added_date;
            item.zt_count = item.zt_count ?? watch.zt_count;
            item.follow_limit_up_count = item.follow_limit_up_count ?? watch.zt_count;
            item.seal_time = item.seal_time || watch.seal_time;
            item.consecutive = item.consecutive || watch.consecutive;
            item.break_count = item.break_count ?? watch.break_count;
            if (!isRealNumber(item.current_price) && isRealNumber(watch.current_price)) item.current_price = watch.current_price;
            if (!isRealNumber(item.drop_pct) && isRealNumber(watch.drop_pct)) item.drop_pct = watch.drop_pct;
        }

        if (!item.limit_events?.length && item.zt_date) {
            item.limit_events = [{
                date: item.zt_date,
                seal_time: item.seal_time,
                label: item.consecutive && item.consecutive !== '0' ? `${item.consecutive}板` : '涨停',
            }];
        }
        return item;
    }));
    return enriched;
}

async function fetchRecommendationDetail(code) {
    if (!code) return null;
    try {
        const resp = await apiFetch(`/watchlist/${encodeURIComponent(code)}/detail`, { timeout: 120000, retries: 0 });
        if (!resp.ok) return null;
        return await resp.json();
    } catch (e) {
        return null;
    }
}

async function fetchRecommendationWatchlistRow(code) {
    if (!code) return null;
    try {
        const params = new URLSearchParams({ search: code, size: '1' });
        const resp = await apiFetch(`/watchlist?${params.toString()}`, { timeout: 30000, retries: 0 });
        if (!resp.ok) return null;
        const data = await resp.json();
        return (data.items || [])[0] || null;
    } catch (e) {
        return null;
    }
}

function buildPriceHistoryFromDetail(detail) {
    const cd = detail?.chart_data || {};
    const dates = cd.dates || [];
    const closes = cd.closes || [];
    if (!dates.length || !closes.length) return [];
    return dates.map((date, index) => ({
        date,
        close: closes[index],
        change_pct: cd.changes?.[index],
        drawdown_pct: cd.drawdowns?.[index],
    })).filter(row => Number.isFinite(Number(row.close))).slice(-12);
}

function setRecommendationLevel(level, reload = false) {
    recommendationCurrentLevel = level || '';
    const select = document.getElementById('recLevel');
    if (select && select.value !== recommendationCurrentLevel) select.value = recommendationCurrentLevel;
    if (reload || !recommendationLastData) loadRecommendations();
    else renderRecommendationView();
}

function renderRecommendationView() {
    const level = recommendationCurrentLevel;
    syncRecommendationStatsActive(level);
    const filtered = level
        ? recommendationEnrichedResults.filter(r => r.recommendation === level)
        : recommendationEnrichedResults;
    if (filtered.length > 0) {
        renderRecommendationCards(filtered);
    } else {
        const label = level || '全部等级';
        document.getElementById('recList').innerHTML = `<p class="empty-state">${escapeHtml(label)}暂无推荐结果</p>`;
    }
}

function syncRecommendationStatsActive(level = recommendationCurrentLevel) {
    document.querySelectorAll('#recStats .metric-card[data-rec-level]').forEach(card => {
        card.classList.toggle('active', card.dataset.recLevel === level);
    });
}

function renderRecStats(data) {
    const total = data.total_scored || 0;
    const approved = (data.strong_buy || 0) + (data.buy || 0) + (data.watch || 0);
    const passRate = total ? Math.round(approved / total * 100) : 0;
    setTopbarMeta('recommendations', `筛选日期: ${data.date || '--'}  |  审核通过率: ${passRate}%`);
    document.getElementById('recStats').innerHTML = `
        <div class="metric-cards" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">
            <button type="button" class="metric-card rec-stat-card" data-rec-level="STRONG_BUY" onclick="setRecommendationLevel('STRONG_BUY')"><div class="metric-label">STRONG BUY</div><div class="metric-value up">${data.strong_buy||0}</div></button>
            <button type="button" class="metric-card rec-stat-card" data-rec-level="BUY" onclick="setRecommendationLevel('BUY')"><div class="metric-label">BUY</div><div class="metric-value" style="color:#F39C12">${data.buy||0}</div></button>
            <button type="button" class="metric-card rec-stat-card" data-rec-level="WATCH" onclick="setRecommendationLevel('WATCH')"><div class="metric-label">WATCH</div><div class="metric-value" style="color:#2932e1">${data.watch||0}</div></button>
            <button type="button" class="metric-card rec-stat-card" data-rec-level="" onclick="setRecommendationLevel('')"><div class="metric-label">总计评分</div><div class="metric-value">${total}只</div><div class="metric-foot">审核降级后: ${approved}只</div></button>
        </div>`;
    syncRecommendationStatsActive();
}

function renderRecommendationCards(stocks) {
    disposeRecommendationCharts();
    const names = {STRONG_BUY:'STRONG BUY',BUY:'BUY',WATCH:'WATCH',PASS:'PASS'};
    const colors = {STRONG_BUY:'#e60012',BUY:'#F39C12',WATCH:'#2932e1',PASS:'#999999'};
    const keys = ['pullback','volume_trend','ma_alignment','strength','entry_point','market_cap','volume_ratio','turnover','pe','zt_quality'];
    const el = document.getElementById('recList');
    const cards = stocks.map(s => {
        let dots = '';
        if (s.factors) keys.forEach(k => {
            const f = s.factors[k]; if (!f) return;
            dots += `<span class="factor-chip ${f.passed?'pass':'fail'}" title="${escapeHtml(f.name)}: ${escapeHtml(f.detail)} (${f.score}/10)">${escapeHtml(f.name)} ${f.score}/10</span>`;
        });
        const auditHtml = renderAuditFailures(s.audit);
        const chartHtml = renderRecommendationTrendChart(s);
        const rec = s.recommendation || 'WATCH';
        const label = names[rec] || rec;
        const actionLabel = rec === 'STRONG_BUY' ? 'STRONG_BUY' : label;
        const score = isRealNumber(s.adjusted_score) ? Math.round(Number(s.adjusted_score)) : '--';
        const followCount = formatCountValue(s.follow_limit_up_count ?? s.zt_count);
        const addedDate = s.added_date || '--';
        const sourceText = s.watchlist_source ? '来源: 监控列表接口' : '来源: 筛选报告';
        return `<div class="rec-card">
            <div class="rec-card-header">
                <div class="rec-rank">#${escapeHtml(s.rank || '--')}</div>
                <div class="rec-stock-main"><span class="rec-stock-name">${escapeHtml(s.name)}</span> <span class="stock-code" style="font-size:15px">${escapeHtml(s.code)}</span></div>
                <div style="flex:1"></div>
                <div class="rec-stars">${'\u2605'.repeat(Math.min(4,Math.max(1,Math.round(toNumber(s.adjusted_score, 0)/25))))}</div>
                <span class="rec-score" style="color:${colors[rec]||'#999999'}">${s.adjusted_score}</span>
                <span class="tag tag-${rec.toLowerCase()}">${label}</span>
                <button class="btn rec-action" onclick="observeStock('${jsString(s.code)}','${jsString(s.name)}','${jsString(actionLabel)}')">${actionLabel} 观察</button>
            </div>
            <div class="rec-meta">
                <span>评分:${score}分</span>
                <span>回撤:<b class="${valueClass(s.drop_pct)}">${formatMaybePct(s.drop_pct, 2)}</b></span>
                <span>涨停日: ${escapeHtml(s.zt_date)}</span>
                <span>关注日:${escapeHtml(addedDate)}</span>
                <span>关注至今涨停:<b>${followCount}</b></span>
                <span>参考价:${formatNumber(s.ref_price, 2)}</span>
                <span>现价:${formatNumber(s.current_price, 2)}</span>
            </div>
            <div class="rec-source-line">${escapeHtml(sourceText)} · ${escapeHtml(s.detail_source ? 'K线来自详情接口' : 'K线来自筛选报告')}</div>
            ${auditHtml}
            <div class="rec-factor-list">${dots}</div>
            ${chartHtml}
        </div>`;
    }).join('');
    el.innerHTML = `<div class="rec-list-heading">WATCH LIST <span>${stocks.length} 条结果</span></div>${cards}`;
    renderRecommendationCharts(stocks);
    requestAnimationFrame(() => scheduleChartResize());
}

function disposeRecommendationCharts() {
    Object.values(recommendationTrendCharts).forEach(chart => disposeChart(chart));
    recommendationTrendCharts = {};
}

function renderRecommendationCharts(stocks) {
    const chartTheme = getEChartsLightTheme();
    stocks.forEach(stock => {
        const chartId = makeChartId('recChart', stock.code);
        const el = document.getElementById(chartId);
        if (!el) return;
        const rows = (stock.price_history || [])
            .slice(-12)
            .filter(row => Number.isFinite(Number(row.close)));
        if (!rows.length) return;
        const refPrice = toNumber(stock.ref_price, 0);
        const drawVals = rows.map(row => toNumber(row.drawdown_pct, 0));
        const closeVals = rows.map(row => Number(row.close));
        if (refPrice > 0) closeVals.push(refPrice);
        let minClose = Math.min(...closeVals);
        let maxClose = Math.max(...closeVals);
        if (minClose === maxClose) {
            minClose -= 1;
            maxClose += 1;
        }
        const closePad = (maxClose - minClose) * 0.12;
        minClose -= closePad;
        maxClose += closePad;

        let minDraw = Math.min(...drawVals);
        let maxDraw = Math.max(...drawVals);
        if (minDraw === maxDraw) {
            minDraw -= 1;
            maxDraw += 1;
        }
        const drawPad = Math.max((maxDraw - minDraw) * 0.12, 0.5);
        minDraw -= drawPad;
        maxDraw += drawPad;

        const chart = echarts.init(el, chartTheme, { renderer: 'canvas' });
        recommendationTrendCharts[stock.code] = chart;
        const events = (stock.limit_events || []).slice(-6);
        const xAxis = detailCategoryAxis(rows.map(row => row.date));
        xAxis.axisLabel = {
            ...xAxis.axisLabel,
            color: '#5f6673',
            fontSize: 14,
            margin: 14,
        };
        chart.setOption(mergeChartOption({
            animation: false,
            textStyle: { fontFamily: 'PingFang SC, Microsoft YaHei, sans-serif', color: '#5f6673', fontSize: 14 },
            tooltip: {
                trigger: 'axis',
                confine: true,
                axisPointer: {
                    type: 'cross',
                    lineStyle: { color: 'rgba(41,50,225,0.25)' },
                    crossStyle: { color: 'rgba(41,50,225,0.18)' },
                },
                textStyle: { color: 'rgba(0,0,0,0.85)', fontSize: 14, lineHeight: 21 },
            },
            grid: chartGrid({ top: 24, left: 8, right: 8, bottom: 46 }),
            legend: { show: false },
            xAxis,
            yAxis: [
                {
                    type: 'value',
                    min: minClose,
                    max: maxClose,
                    axisLabel: {
                        formatter: v => Number(v).toFixed(2),
                        color: '#5f6673',
                        fontSize: 14,
                    },
                },
                {
                    type: 'value',
                    min: minDraw,
                    max: maxDraw,
                    inverse: true,
                    axisLabel: {
                        formatter: v => `${Number(v).toFixed(1)}%`,
                        color: '#5f6673',
                        fontSize: 14,
                    },
                    splitLine: { show: false },
                }
            ],
            series: [
                {
                    type: 'line',
                    name: '收盘价',
                    data: rows.map(row => Number(row.close)),
                    smooth: true,
                    symbol: 'circle',
                    symbolSize: 9,
                    yAxisIndex: 0,
                    lineStyle: { color: '#2932e1', width: 3.8 },
                    itemStyle: { color: '#2932e1' },
                    emphasis: { focus: 'series' },
                },
                {
                    type: 'line',
                    name: '回撤%',
                    data: rows.map(row => toNumber(row.drawdown_pct, 0)),
                    smooth: true,
                    symbol: 'circle',
                    symbolSize: 8,
                    yAxisIndex: 1,
                    lineStyle: { color: '#f5a400', width: 3.2 },
                    itemStyle: { color: '#f5a400' },
                    emphasis: { focus: 'series' },
                },
                ...(refPrice > 0 ? [{
                    type: 'line',
                    name: '参考价',
                    data: rows.map(() => refPrice),
                    symbol: 'none',
                    yAxisIndex: 0,
                    lineStyle: { color: '#8a9099', width: 2, type: 'dashed' },
                    itemStyle: { color: '#8a9099' },
                }] : []),
                ...(events.length ? [{
                    type: 'scatter',
                    name: '涨停次数',
                    data: events.map(event => {
                        const date = String(event.date || event.zt_date || '');
                        const index = rows.findIndex(row => row.date === date);
                        return index >= 0 ? [date, rows[index].close] : null;
                    }).filter(Boolean),
                    symbolSize: 12,
                    itemStyle: { color: '#f5222d' },
                    emphasis: { scale: 1.4 },
                    label: {
                        show: true,
                        formatter: params => formatDateShort(params.value?.[0]),
                        position: 'top',
                        color: '#f5222d',
                        fontSize: 13,
                        fontWeight: 600,
                    },
                }] : []),
            ],
        }));
    });
}

function formatCountValue(value) {
    return isRealNumber(value) ? `${Number(value)}次` : '--';
}

function renderAuditFailures(audit) {
    if (!audit || !audit.downgraded) return '';
    const failures = (audit.validations || []).filter(v => v.status === 'fail');
    const items = failures.map(v => `<li><b>${escapeHtml(v.name)}</b>: ${escapeHtml(v.detail)}</li>`).join('');
    return `<div class="audit-failures">
        <div>审核降级: ${escapeHtml(audit.original_rec)} → ${escapeHtml(audit.adjusted_rec)}（失败${audit.fail_count || failures.length}项）</div>
        ${items ? `<ul>${items}</ul>` : ''}
    </div>`;
}

function renderRecommendationTrendChart(stock) {
    const rows = (stock.price_history || [])
        .slice(-12)
        .filter(row => Number.isFinite(Number(row.close)));
    const refPrice = toNumber(stock.ref_price, 0);
    const followCount = stock.follow_limit_up_count ?? stock.zt_count;
    const events = (stock.limit_events || []).slice(-6);
    const title = '关注后交易日趋势图';
    const titleSub = `( 价格走势 + 回撤幅度 + 涨停次数${isRealNumber(followCount) ? ` ${Number(followCount)}次` : ''} )`;
    const subParts = [];
    if (refPrice > 0) subParts.push(`参考价${refPrice.toFixed(2)} 以虚线标记`);
    if (isRealNumber(followCount)) subParts.push(`关注至今涨停板次数 ${Number(followCount)} 次`);
    if (events.length) subParts.push(`涨停日期 ${events.map(e => formatDateShort(e.date || e.zt_date)).join('、')}`);
    const sub = subParts.join(' · ') || '接口未返回参考价';
    const eventBar = renderRecommendationLimitEvents(stock, rows);

    if (!rows.length) {
        return `<div class="rec-trend-chart">
            <div class="rec-chart-head">
                <div>
                    <div class="rec-chart-title">${title}<span>${titleSub}</span></div>
                    <div class="rec-chart-sub">${sub}</div>
                </div>
                <div class="rec-chart-legend">
                    <span class="legend-item" style="color:#3d65fe"><span class="legend-dot"></span>收盘价</span>
                    <span class="legend-item" style="color:#faad14"><span class="legend-dot"></span>回撤%</span>
                    <span class="legend-item" style="color:#8a9099"><span class="legend-dot"></span>参考价</span>
                    <span class="legend-item" style="color:#f5222d"><span class="legend-dot"></span>涨停次数</span>
                </div>
            </div>
            <div class="rec-chart-empty">接口未返回价格历史，未使用本地假数据补绘</div>
            ${eventBar}
        </div>`;
    }
    const chartId = makeChartId('recChart', stock.code);
    return `<div class="rec-trend-chart">
        <div class="rec-chart-head">
            <div>
                <div class="rec-chart-title">${title}<span>${titleSub}</span></div>
                <div class="rec-chart-sub">${sub}</div>
            </div>
            <div class="rec-chart-legend">
                <span class="legend-item" style="color:#3d65fe"><span class="legend-dot"></span>收盘价</span>
                <span class="legend-item" style="color:#faad14"><span class="legend-dot"></span>回撤%</span>
                <span class="legend-item" style="color:#8a9099"><span class="legend-dot"></span>参考价${refPrice > 0 ? refPrice.toFixed(2) : ''}</span>
                <span class="legend-item" style="color:#f5222d"><span class="legend-dot"></span>涨停次数${isRealNumber(followCount) ? Number(followCount) : '--'}次</span>
            </div>
        </div>
        <div class="rec-chart-canvas" id="${chartId}" role="img" aria-label="${escapeHtml(stock.name)}价格与回撤趋势"></div>
        ${eventBar}
    </div>`;
}

function renderRecommendationLimitEvents(stock, rows = []) {
    const followCount = stock.follow_limit_up_count ?? stock.zt_count;
    const events = (stock.limit_events || []).slice(-6);
    if (!isRealNumber(followCount) && !events.length) return '';
    const eventDates = events.length
        ? events.map(event => `<span>${escapeHtml(formatDateShort(event.date || event.zt_date))}<b>${escapeHtml(event.label || '涨停')}</b></span>`).join('')
        : '<span>接口未返回涨停日期序列</span>';
    const count = isRealNumber(followCount) ? Number(followCount) : events.length;
    return `<div class="rec-limit-events">
        <div class="rec-limit-count"><strong>${count}</strong><span>关注至今涨停板次数</span></div>
        <div class="rec-limit-dates">${eventDates}</div>
    </div>`;
}

function observeStock(code, name, level) {
    const search = code || name;
    const params = new URLSearchParams({ search });
    if (name) params.set('name', name);
    const route = `watchlist?${params.toString()}`;
    applyWatchlistQuery(params.toString());
    history.replaceState(null, '', '#' + route);
    navigateTo(route, false, true);
    setInlineStatus('wlQueryHint', `来自推荐结果：${level} 观察，已过滤 ${name ? `${name} ` : ''}${code}`);
}

function scrollWatchlistTable(direction) {
    const scroller = document.getElementById('wlTableScroll') || document.querySelector('.watchlist-panel .table-scroll');
    if (!scroller) return;
    const distance = Math.max(240, Math.round(scroller.clientWidth * 0.75));
    scroller.scrollBy({ left: direction < 0 ? -distance : distance, behavior: 'smooth' });
}

// ---- Screening ----
let screeningLastResults = [];

async function runScreening() {
    const body = document.getElementById('scrResultBody');
    const countEl = document.getElementById('scrResultCount');
    const startBtn = document.querySelector('.btn-primary[onclick="runScreening()"]');
    const oldBtnText = startBtn.textContent;
    startBtn.disabled = true;
    startBtn.textContent = '筛选中...';
    body.innerHTML = '<div class="empty-state"><p>筛选任务已提交，等待结果...</p><p style="font-size:12px;color:#999">流水线运行中（约 1-3 分钟），结果将自动刷新</p></div>';

    const params = new URLSearchParams();
    params.set('drop_min', parseDecoratedNumber(document.getElementById('scrDropMin').value, 3));
    params.set('drop_max', parseDecoratedNumber(document.getElementById('scrDropMax').value, 10));
    params.set('vol_min', parseDecoratedNumber(document.getElementById('scrVolMin').value, 1));
    params.set('vol_max', parseDecoratedNumber(document.getElementById('scrVolMax').value, 5));
    params.set('turnover_min', parseDecoratedNumber(document.getElementById('scrToMin').value, 5));
    params.set('turnover_max', parseDecoratedNumber(document.getElementById('scrToMax').value, 10));
    params.set('mc_min', parseDecoratedNumber(document.getElementById('scrMcMin').value, 50));
    params.set('mc_max', parseDecoratedNumber(document.getElementById('scrMcMax').value, 200));
    params.set('pe_max', parseDecoratedNumber(document.getElementById('scrPeMax').value, 50));

    try {
        // Step 1: 启动后台筛选任务（立即返回，不阻塞）
        const startResp = await apiFetch(`/screening/run?${params}`, { method: 'POST', timeout: 60000, retries: 2 });
        const startData = await startResp.json();

        if (startData.status !== 'started') {
            body.innerHTML = `<div class="empty-state"><p style="color:#e60012">启动失败: ${startData.message || '未知错误'}</p></div>`;
            return;
        }

        // Step 2: 轮询结果（最长 5 分钟，每 3 秒一次）
        let dots = 0;
        for (let i = 0; i < 100; i++) {
            await new Promise(r => setTimeout(r, 3000));
            const pollResp = await apiFetch('/screening/latest', { timeout: 10000 });
            const pollData = await pollResp.json();

            // 更新进度提示
            dots = (dots + 1) % 4;
            const spinner = '.'.repeat(dots + 1);
            body.innerHTML = `<div class="empty-state"><p>筛选运行中${spinner}</p><p style="font-size:12px;color:#999">已完成 ${Math.round((i+1)/100*100)}% 轮询（最长 5 分钟）</p></div>`;

            if (pollData.status === 'completed') {
                if (pollData.results && pollData.results.length > 0) {
                    countEl.textContent = `${pollData.results.length} 条结果`;
                    screeningLastResults = pollData.results;
                    renderScreeningResults(pollData.results);
                    return;
                }
                if (pollData.errors && pollData.errors[0] && pollData.errors[0].includes('监控列表为空')) {
                    body.innerHTML = '<div class="empty-state"><p>监控列表为空</p><p style="color:#999999;font-size:13px">每日15:10自动从涨停股池添加监控股票</p></div>';
                    return;
                }
                countEl.textContent = '0 条结果';
                body.innerHTML = '<div class="empty-state"><p>筛选完成，无符合条件的股票</p></div>';
                return;
            }

            if (pollData.status === 'error') {
                body.innerHTML = `<div class="empty-state"><p style="color:#e60012">筛选异常: ${pollData.message || '未知错误'}</p></div>`;
                return;
            }
        }

        body.innerHTML = '<div class="empty-state"><p style="color:#e60012">筛选超时，请稍后重试或查看 /screening/latest</p></div>';
    } catch(e) {
        body.innerHTML = '<div class="empty-state"><p style="color:#e60012">请求异常，请检查后端是否运行</p></div>';
    } finally {
        startBtn.disabled = false;
        startBtn.textContent = oldBtnText;
    }
}

function renderScreeningResults(results) {
    screeningLastResults = results || [];
    const names = {strong_buy:'STRONG BUY',buy:'BUY',watch:'WATCH',pass:'PASS'};
    const keys = ['pullback','volume_trend','ma_alignment','strength','entry_point','market_cap','volume_ratio','turnover','pe','zt_quality'];
    let rows = results.map(s => {
        let dots = '';
        if (s.factors) keys.forEach(k => {
            const f = s.factors[k]; if (!f) return;
            dots += `<span class="factor-dot ${f.passed?'pass':'fail'}" title="${f.name}: ${f.detail}"></span>`;
        });
        return `<tr><td>${s.rank}</td><td><span class="stock-code">${s.code}</span></td><td>${s.name}</td>
            <td>${'\u2605'.repeat(Math.min(4,Math.max(1,Math.round(s.adjusted_score/25))))} <b>${s.adjusted_score}</b></td>
            <td><span class="${valueClass(s.drop_pct)}">${formatMaybePct(s.drop_pct, 2)}</span></td>
            <td><span class="tag tag-${(s.recommendation||'pass').toLowerCase()}">${names[(s.recommendation||'pass').toLowerCase()]}</span></td>
            <td>${s.zt_date||'--'}</td><td>${dots}</td></tr>`;
    }).join('');
    document.getElementById('scrResultBody').innerHTML = `<table class="data-table"><thead><tr><th>#</th><th>代码</th><th>名称</th><th>得分</th><th>回撤</th><th>推荐</th><th>日期</th><th>因子</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function filterScreeningResults() {
    const keyword = document.getElementById('scrSearch')?.value.trim().toLowerCase();
    if (!keyword) {
        renderScreeningResults(screeningLastResults);
        document.getElementById('scrResultCount').textContent = `${screeningLastResults.length} 条结果`;
        return;
    }
    const filtered = screeningLastResults.filter(s => String(s.code || '').includes(keyword) || String(s.name || '').toLowerCase().includes(keyword));
    document.getElementById('scrResultCount').textContent = `${filtered.length} 条结果`;
    if (filtered.length) renderScreeningResults(filtered);
    else document.getElementById('scrResultBody').innerHTML = '<div class="empty-state"><p>无匹配筛选结果</p></div>';
}

function resetScreening() {
    document.getElementById('scrDropMin').value='3.0%'; document.getElementById('scrDropMax').value='10.0%';
    document.getElementById('scrVolMin').value='1.0'; document.getElementById('scrVolMax').value='5.0';
    document.getElementById('scrToMin').value='5.0%'; document.getElementById('scrToMax').value='10.0%';
    document.getElementById('scrMcMin').value='50亿'; document.getElementById('scrMcMax').value='200亿';
    document.getElementById('scrPeMax').value='50';
    screeningLastResults = [];
    document.getElementById('scrResultBody').innerHTML = renderScreeningEmptyState();
    document.getElementById('scrResultCount').textContent='0 条结果';
}

function renderScreeningEmptyState() {
    return `<div class="empty-state screening-empty">
        <div class="empty-illustration" aria-hidden="true">
            <span class="lens"></span>
            <span class="handle"></span>
        </div>
        <p>设置筛选条件后点击「执行筛选」</p>
        <p>筛选结果将在此处展示</p>
        <p>筛选结果包含 STRONG_BUY / BUY / WATCH 三级推荐及审核降级详情</p>
    </div>`;
}

// ---- Settings ----
let factorWeights = { pullback:15,volume_trend:12,ma_alignment:12,strength:10,entry_point:10,market_cap:10,volume_ratio:8,turnover:8,pe:8,zt_quality:7 };
let strategyDraft = { dropMin:5, dropMax:10, peMax:50, volMin:1, volMax:5, turnoverMin:5, turnoverMax:10, mcMin:50, mcMax:200, trackingDays:30, latestFbt:140000, maxZbc:0, maxZtFrequency:2 };
let notificationDraft = { emailEnabled:true, emailHost:'smtp.qq.com', emailPort:465, emailUser:'', emailTo:'' };
let ztSortDraft = { sortBy:'seal_time', sortOrder:'asc' };
let runtimeConfigState = { config:null, previous_config:null, saved_at:null, source:'config/strategy_params.py' };
const factorNames = {pullback:'回撤幅度',volume_trend:'量能趋势',ma_alignment:'均线多头',strength:'强势确认',entry_point:'尾盘买点',market_cap:'流通市值',volume_ratio:'量比',turnover:'换手率',pe:'市盈率',zt_quality:'涨停质量'};
const strategyLabels = {
    dropMin:'回撤下限', dropMax:'回撤上限', peMax:'PE上限', volMin:'量比下限', volMax:'量比上限',
    turnoverMin:'换手率下限', turnoverMax:'换手率上限', mcMin:'流通市值下限', mcMax:'流通市值上限',
    trackingDays:'监控周期', latestFbt:'最晚封板时间', maxZbc:'最大炸板次数', maxZtFrequency:'周期内最大涨停次数'
};

function setupSettingsPage() {
    renderSettingsLoading();
    loadRuntimeConfig();
    updateNextRun();
}

function renderSettingsLoading() {
    const strategyEl = document.getElementById('strategyForm');
    const weightsEl = document.getElementById('factorWeightsForm');
    const compareEl = document.getElementById('settingsCompare');
    if (strategyEl) strategyEl.innerHTML = '<div class="form-status">正在从后端读取策略配置...</div>';
    if (weightsEl) weightsEl.innerHTML = '<div class="form-status">正在读取因子权重...</div>';
    if (compareEl) compareEl.textContent = '正在读取配置...';
}

async function loadRuntimeConfig() {
    try {
        const resp = await apiFetch('/config/strategy', { timeout: 15000, retries: 1 });
        const data = await readJsonResponse(resp);
        applyRuntimeConfig(data);
        setFormStatus('settingsStatus', `配置来源: ${data.source || '后端配置'}${data.saved_at ? '，上次保存 ' + formatSavedAt(data.saved_at) : ''}`, 'ok');
    } catch (e) {
        setFormStatus('settingsStatus', `配置读取失败: ${e.message || '请检查后端服务'}`, 'error');
        renderSettingsForms();
    }
}

async function readJsonResponse(resp) {
    let data = {};
    try {
        data = await resp.json();
    } catch (e) {
        data = {};
    }
    if (!resp.ok) {
        throw new Error(data.detail || data.message || `HTTP ${resp.status}`);
    }
    return data;
}

function applyRuntimeConfig(data) {
    runtimeConfigState = data || runtimeConfigState;
    const config = data.config || {};
    strategyDraft = { ...strategyDraft, ...(config.strategy || {}) };
    factorWeights = { ...factorWeights, ...(config.factorWeights || {}) };
    notificationDraft = { ...notificationDraft, ...(config.notification || {}) };
    ztSortDraft = { ...ztSortDraft, ...(config.ztSort || {}) };
    renderSettingsForms();
}

function renderSettingsForms() {
    const strategyEl = document.getElementById('strategyForm');
    if (strategyEl) {
        const latestFbt = getLatestFbtParts(strategyDraft.latestFbt);
        strategyEl.innerHTML = `
        <div class="form-row"><label>回撤区间</label><input type="number" step="0.1" class="input input-sm" data-config-key="dropMin" value="${strategyDraft.dropMin}"> - <input type="number" step="0.1" class="input input-sm" data-config-key="dropMax" value="${strategyDraft.dropMax}"> %</div>
        <div class="form-row"><label>PE上限</label><input type="number" step="0.1" class="input input-sm" data-config-key="peMax" value="${strategyDraft.peMax}"></div>
        <div class="form-row"><label>量比区间</label><input type="number" step="0.1" class="input input-sm" data-config-key="volMin" value="${strategyDraft.volMin}"> - <input type="number" step="0.1" class="input input-sm" data-config-key="volMax" value="${strategyDraft.volMax}"></div>
        <div class="form-row"><label>换手率区间</label><input type="number" step="0.1" class="input input-sm" data-config-key="turnoverMin" value="${strategyDraft.turnoverMin}"> - <input type="number" step="0.1" class="input input-sm" data-config-key="turnoverMax" value="${strategyDraft.turnoverMax}"> %</div>
        <div class="form-row"><label>流通市值区间</label><input type="number" step="1" class="input input-sm" data-config-key="mcMin" value="${strategyDraft.mcMin}"> - <input type="number" step="1" class="input input-sm" data-config-key="mcMax" value="${strategyDraft.mcMax}"> 亿</div>
        <div class="form-row"><label>监控周期</label><input type="number" step="1" class="input input-sm" data-config-key="trackingDays" value="${strategyDraft.trackingDays}"> 天</div>
        <div class="form-row clock-row">
            <label>最晚封板</label>
            <div class="clock-selects" data-config-key="latestFbt">
                <select class="input input-sm clock-select" data-config-key="latestFbtHour">${buildClockOptions(23, latestFbt.hour, '时')}</select>
                <span class="clock-separator">:</span>
                <select class="input input-sm clock-select" data-config-key="latestFbtMinute">${buildClockOptions(59, latestFbt.minute, '分')}</select>
                <span class="clock-separator">:</span>
                <select class="input input-sm clock-select" data-config-key="latestFbtSecond">${buildClockOptions(59, latestFbt.second, '秒')}</select>
            </div>
            <span class="field-hint">HH:MM:SS</span>
        </div>
        <div class="form-row"><label>炸板上限</label><input type="number" step="1" class="input input-sm" data-config-key="maxZbc" value="${strategyDraft.maxZbc}"> 次</div>
        <div class="form-row"><label>涨停频率上限</label><input type="number" step="1" class="input input-sm" data-config-key="maxZtFrequency" value="${strategyDraft.maxZtFrequency}"> 次</div>
        <div class="form-row"><label>涨停列表排序</label><select class="input input-sm" data-sort-key="sortBy">
            <option value="seal_time">封板时间</option><option value="turnover">换手率</option><option value="vol_ratio">量比</option><option value="pe">PE</option><option value="mcap">流通市值</option><option value="change_pct">涨幅</option>
        </select><select class="input input-sm" data-sort-key="sortOrder"><option value="asc">升序</option><option value="desc">降序</option></select></div>`;
        strategyEl.querySelector('[data-sort-key="sortBy"]').value = ztSortDraft.sortBy;
        strategyEl.querySelector('[data-sort-key="sortOrder"]').value = ztSortDraft.sortOrder;
        strategyEl.querySelectorAll('input,select').forEach(el => {
            el.addEventListener('input', renderSettingsCompare);
            el.addEventListener('change', renderSettingsCompare);
        });
    }

    const notifyEl = document.getElementById('notificationForm');
    if (notifyEl) {
        notifyEl.querySelectorAll('[data-notify-key]').forEach(input => {
            const key = input.dataset.notifyKey;
            input.value = notificationDraft[key] ?? '';
            input.addEventListener('input', renderSettingsCompare);
        });
    }

    const weightsEl = document.getElementById('factorWeightsForm');
    if (weightsEl) {
        weightsEl.innerHTML = Object.entries(factorWeights).map(([k,v]) =>
            `<div class="form-row"><label>${factorNames[k]||k}</label><input type="range" min="0" max="25" value="${v}" class="weight-slider" data-key="${k}" oninput="updateWeight(this)"><span id="wv_${k}">${v}%</span></div>`
        ).join('');
    }
    updateWeightSum();
    updateNextRun();
    renderSettingsCompare();
}

function updateNextRun() {
    const weekdays = ['周日','周一','周二','周三','周四','周五','周六'];
    const next = new Date();
    do { next.setDate(next.getDate() + 1); } while (next.getDay() === 0 || next.getDay() === 6);
    const el = document.getElementById('cfgNextRun');
    if (el) el.textContent = `${next.getFullYear()}-${String(next.getMonth()+1).padStart(2,'0')}-${String(next.getDate()).padStart(2,'0')} (${weekdays[next.getDay()]}) 15:10`;
}

function updateWeight(s) {
    factorWeights[s.dataset.key] = Number(s.value);
    const el = document.getElementById('wv_' + s.dataset.key);
    if (el) el.textContent = s.value + '%';
    updateWeightSum();
    renderSettingsCompare();
}

function updateWeightSum() {
    const sum = Object.values(factorWeights).reduce((a,b)=>a+Number(b || 0),0);
    const el = document.getElementById('weightSum');
    if (!el) return;
    el.textContent = '当前总权重: ' + sum + '%';
    el.className = 'weight-sum' + (sum !== 100 ? ' warn' : '');
}

function collectStrategyConfig() {
    const strategy = { ...strategyDraft };
    document.querySelectorAll('#strategyForm [data-config-key]').forEach(input => {
        if (!input.matches('input,select')) return;
        if (input.dataset.configKey === 'latestFbtHour' || input.dataset.configKey === 'latestFbtMinute' || input.dataset.configKey === 'latestFbtSecond') {
            return;
        }
        strategy[input.dataset.configKey] = Number(input.value);
    });
    strategy.latestFbt = readLatestFbtFromForm();
    const sortBy = document.querySelector('#strategyForm [data-sort-key="sortBy"]')?.value || ztSortDraft.sortBy;
    const sortOrder = document.querySelector('#strategyForm [data-sort-key="sortOrder"]')?.value || ztSortDraft.sortOrder;
    return { strategy, ztSort: { sortBy, sortOrder } };
}

function collectNotificationConfig() {
    const notification = { ...notificationDraft };
    document.querySelectorAll('#notificationForm [data-notify-key]').forEach(input => {
        notification[input.dataset.notifyKey] = input.type === 'number' ? Number(input.value) : input.value.trim();
    });
    return notification;
}

function collectFullRuntimeConfig() {
    const strategyPart = collectStrategyConfig();
    return {
        strategy: strategyPart.strategy,
        factorWeights: { ...factorWeights },
        notification: collectNotificationConfig(),
        ztSort: strategyPart.ztSort,
        schedule: runtimeConfigState.config?.schedule || { cronExpression:'10 15 * * 1-5', runTime:'15:10' },
    };
}

async function saveRuntimeConfig(payload, statusId, event, okText) {
    const btn = event?.target;
    const oldText = btn?.textContent;
    if (btn) { btn.disabled = true; btn.textContent = '保存中...'; }
    setFormStatus(statusId, '正在写入后端配置表并同步 config/strategy_params.py ...');
    try {
        const merged = { ...(runtimeConfigState.config || {}), ...payload };
        if (payload.strategy) merged.strategy = payload.strategy;
        if (payload.factorWeights) merged.factorWeights = payload.factorWeights;
        if (payload.notification) merged.notification = payload.notification;
        if (payload.ztSort) merged.ztSort = payload.ztSort;
        const resp = await apiFetch('/config/strategy', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(merged),
            timeout: 20000,
            retries: 0,
        });
        const data = await readJsonResponse(resp);
        applyRuntimeConfig(data);
        setFormStatus(statusId, okText || '配置已保存并生效', 'ok');
        return data;
    } catch (e) {
        setFormStatus(statusId, `保存失败: ${e.message || '后端异常'}`, 'error');
        throw e;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = oldText; }
    }
}

async function saveStrategyConfig(event) {
    const payload = collectStrategyConfig();
    await saveRuntimeConfig(payload, 'strategyStatus', event, '策略参数已保存。后续筛选、监控状态和涨停频率按该配置执行。');
}

async function saveNotificationConfig(event, opts = {}) {
    const payload = { notification: collectNotificationConfig() };
    const data = await saveRuntimeConfig(payload, 'emailStatus', event, opts.silent ? '通知配置已保存，准备发送测试邮件...' : '通知配置已保存。后续邮件按该收件账号发送。');
    return data;
}

async function saveFactorWeights(event) {
    await saveRuntimeConfig({ factorWeights: { ...factorWeights } }, 'settingsStatus', event, '权重配置已保存。后续评分优先使用该配置。');
}

function formatSavedAt(value) {
    if (!value) return '--';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).slice(0, 19);
    return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
}

function formatConfigValue(value, suffix = '') {
    if (value === undefined || value === null || value === '') return '--';
    return `${value}${suffix}`;
}

function renderSettingsCompare() {
    const el = document.getElementById('settingsCompare');
    if (!el) return;
    const prev = runtimeConfigState.previous_config || runtimeConfigState.config || {};
    const current = collectFullRuntimeConfig();
    const rows = [];
    const strategySuffix = {
        dropMin:'%', dropMax:'%', turnoverMin:'%', turnoverMax:'%', mcMin:'亿', mcMax:'亿',
        trackingDays:'天', maxZbc:'次', maxZtFrequency:'次'
    };
    Object.keys(strategyLabels).forEach(key => {
        const prevVal = key === 'latestFbt' ? formatFbtToClock(prev.strategy?.[key]) : prev.strategy?.[key];
        const currentVal = key === 'latestFbt' ? formatFbtToClock(current.strategy[key]) : current.strategy[key];
        rows.push([strategyLabels[key], prevVal, currentVal, strategySuffix[key] || '']);
    });
    Object.entries(factorNames).forEach(([key, label]) => {
        rows.push([`${label}权重`, prev.factorWeights?.[key], current.factorWeights[key], '%']);
    });
    rows.push(['收件邮箱', prev.notification?.emailTo, current.notification.emailTo, '']);
    rows.push(['发件邮箱', prev.notification?.emailUser, current.notification.emailUser, '']);
    rows.push(['SMTP主机', prev.notification?.emailHost, current.notification.emailHost, '']);
    rows.push(['SMTP端口', prev.notification?.emailPort, current.notification.emailPort, '']);
    rows.push(['涨停排序', `${prev.ztSort?.sortBy || '--'} / ${prev.ztSort?.sortOrder || '--'}`, `${current.ztSort.sortBy} / ${current.ztSort.sortOrder}`, '']);

    el.innerHTML = `
        <div class="compare-meta">上次保存：${formatSavedAt(runtimeConfigState.saved_at)}｜来源：${runtimeConfigState.source || '--'}</div>
        <table class="compare-table"><thead><tr><th>配置项</th><th>上次保存值</th><th>当前编辑值</th></tr></thead><tbody>
        ${rows.map(([label, oldVal, newVal, suffix]) => {
            const changed = String(oldVal ?? '') !== String(newVal ?? '');
            return `<tr class="${changed ? 'changed' : ''}"><td>${escapeHtml(label)}</td><td>${escapeHtml(formatConfigValue(oldVal, suffix))}</td><td>${escapeHtml(formatConfigValue(newVal, suffix))}</td></tr>`;
        }).join('')}</tbody></table>`;
}

async function testEmail(event) {
    const btn = event?.target;
    if (btn) btn.disabled = true;
    setFormStatus('emailStatus', '正在保存当前通知配置...');
    try {
        await saveNotificationConfig(null, { silent: true });
        setFormStatus('emailStatus', '正在使用已保存收件账号发送测试邮件...');
        const resp = await apiFetch('/system/test-email', { method: 'POST', timeout: 30000 });
        const data = await readJsonResponse(resp);
        if (data.status === 'ok') {
            setFormStatus('emailStatus', `${data.message || '测试邮件已发送，请检查收件箱'}${data.email_to ? '：' + data.email_to : ''}`, 'ok');
        } else {
            setFormStatus('emailStatus', '发送失败: ' + (data.message || '未知错误'), 'error');
        }
    } catch(e) {
        setFormStatus('emailStatus', '请求超时或后端异常，请稍后重试', 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function manualRun(event) {
    const btn = event?.target;
    const oldText = btn.textContent;
    btn.textContent = '筛选中，请稍候...';
    btn.disabled = true;
    setFormStatus('runStatus', '已请求 /api/v1/screening/run，等待后台返回结果...');
    try {
        // 启动后台任务
        const startResp = await apiFetch('/screening/run', { method: 'POST', timeout: 60000, retries: 2 });
        const startData = await startResp.json();
        if (startData.status !== 'started') throw new Error(startData.message || '启动失败');

        // 轮询结果
        for (let i = 0; i < 100; i++) {
            await new Promise(r => setTimeout(r, 3000));
            btn.textContent = `轮询中 (${i + 1}/100)...`;
            const pollResp = await apiFetch('/screening/latest', { timeout: 10000 });
            const pollData = await pollResp.json();
            if (pollData.status === 'completed') {
                const total = pollData.total_scored || pollData.results?.length || 0;
                setFormStatus('runStatus', `筛选已完成，共 ${total} 条推荐结果，STRONG_BUY ${pollData.strong_buy || 0}、BUY ${pollData.buy || 0}、WATCH ${pollData.watch || 0}`, 'ok');
                loadDashboard();
                return;
            }
            if (pollData.status === 'error') {
                setFormStatus('runStatus', '筛选失败: ' + (pollData.message || '未知错误'), 'error');
                return;
            }
        }
        setFormStatus('runStatus', '筛选超时，请查看筛选页面或 /api/v1/screening/latest', 'error');
    } catch(e) {
        setFormStatus('runStatus', '请求异常，请检查后端是否运行', 'error');
    } finally {
        btn.textContent = oldText;
        btn.disabled = false;
    }
}
function saveConfig() {
    saveFactorWeights();
}

// ---- Pagination ----
function renderPagination(cid, total, page, size, cb) {
    const p = Math.ceil(total / size);
    if (p <= 1) {
        document.getElementById(cid).innerHTML = total ? `<span class="page-summary">共 ${total} 条</span>` : '';
        return;
    }
    const prevDisabled = page <= 1 ? 'disabled' : '';
    const nextDisabled = page >= p ? 'disabled' : '';
    document.getElementById(cid).innerHTML = `
        <button class="page-arrow" ${prevDisabled} aria-label="上一页" title="上一页" onclick="${cb.name}(${Math.max(1, page - 1)})">‹</button>
        <button class="active" disabled aria-current="page">第 ${page}/${p} 页</button>
        <button class="page-arrow" ${nextDisabled} aria-label="下一页" title="下一页" onclick="${cb.name}(${Math.min(p, page + 1)})">›</button>
        <span class="page-summary">共 ${total} 条</span>`;
}

// ---- Global ----
function refreshData() { navigateTo(location.hash.replace('#','')||'dashboard', false, true); }
function setupScreeningPage() {
    if (!screeningLastResults.length) {
        const body = document.getElementById('scrResultBody');
        if (body && (!body.textContent.trim() || body.textContent.includes('设置筛选条件后点击'))) {
            body.innerHTML = renderScreeningEmptyState();
        }
    }
}
