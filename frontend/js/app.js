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

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initWatchlistTabs();
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
function navigateTo(page, pushState = true, force = false) {
    if (!force && page === _currentPage) return;
    _currentPage = page;
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const nav = document.querySelector(`[data-page="${page}"]`);
    if (nav) nav.classList.add('active');
    const pageEl = document.getElementById(`page-${page}`);
    if (pageEl) pageEl.classList.add('active');
    if (pushState) history.replaceState(null, '', '#' + page);
    document.getElementById('pageTitle').textContent = {
        dashboard:'Dashboard 市场总览',watchlist:'监控列表',
        recommendations:'推荐结果',screening:'手动筛选',settings:'策略配置'
    }[page] || page;
    if (page === 'dashboard') loadDashboard();
    if (page === 'watchlist') loadWatchlist();
    if (page === 'recommendations') loadRecommendations();
    if (page === 'settings') setupSettingsPage();
}

let wlCurrentStatus = ''; // current tab filter

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

function syncWatchlistTabs() {
    document.querySelectorAll('#wlTabs .tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.status === wlCurrentStatus);
    });
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
    document.getElementById('dateDisplay').textContent =
        `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')} ${weekdays[now.getDay()]}`;
}

// ---- Dashboard ----
async function loadDashboard() {
    // 重置为空状态
    document.getElementById('metricWatchlist').textContent = '--';
    document.getElementById('metricNewZT').textContent = '--';
    document.getElementById('metricRecs').textContent = '--';
    document.getElementById('metricIndex').textContent = '--';
    document.getElementById('recentRecsBody').innerHTML = '<tr><td colspan="6" class="empty-cell">加载中...</td></tr>';
    document.getElementById('ztCount').textContent = '--';
    document.getElementById('sectorDist').innerHTML = '<p style="color:#8B95A8;text-align:center;padding:20px">加载中...</p>';

    try {
        // 并行请求 watchlist stats 和最新筛选结果
        const [wlResp, recResp] = await Promise.all([
            apiFetch('/watchlist/stats'),
            apiFetch('/screening/latest')
        ]);
        const wl = await wlResp.json();
        const rec = await recResp.json();

        // 监控股票数
        document.getElementById('metricWatchlist').textContent = wl.total || 0;
        document.getElementById('metricNewZT').textContent = wl.new_today || 0;

        // 推荐数和指数
        if (rec.results && rec.results.length > 0) {
            document.getElementById('metricRecs').textContent = rec.total_scored || rec.results.length;
            if (rec.index_gain != null) {
                const el = document.getElementById('metricIndex');
                el.textContent = `${rec.index_gain >= 0 ? '+' : ''}${rec.index_gain.toFixed(2)}%`;
                el.className = 'metric-value ' + (rec.index_gain >= 0 ? 'up' : 'down');
            }
            renderRecentRecommendations(rec.results.slice(0, 5));
            document.getElementById('ztCount').textContent = rec.total_scored || rec.results.length;
        } else {
            document.getElementById('metricRecs').textContent = '0';
            document.getElementById('ztCount').textContent = '0';
            document.getElementById('recentRecsBody').innerHTML = '<tr><td colspan="6" class="empty-cell">暂无推荐数据</td></tr>';
            // 尝试获取上证指数
            try {
                const idxResp = await apiFetch('/system/status');
                const idx = await idxResp.json();
                document.getElementById('metricIndex').textContent = '--';
            } catch(e) {}
        }

        // 板块分布 - 从筛选结果的行业统计中获取
        if (rec.sector_dist) {
            renderSectorDist(rec.sector_dist);
        } else {
            document.getElementById('sectorDist').innerHTML = '<p style="color:#8B95A8;text-align:center;padding:20px">暂无板块分布数据</p>';
        }

    } catch(e) {
        // API 不可用（可能是 Render 冷启动），提供重试
        document.getElementById('metricWatchlist').textContent = '--';
        document.getElementById('metricNewZT').textContent = '--';
        document.getElementById('metricRecs').textContent = '--';
        document.getElementById('metricIndex').textContent = '--';
        document.getElementById('recentRecsBody').innerHTML =
            '<tr><td colspan="6" class="empty-cell" style="color:#e60012">'
            + 'API 连接失败，后端可能正在启动中（冷启动约30-60秒）<br>'
            + '<button class="btn" onclick="refreshData()" style="margin-top:8px">点击重试</button>'
            + '</td></tr>';
        document.getElementById('ztCount').textContent = '--';
        document.getElementById('sectorDist').innerHTML =
            '<p style="color:#e60012;text-align:center;padding:20px">后端服务不可用</p>';
    }
}

function renderSectorDist(sectors) {
    if (!sectors || Object.keys(sectors).length === 0) {
        document.getElementById('sectorDist').innerHTML = '<p style="color:#8B95A8;text-align:center;padding:20px">暂无板块分布数据</p>';
        return;
    }
    const total = Object.values(sectors).reduce((a,b)=>a+b,0);
    const colors = ['#2932e1','#e60012','#009966','#F39C12','#5A6680','#8B95A8'];
    let i = 0;
    document.getElementById('sectorDist').innerHTML = Object.entries(sectors)
        .sort((a,b) => b[1]-a[1])
        .map(([name, count]) => {
            const pct = total > 0 ? Math.round(count/total*100) : 0;
            const c = colors[i++ % colors.length];
            return `<div class="sector-bar"><div class="sector-bar-label"><span>${name}</span><span>${pct}%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:${pct}%;background:${c}"></div></div></div>`;
        }).join('');
}

function renderRecentRecommendations(results) {
    const names = {strong_buy:'STRONG BUY',buy:'BUY',watch:'WATCH',pass:'PASS'};
    document.getElementById('recentRecsBody').innerHTML = results.map(s => `
        <tr>
            <td><span class="stock-code">${s.code}</span></td>
            <td>${s.name}</td>
            <td>${'\u2605'.repeat(Math.min(4,Math.max(1,Math.round(s.adjusted_score/25))))} ${s.adjusted_score}</td>
            <td style="color:${s.drop_pct<0?'#e60012':'#009966'}">${(s.drop_pct||0).toFixed(2)}%</td>
            <td><span class="tag tag-${(s.recommendation||'pass').toLowerCase()}">${names[(s.recommendation||'pass').toLowerCase()]}</span></td>
            <td>${s.zt_date||'--'}</td>
        </tr>`).join('');
}

// ---- Watchlist ----
let wlCurrentPage = 1;
let wlFullCache = null; // 全量数据缓存（用于状态分类计数）
let wlSortKey = '';
let wlSortOrder = 'asc';

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
    const params = new URLSearchParams({ page: String(page), size: '20' });
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
    wlCurrentPage = page;
    syncWatchlistTabs();
    updateWatchlistSortHeaders();

    // 重置为空状态
    document.getElementById('wlTableBody').innerHTML = '<tr><td colspan="15" class="empty-cell">加载中...</td></tr>';
    document.getElementById('wlCountAll').textContent = '0';
    document.getElementById('wlCountActive').textContent = '0';
    document.getElementById('wlCountRec').textContent = '0';
    document.getElementById('wlCountExpired').textContent = '0';

    try {
        const pageParams = buildWatchlistParams(page, true);
        const statsParams = buildWatchlistParams(1, false);
        statsParams.set('size', '500');

        // 同时获取分页数据和全量统计（全量用于tab计数）
        const [pageResp, statsResp] = await Promise.all([
            apiFetch(`/watchlist?${pageParams.toString()}`),
            apiFetch(`/watchlist?${statsParams.toString()}`)  // 一次获取全量用于状态统计
        ]);
        const data = await pageResp.json();
        const allData = await statsResp.json();

        if (data.items && data.items.length > 0) {
            renderWatchlistTable(data.items);
            renderPagination('wlPagination', data.total, data.page, data.size, loadWatchlist);
        } else {
            document.getElementById('wlTableBody').innerHTML = '<tr><td colspan="15" class="empty-cell">暂无监控数据。每日15:10自动从涨停股池添加。</td></tr>';
            document.getElementById('wlPagination').innerHTML = '';
        }

        // 从全量数据计算各状态数量
        if (allData.items && allData.items.length > 0) {
            wlFullCache = allData.items;
            document.getElementById('wlCountAll').textContent = allData.total;
            document.getElementById('wlCountActive').textContent = allData.items.filter(i => i.status === 'active').length;
            document.getElementById('wlCountRec').textContent = allData.items.filter(i => i.status === 'recommended').length;
            document.getElementById('wlCountExpired').textContent = allData.items.filter(i => i.status === 'expired').length;
        } else {
            document.getElementById('wlCountAll').textContent = allData.total || 0;
        }

    } catch(e) {
        document.getElementById('wlTableBody').innerHTML =
            '<tr><td colspan="15" class="empty-cell" style="color:#e60012">'
            + 'API 连接失败，后端可能正在启动中（冷启动约30-60秒）'
            + ' <a href="#" onclick="refreshData();return false" style="color:#3B82F6">点击重试</a></td></tr>';
        document.getElementById('wlPagination').innerHTML = '';
    }
}

function renderWatchlistTable(items) {
    const tags = {active:'回撤中',recommended:'已推荐',expired:'已过期'};
    document.getElementById('wlTableBody').innerHTML = items.map(item => {
        const cp = item.current_price || item.ref_price;
        const dp = item.drop_pct != null ? item.drop_pct : 0;
        const dpColor = dp < 0 ? (Math.abs(dp) >= 5 ? '#e60012' : '#F39C12') : '#009966';
        // 封板时间格式化
        const sealTime = item.seal_time && item.seal_time !== '0' && item.seal_time !== 0
            ? (() => { const s = String(item.seal_time); const h = parseInt(s.substring(0, s.length-4)||'0'); const m = parseInt(s.substring(s.length-4, s.length-2)||'0'); return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}`; })()
            : '--';
        const ztCount = item.zt_count && item.zt_count !== '0' ? item.zt_count : '--';
        return `<tr>
            <td><input type="checkbox"></td>
            <td><span class="stock-code">${item.code}</span></td>
            <td>${item.name}${item.zt_time ? '<div style="font-size:10px;color:#999999">封板'+item.zt_time+(item.zbc>0?' 炸板'+item.zbc+'次':'')+'</div>' : ''}</td>
            <td>${item.zt_date}</td>
            <td>${item.ref_price.toFixed(2)}</td>
            <td>${cp.toFixed(2)}</td>
            <td style="color:${dpColor};font-weight:600">${dp.toFixed(2)}%</td>
            <td>${item.turnover||'--'}%</td>
            <td>${item.vol_ratio||'--'}</td>
            <td>${item.pe||'--'}</td>
            <td style="font-size:12px">${sealTime}</td>
            <td style="font-size:12px">${ztCount}</td>
            <td>${item.mcap||'--'}</td>
            <td><span class="tag tag-${item.status||'active'}">${tags[item.status]||'回撤中'}</span></td>
            <td style="white-space:nowrap">
                <button class="btn btn-sm" style="margin-right:4px" onclick="event.stopPropagation();showStockDetail('${item.code}')">详情</button>
                <button class="btn btn-sm" onclick="removeWatchlistItem('${item.code}')">移除</button>
            </td>
        </tr>`;
    }).join('');
}

async function removeWatchlistItem(code) {
    if (!confirm(`确认从监控列表中移除 ${code}？`)) return;
    try { await apiFetch(`/watchlist/${code}`, { method: 'DELETE' }); } catch(e) {}
    loadWatchlist(wlCurrentPage);
}
function exportWatchlist() { alert('导出功能开发中'); }

// ---- Stock Detail Modal ----
let _detailCharts = {};

function showStockDetail(code) {
    const modal = document.getElementById('stockDetailModal');
    if (!modal) return;
    modal.style.display = 'flex';
    document.getElementById('modalStockName').textContent = '加载中...';
    document.getElementById('modalStockCode').textContent = code;
    document.getElementById('modalMeta').innerHTML = '';

    // 标记所有图表为加载中
    ['chartPrice','chartVolume','chartMA','chartChange','chartDrawdownDist'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.add('loading');
    });

    loadStockDetailData(code);
}

function closeStockDetail() {
    const modal = document.getElementById('stockDetailModal');
    if (modal) modal.style.display = 'none';
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
    const dpClass = dp !== null && dp < 0 ? 'down' : 'up';
    const dpVal = dp !== null ? `${dp.toFixed(2)}%` : '--';
    document.getElementById('modalMeta').innerHTML =
        `<span>涨停日期: <b class="val">${data.zt_date}</b></span>` +
        `<span>参考价: <b class="val">${data.ref_price.toFixed(2)}</b></span>` +
        `<span>现价: <b class="val">${data.current_price ? data.current_price.toFixed(2) : '--'}</b></span>` +
        `<span>回撤: <b class="val ${dpClass}">${dpVal}</b></span>` +
        `<span>封板时间: <b class="val">${formatSealTimeForDetail(data.seal_time)}</b></span>` +
        `<span>连板数: <b class="val">${data.consecutive || '--'}</b></span>` +
        `<span>加入时间: <b class="val">${data.added_date || '--'}</b></span>`;

    const cd = data.chart_data;
    if (!cd || !cd.dates || cd.dates.length === 0) {
        document.getElementById('chartPrice').innerHTML = '<p style="color:#64748B;text-align:center;padding:60px 0">暂无K线数据</p>';
        return;
    }

    // 清除loading状态
    ['chartPrice','chartVolume','chartMA','chartChange','chartDrawdownDist'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.remove('loading');
    });

    // 销毁旧图表
    Object.values(_detailCharts).forEach(c => { try { c.dispose(); } catch(e) {} });
    _detailCharts = {};

    const darkTheme = getEChartsDarkTheme();

    // 1. 价格走势 + 回撤双轴图
    _detailCharts.price = echarts.init(document.getElementById('chartPrice'), darkTheme);
    _detailCharts.price.setOption({
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'cross', crossStyle: { color: '#64748B' } },
            backgroundColor: 'rgba(15,23,42,0.95)',
            borderColor: '#1E293B',
            textStyle: { color: '#F8FAFC', fontSize: 12 }
        },
        legend: { data: ['收盘价','回撤%','参考价'], bottom: 0, textStyle: { color: '#94A3B8', fontSize: 11 } },
        grid: { left: 60, right: 60, top: 20, bottom: 40 },
        xAxis: {
            type: 'category', data: cd.dates,
            axisLine: { lineStyle: { color: '#1E293B' } },
            axisLabel: { color: '#64748B', fontSize: 10, rotate: cd.dates.length > 30 ? 45 : 0 },
        },
        yAxis: [
            {
                type: 'value', name: '价格(元)',
                nameTextStyle: { color: '#94A3B8', fontSize: 11 },
                axisLabel: { color: '#64748B', fontSize: 10 },
                splitLine: { lineStyle: { color: '#1E293B', type: 'dashed' } },
            },
            {
                type: 'value', name: '回撤(%)',
                nameTextStyle: { color: '#94A3B8', fontSize: 11 },
                axisLabel: { color: '#64748B', fontSize: 10 },
                splitLine: { show: false },
            }
        ],
        series: [
            {
                name: '收盘价', type: 'line', data: cd.closes,
                smooth: true, symbol: 'none',
                lineStyle: { color: '#3B82F6', width: 2 },
                areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [{offset:0,color:'rgba(59,130,246,0.15)'},{offset:1,color:'rgba(59,130,246,0)'}] } },
            },
            {
                name: '参考价', type: 'line', yAxisIndex: 0,
                markLine: { silent: true, symbol: 'none',
                    lineStyle: { color: '#F59E0B', type: 'dashed', width: 1.5 },
                    label: { formatter: `参考价 {c}`, color: '#F59E0B', fontSize: 11 },
                    data: [{ yAxis: data.ref_price }] },
                data: []
            },
            {
                name: '回撤%', type: 'line', yAxisIndex: 1, data: cd.drawdowns,
                smooth: true, symbol: 'none',
                lineStyle: { color: '#EF4444', width: 1.5, type: 'dashed' },
                areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                    colorStops: [{offset:0,color:'rgba(239,68,68,0.1)'},{offset:1,color:'rgba(239,68,68,0)'}] } },
            }
        ]
    });

    // 2. 成交量图
    _detailCharts.volume = echarts.init(document.getElementById('chartVolume'), darkTheme);
    _detailCharts.volume.setOption({
        tooltip: { trigger: 'axis', backgroundColor: 'rgba(15,23,42,0.95)', borderColor: '#1E293B', textStyle: { color: '#F8FAFC', fontSize: 12 } },
        grid: { left: 50, right: 16, top: 16, bottom: 30 },
        xAxis: { type: 'category', data: cd.dates, axisLabel: { color: '#64748B', fontSize: 10, rotate: cd.dates.length > 30 ? 45 : 0 } },
        yAxis: { type: 'value', name: '手', axisLabel: { color: '#64748B', fontSize: 10, formatter: v => v >= 1e6 ? (v/1e6).toFixed(1)+'M' : v >= 1e4 ? (v/1e4).toFixed(0)+'万' : v }, splitLine: { lineStyle: { color: '#1E293B', type: 'dashed' } } },
        series: [{ type: 'bar', data: cd.volumes,
            itemStyle: { color: params => cd.changes && cd.changes[params.dataIndex] >= 0 ? 'rgba(34,197,94,0.6)' : 'rgba(239,68,68,0.5)' } }]
    });

    // 3. 均线图
    _detailCharts.ma = echarts.init(document.getElementById('chartMA'), darkTheme);
    const maSeries = [
        { name: '收盘价', type: 'line', data: cd.closes, smooth: true, symbol: 'none', lineStyle: { color: '#64748B', width: 1, type: 'dotted' } }
    ];
    const maColors = { 'MA5': '#F59E0B', 'MA10': '#3B82F6', 'MA20': '#8B5CF6', 'MA60': '#EC4899' };
    ['ma5','ma10','ma20','ma60'].forEach((k, i) => {
        if (cd[k] && cd[k].length > 0) {
            const name = 'MA' + [5,10,20,60][i];
            maSeries.push({ name, type: 'line', data: cd[k], smooth: true, symbol: 'none', lineStyle: { color: maColors[name], width: 1.5 } });
        }
    });
    _detailCharts.ma.setOption({
        tooltip: { trigger: 'axis', backgroundColor: 'rgba(15,23,42,0.95)', borderColor: '#1E293B', textStyle: { color: '#F8FAFC', fontSize: 12 } },
        legend: { data: maSeries.map(s => s.name), bottom: 0, textStyle: { color: '#94A3B8', fontSize: 10 } },
        grid: { left: 55, right: 16, top: 16, bottom: 35 },
        xAxis: { type: 'category', data: cd.dates, axisLabel: { color: '#64748B', fontSize: 10, rotate: cd.dates.length > 30 ? 45 : 0 } },
        yAxis: { type: 'value', name: '元', axisLabel: { color: '#64748B', fontSize: 10 }, splitLine: { lineStyle: { color: '#1E293B', type: 'dashed' } } },
        series: maSeries
    });

    // 4. 每日涨跌幅
    if (cd.changes && cd.changes.length > 0) {
        _detailCharts.change = echarts.init(document.getElementById('chartChange'), darkTheme);
        _detailCharts.change.setOption({
            tooltip: { trigger: 'axis', backgroundColor: 'rgba(15,23,42,0.95)', borderColor: '#1E293B', textStyle: { color: '#F8FAFC', fontSize: 12 } },
            grid: { left: 50, right: 16, top: 16, bottom: 30 },
            xAxis: { type: 'category', data: cd.dates, axisLabel: { color: '#64748B', fontSize: 10, rotate: cd.dates.length > 30 ? 45 : 0 } },
            yAxis: { type: 'value', name: '%', axisLabel: { color: '#64748B', fontSize: 10 }, splitLine: { lineStyle: { color: '#1E293B', type: 'dashed' } } },
            series: [{ type: 'bar', data: cd.changes,
                itemStyle: { color: params => params.value >= 0 ? '#EF4444' : '#22C55E' } }]
        });
    }

    // 5. 回撤分布饼图
    const dist = cd.drawdown_distribution;
    if (dist && Object.keys(dist).length > 0) {
        _detailCharts.dist = echarts.init(document.getElementById('chartDrawdownDist'), darkTheme);
        const distData = Object.entries(dist).map(([k, v]) => ({ name: k, value: v }));
        _detailCharts.dist.setOption({
            tooltip: { trigger: 'item', formatter: '{b}: {c} 天 ({d}%)', backgroundColor: 'rgba(15,23,42,0.95)', borderColor: '#1E293B', textStyle: { color: '#F8FAFC', fontSize: 12 } },
            legend: { orient: 'vertical', right: 8, top: 'center', textStyle: { color: '#94A3B8', fontSize: 10 } },
            series: [{
                type: 'pie', radius: ['40%', '72%'], center: ['40%', '50%'], avoidLabelOverlap: false,
                itemStyle: { borderRadius: 2, borderColor: '#0A1628', borderWidth: 2 },
                label: { show: true, formatter: '{b}\n{d}%', color: '#94A3B8', fontSize: 10 },
                data: distData,
                color: ['#EF4444','#F59E0B','#F97316','#64748B','#22C55E','#10B981']
            }]
        });
    }
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

function getEChartsDarkTheme() {
    return {
        backgroundColor: 'transparent',
        textStyle: { color: '#94A3B8' },
    };
}

// ---- Recommendations ----
async function loadRecommendations() {
    const level = document.getElementById('recLevel').value;
    document.getElementById('recList').innerHTML = '<p class="empty-state">加载中...</p>';
    document.getElementById('recStats').innerHTML = '';

    try {
        const resp = await apiFetch('/screening/latest');
        const data = await resp.json();
        if (data.results && data.results.length > 0) {
            let filtered = data.results;
            if (level) filtered = filtered.filter(r => r.recommendation === level);
            if (filtered.length > 0) {
                renderRecommendationCards(filtered);
            } else {
                document.getElementById('recList').innerHTML = '<p class="empty-state">该等级暂无推荐结果</p>';
            }
            renderRecStats(data);
            return;
        }
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

function renderRecStats(data) {
    document.getElementById('recStats').innerHTML = `
        <div class="metric-cards" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">
            <div class="metric-card"><div class="metric-label">总推荐数</div><div class="metric-value highlight">${data.total_scored||0}</div></div>
            <div class="metric-card"><div class="metric-label">STRONG BUY</div><div class="metric-value up">${data.strong_buy||0}</div></div>
            <div class="metric-card"><div class="metric-label">BUY</div><div class="metric-value" style="color:#F39C12">${data.buy||0}</div></div>
            <div class="metric-card"><div class="metric-label">WATCH</div><div class="metric-value" style="color:#2932e1">${data.watch||0}</div></div>
        </div>`;
}

function renderRecommendationCards(stocks) {
    const names = {STRONG_BUY:'STRONG BUY',BUY:'BUY',WATCH:'WATCH',PASS:'PASS'};
    const colors = {STRONG_BUY:'#e60012',BUY:'#F39C12',WATCH:'#2932e1',PASS:'#999999'};
    const keys = ['pullback','volume_trend','ma_alignment','strength','entry_point','market_cap','volume_ratio','turnover','pe','zt_quality'];
    const el = document.getElementById('recList');
    el.innerHTML = stocks.map(s => {
        let dots = '';
        if (s.factors) keys.forEach(k => {
            const f = s.factors[k]; if (!f) return;
            dots += `<span class="factor-dot ${f.passed?'pass':'fail'}" title="${f.name}: ${f.detail} (${f.score}/10)"></span>`;
        });
        const auditHtml = renderAuditFailures(s.audit);
        const historyHtml = renderPriceHistory(s.price_history);
        return `<div class="rec-card">
            <div class="rec-card-header">
                <div class="rec-rank">${s.rank}</div>
                <div style="margin-left:8px"><span class="stock-code" style="font-size:15px">${s.code}</span> <span style="font-size:15px;font-weight:600">${s.name}</span></div>
                <div style="flex:1"></div>
                <div class="rec-stars">${'\u2605'.repeat(Math.min(4,Math.max(1,Math.round(s.adjusted_score/25))))}</div>
                <span style="font-size:20px;font-weight:700;color:${colors[s.recommendation]||'#999999'};min-width:36px;text-align:right">${s.adjusted_score}</span>
                <span class="tag tag-${s.recommendation.toLowerCase()}">${names[s.recommendation]}</span>
            </div>
            <div style="display:flex;gap:24px;margin:8px 0;font-size:13px;color:#999999">
                <span>回撤: <b style="color:${s.drop_pct<0?'#e60012':'#009966'}">${(s.drop_pct||0).toFixed(2)}%</b></span>
                <span>涨停日: ${s.zt_date}</span>
                <span>参考价: ${s.ref_price}</span>
            </div>
            ${auditHtml}
            ${historyHtml}
            <div>${dots}</div>
        </div>`;
    }).join('');
}

function renderAuditFailures(audit) {
    if (!audit || !audit.downgraded) return '';
    const failures = (audit.validations || []).filter(v => v.status === 'fail');
    const items = failures.map(v => `<li><b>${v.name}</b>: ${v.detail}</li>`).join('');
    return `<div class="audit-failures">
        <div>审核降级: ${audit.original_rec} → ${audit.adjusted_rec}（失败${audit.fail_count || failures.length}项）</div>
        ${items ? `<ul>${items}</ul>` : ''}
    </div>`;
}

function renderPriceHistory(rows) {
    if (!rows || rows.length === 0) return '';
    const body = rows.slice(-12).map(row => {
        const draw = Number(row.drawdown_pct || 0);
        const change = Number(row.change_pct || 0);
        return `<tr>
            <td>${row.date}</td>
            <td>${Number(row.close || 0).toFixed(2)}</td>
            <td class="${change >= 0 ? 'up-text' : 'down-text'}">${change.toFixed(2)}%</td>
            <td class="${draw < 0 ? 'down-text' : 'up-text'}">${draw.toFixed(2)}%</td>
        </tr>`;
    }).join('');
    return `<div class="price-history">
        <div class="price-history-title">价格/回撤走势</div>
        <table>
            <thead><tr><th>日期</th><th>收盘</th><th>涨跌</th><th>回撤</th></tr></thead>
            <tbody>${body}</tbody>
        </table>
    </div>`;
}

// ---- Screening ----
async function runScreening() {
    const body = document.getElementById('scrResultBody');
    const countEl = document.getElementById('scrResultCount');
    const startBtn = document.querySelector('.btn-primary[onclick="runScreening()"]');
    const oldBtnText = startBtn.textContent;
    startBtn.disabled = true;
    startBtn.textContent = '筛选中...';
    body.innerHTML = '<div class="empty-state"><p>筛选任务已提交，等待结果...</p><p style="font-size:12px;color:#999">流水线运行中（约 1-3 分钟），结果将自动刷新</p></div>';

    const params = new URLSearchParams();
    params.set('gain_min', document.getElementById('scrGainMin').value||3);
    params.set('gain_max', document.getElementById('scrGainMax').value||10);
    params.set('drop_min', document.getElementById('scrDropMin').value||3);
    params.set('drop_max', document.getElementById('scrDropMax').value||10);
    params.set('vol_min', document.getElementById('scrVolMin').value||1);
    params.set('vol_max', document.getElementById('scrVolMax').value||5);
    params.set('turnover_min', document.getElementById('scrToMin').value||5);
    params.set('turnover_max', document.getElementById('scrToMax').value||10);
    params.set('mc_min', document.getElementById('scrMcMin').value||50);
    params.set('mc_max', document.getElementById('scrMcMax').value||200);
    params.set('pe_max', document.getElementById('scrPeMax').value||50);

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
            <td style="color:${s.drop_pct<0?'#e60012':'#009966'}">${(s.drop_pct||0).toFixed(2)}%</td>
            <td><span class="tag tag-${(s.recommendation||'pass').toLowerCase()}">${names[(s.recommendation||'pass').toLowerCase()]}</span></td>
            <td>${s.zt_date||'--'}</td><td>${dots}</td></tr>`;
    }).join('');
    document.getElementById('scrResultBody').innerHTML = `<table class="data-table"><thead><tr><th>#</th><th>代码</th><th>名称</th><th>得分</th><th>回撤</th><th>推荐</th><th>日期</th><th>因子</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function resetScreening() {
    document.getElementById('scrGainMin').value=3; document.getElementById('scrGainMax').value=10;
    document.getElementById('scrDropMin').value=3; document.getElementById('scrDropMax').value=10;
    document.getElementById('scrVolMin').value=1; document.getElementById('scrVolMax').value=5;
    document.getElementById('scrToMin').value=5; document.getElementById('scrToMax').value=10;
    document.getElementById('scrMcMin').value=50; document.getElementById('scrMcMax').value=200;
    document.getElementById('scrPeMax').value=50;
    document.getElementById('scrResultBody').innerHTML='<p class="empty-state">设置筛选条件后点击「执行筛选」</p>';
    document.getElementById('scrResultCount').textContent='';
}

// ---- Settings ----
let factorWeights = { pullback:15,volume_trend:12,ma_alignment:12,strength:10,entry_point:10,market_cap:10,volume_ratio:8,turnover:8,pe:8,zt_quality:7 };
function setupSettingsPage() {
    document.getElementById('strategyForm').innerHTML = `
        <div class="form-row"><label>回撤区间</label><input type="number" class="input input-sm" value="5"> - <input type="number" class="input input-sm" value="10"> %</div>
        <div class="form-row"><label>PE上限</label><input type="number" class="input input-sm" value="50"></div>
        <div class="form-row"><label>量比区间</label><input type="number" class="input input-sm" value="1"> - <input type="number" class="input input-sm" value="5"></div>
        <div class="form-row"><label>换手率区间</label><input type="number" class="input input-sm" value="5"> - <input type="number" class="input input-sm" value="10"> %</div>
        <div class="form-row"><label>流通市值区间</label><input type="number" class="input input-sm" value="50"> - <input type="number" class="input input-sm" value="200"> 亿</div>
        <div class="form-row"><label>监控周期</label><input type="number" class="input input-sm" value="30"> 天</div>`;
    const names = {pullback:'回撤幅度',volume_trend:'量能趋势',ma_alignment:'均线多头',strength:'强势确认',entry_point:'尾盘买点',market_cap:'流通市值',volume_ratio:'量比',turnover:'换手率',pe:'市盈率',zt_quality:'涨停质量'};
    document.getElementById('factorWeightsForm').innerHTML = Object.entries(factorWeights).map(([k,v]) =>
        `<div class="form-row"><label>${names[k]||k}</label><input type="range" min="0" max="25" value="${v}" class="weight-slider" data-key="${k}" oninput="updateWeight(this)"><span id="wv_${k}">${v}%</span></div>`
    ).join('');
    updateWeightSum();
    // 计算下一个交易日
    const weekdays = ['周日','周一','周二','周三','周四','周五','周六'];
    const next = new Date();
    do { next.setDate(next.getDate() + 1); } while (next.getDay() === 0 || next.getDay() === 6);
    document.getElementById('cfgNextRun').textContent =
        `${next.getFullYear()}-${String(next.getMonth()+1).padStart(2,'0')}-${String(next.getDate()).padStart(2,'0')} (${weekdays[next.getDay()]}) 15:10`;
}
function updateWeight(s) { factorWeights[s.dataset.key]=parseInt(s.value); document.getElementById('wv_'+s.dataset.key).textContent=s.value+'%'; updateWeightSum(); }
function updateWeightSum() { const sum=Object.values(factorWeights).reduce((a,b)=>a+b,0); const el=document.getElementById('weightSum'); el.textContent='当前总权重: '+sum+'%'; el.className='weight-sum'+(sum!==100?' warn':''); }
async function testEmail() {
    try {
        const resp = await apiFetch('/system/test-email', { method: 'POST', timeout: 30000 });
        const data = await resp.json();
        if (data.status === 'ok') {
            alert('测试邮件已发送，请检查收件箱');
        } else {
            alert('发送失败: ' + (data.message || '未知错误'));
        }
    } catch(e) {
        alert('请求超时或后端异常，请稍后重试');
    }
}

async function manualRun() {
    const btn = event.target;
    const oldText = btn.textContent;
    btn.textContent = '筛选中，请稍候...';
    btn.disabled = true;
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
                alert(`筛选已完成\n共 ${total} 条推荐结果\nSTRONG_BUY: ${pollData.strong_buy || 0}  BUY: ${pollData.buy || 0}  WATCH: ${pollData.watch || 0}`);
                loadDashboard();
                return;
            }
            if (pollData.status === 'error') {
                alert('筛选失败: ' + (pollData.message || '未知错误'));
                return;
            }
        }
        alert('筛选超时，请查看筛选页面');
    } catch(e) {
        alert('请求异常，请检查后端是否运行');
    } finally {
        btn.textContent = oldText;
        btn.disabled = false;
    }
}
function saveConfig() { alert('配置已保存到本地存储'); }

// ---- Pagination ----
function renderPagination(cid, total, page, size, cb) {
    const p = Math.ceil(total / size);
    if (p <= 1) {
        document.getElementById(cid).innerHTML = '';
        return;
    }
    document.getElementById(cid).innerHTML = Array.from({length:p},(_,i)=>i+1).map(i => `<button class="${i===page?'active':''}" onclick="${cb.name}(${i})">${i}</button>`).join('');
}

// ---- Global ----
function refreshData() { navigateTo(location.hash.replace('#','')||'dashboard', false, true); }
function setupScreeningPage() {}
