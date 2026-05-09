/* New France — 尾盘涨停选股系统 前端逻辑 */
const API_BASE = '/api/v1';

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    updateDateTime();
    checkSystemStatus();
    loadDashboard();
    setInterval(updateDateTime, 60000);
});

// ---- Navigation ----
function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const page = item.dataset.page;
            navigateTo(page);
        });
    });
    // Handle hash changes
    window.addEventListener('hashchange', () => {
        const page = location.hash.replace('#', '') || 'dashboard';
        navigateTo(page, false);
    });
    const initPage = location.hash.replace('#', '') || 'dashboard';
    navigateTo(initPage, false);
}

function navigateTo(page, pushState = true) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const nav = document.querySelector(`[data-page="${page}"]`);
    if (nav) nav.classList.add('active');
    const pageEl = document.getElementById(`page-${page}`);
    if (pageEl) pageEl.classList.add('active');
    if (pushState) location.hash = page;
    const titles = {dashboard:'Dashboard 市场总览',watchlist:'监控列表',recommendations:'推荐结果',screening:'手动筛选',settings:'策略配置'};
    document.getElementById('pageTitle').textContent = titles[page] || page;
    if (page === 'dashboard') loadDashboard();
    if (page === 'watchlist') loadWatchlist();
    if (page === 'recommendations') loadRecommendations();
    if (page === 'screening') setupScreeningPage();
    if (page === 'settings') setupSettingsPage();
}

// ---- System Status ----
async function checkSystemStatus() {
    try {
        const resp = await fetch(`${API_BASE}/system/status`);
        const data = await resp.json();
        const dot = document.getElementById('statusDot');
        const text = document.getElementById('statusText');
        const nextRun = document.getElementById('nextRun');
        if (data.is_trading_day && data.is_trading_hours) {
            dot.className = 'status-dot';
            text.textContent = '交易中';
        } else if (data.is_trading_day) {
            dot.className = 'status-dot';
            text.textContent = '已收盘';
        } else {
            dot.className = 'status-dot off';
            text.textContent = '休市中';
        }
        nextRun.textContent = `下次执行: ${data.cron_expression}`;
    } catch (e) { console.log('System status fetch failed', e); }
}

function updateDateTime() {
    const now = new Date();
    const weekdays = ['周日','周一','周二','周三','周四','周五','周六'];
    const str = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')} ${weekdays[now.getDay()]}`;
    document.getElementById('dateDisplay').textContent = str;
}

// ---- Dashboard ----
async function loadDashboard() {
    try {
        // Watchlist stats
        const wlResp = await fetch(`${API_BASE}/watchlist/stats`);
        const wl = await wlResp.json();
        document.getElementById('metricWatchlist').textContent = wl.total || 0;
        document.getElementById('metricNewZT').textContent = wl.new_today || 0;

        // Latest screening results
        let recCount = 0;
        try {
            const recResp = await fetch(`${API_BASE}/screening/latest`);
            const rec = await recResp.json();
            if (rec.results && rec.results.length > 0) {
                recCount = rec.total_scored || rec.results.length;
                document.getElementById('metricIndex').textContent = rec.index_gain != null ? `${rec.index_gain >= 0 ? '+' : ''}${rec.index_gain.toFixed(2)}%` : '--';
                document.getElementById('metricIndex').className = 'metric-value ' + (rec.index_gain >= 0 ? 'up' : 'down');
                renderRecentRecommendations(rec.results.slice(0, 5));
                document.getElementById('ztCount').textContent = rec.total_scored;
            } else {
                document.getElementById('metricIndex').textContent = '--';
                document.getElementById('recentRecsBody').innerHTML =
                    '<tr><td colspan="6" class="empty-cell">暂无所推荐数据，运行每日筛选后出现</td></tr>';
                document.getElementById('ztCount').textContent = '0';
            }
        } catch(e) {
            document.getElementById('metricIndex').textContent = '--';
            document.getElementById('ztCount').textContent = '--';
        }

        document.getElementById('metricRecs').textContent = recCount;

        // Sector distribution
        document.getElementById('sectorDist').innerHTML = `
            <div class="sector-bar"><div class="sector-bar-label"><span>新能源</span><span>28%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:28%;background:var(--accent-gold)"></div></div></div>
            <div class="sector-bar"><div class="sector-bar-label"><span>半导体</span><span>22%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:22%;background:var(--accent-blue)"></div></div></div>
            <div class="sector-bar"><div class="sector-bar-label"><span>消费电子</span><span>18%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:18%;background:var(--semantic-up)"></div></div></div>
            <div class="sector-bar"><div class="sector-bar-label"><span>医药生物</span><span>15%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:15%;background:var(--semantic-down)"></div></div></div>
            <div class="sector-bar"><div class="sector-bar-label"><span>其他</span><span>17%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:17%;background:var(--text-muted)"></div></div></div>
        `;
    } catch (e) {
        console.error('Dashboard load error:', e);
    }
}

function renderRecentRecommendations(results) {
    const tbody = document.getElementById('recentRecsBody');
    const tagNames = {strong_buy:'STRONG BUY', buy:'BUY', watch:'WATCH', pass:'PASS'};
    tbody.innerHTML = results.map(s => `
        <tr>
            <td><span class="stock-code">${s.code}</span></td>
            <td>${s.name}</td>
            <td>${'\u2605'.repeat(Math.min(4, Math.max(1, Math.round(s.adjusted_score / 25))))} ${s.adjusted_score}</td>
            <td style="color:${s.drop_pct < 0 ? '#E74C3C' : '#27AE60'}">${(s.drop_pct || 0).toFixed(2)}%</td>
            <td><span class="tag tag-${(s.recommendation || 'pass').toLowerCase()}">${tagNames[(s.recommendation || 'pass').toLowerCase()] || s.recommendation}</span></td>
            <td>${s.zt_date || '--'}</td>
        </tr>
    `).join('');
}

// ---- Watchlist ----
let wlCurrentPage = 1;
let wlCurrentStatus = '';

async function loadWatchlist(page = 1) {
    wlCurrentPage = page;
    const search = document.getElementById('wlSearch').value.trim();
    const status = document.getElementById('wlStatus').value;
    wlCurrentStatus = status;

    let url = `${API_BASE}/watchlist?page=${page}&size=20`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (status) url += `&status=${encodeURIComponent(status)}`;

    try {
        const resp = await fetch(url);
        const data = await resp.json();
        renderWatchlistTable(data.items);
        renderPagination('wlPagination', data.total, data.page, data.size, loadWatchlist);
        // Update tab counts
        document.getElementById('wlCountAll').textContent = data.total;
    } catch (e) {
        document.getElementById('wlTableBody').innerHTML =
            '<tr><td colspan="13" class="empty-cell">加载失败，请检查后端是否运行</td></tr>';
    }
}

function renderWatchlistTable(items) {
    const tbody = document.getElementById('wlTableBody');
    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="13" class="empty-cell">暂无监控股票</td></tr>';
        return;
    }
    tbody.innerHTML = items.map((item, i) => {
        const statusTag = item.status || 'active';
        const tags = {active:'回撤中',recommended:'已推荐',expired:'已过期'};
        return `<tr>
            <td><input type="checkbox"></td>
            <td><span class="stock-code">${item.code}</span></td>
            <td>${item.name}</td>
            <td>${item.zt_date}</td>
            <td>${item.ref_price.toFixed(2)}</td>
            <td>--</td>
            <td>--</td>
            <td>--</td>
            <td>--</td>
            <td>--</td>
            <td>--</td>
            <td><span class="tag tag-${statusTag}">${tags[statusTag] || statusTag}</span></td>
            <td><button class="btn" onclick="removeWatchlistItem('${item.code}')" style="padding:4px 8px;font-size:11px;">移除</button></td>
        </tr>`;
    }).join('');
}

async function removeWatchlistItem(code) {
    if (!confirm(`确认从监控列表中移除 ${code}？`)) return;
    await fetch(`${API_BASE}/watchlist/${code}`, { method: 'DELETE' });
    loadWatchlist(wlCurrentPage);
}

function exportWatchlist() {
    window.location.href = `${API_BASE}/watchlist?format=csv`;
}

// ---- Recommendations ----
async function loadRecommendations() {
    const dateVal = document.getElementById('recDate').value;
    const level = document.getElementById('recLevel').value;

    try {
        // 优先使用 latest 端点获取 JSON 结果
        const resp = await fetch(`${API_BASE}/screening/latest`);
        const data = await resp.json();

        const listEl = document.getElementById('recList');
        if (data.results && data.results.length > 0) {
            let filtered = data.results;
            if (level) {
                filtered = filtered.filter(r => r.recommendation === level);
            }
            if (filtered.length === 0) {
                listEl.innerHTML = '<div class="empty-state"><p>该筛选条件下暂无结果</p></div>';
                document.getElementById('recStats').innerHTML = '';
                return;
            }
            renderRecommendationCards(filtered);
            document.getElementById('recStats').innerHTML = `
                <div class="metric-cards" style="grid-template-columns:repeat(4,1fr)">
                    <div class="metric-card"><div class="metric-label">总推荐数</div><div class="metric-value highlight">${data.total_scored || filtered.length}</div></div>
                    <div class="metric-card"><div class="metric-label">STRONG BUY</div><div class="metric-value up">${data.strong_buy || 0}</div></div>
                    <div class="metric-card"><div class="metric-label">BUY</div><div class="metric-value" style="color:#F39C12">${data.buy || 0}</div></div>
                    <div class="metric-card"><div class="metric-label">WATCH</div><div class="metric-value" style="color:#3498DB">${data.watch || 0}</div></div>
                </div>`;
        } else {
            listEl.innerHTML = `<div class="empty-state">
                <svg width="48" height="48" viewBox="0 0 48 48" fill="none"><circle cx="24" cy="24" r="22" stroke="#5A6680" stroke-width="2" stroke-dasharray="4 4"/></svg>
                <p>暂无推荐报告</p><p style="color:#8B95A8;font-size:13px;margin-top:4px">请先运行筛选或等待交易日自动执行</p>
            </div>`;
            document.getElementById('recStats').innerHTML = '';
        }
    } catch (e) {
        document.getElementById('recList').innerHTML = '<div class="empty-state"><p>加载失败</p></div>';
    }
}

function renderRecommendationCards(stocks) {
    const listEl = document.getElementById('recList');
    const levelColors = {STRONG_BUY: '#E74C3C', BUY: '#F39C12', WATCH: '#3498DB', PASS: '#8B95A8'};
    const levelNames = {STRONG_BUY: 'STRONG BUY', BUY: 'BUY', WATCH: 'WATCH', PASS: 'PASS'};

    let html = '';
    stocks.forEach(s => {
        const stars = '\u2605'.repeat(Math.min(4, Math.max(1, Math.round(s.adjusted_score / 25))));
        const color = levelColors[s.recommendation] || '#8B95A8';

        let factorBars = '';
        const factorOrder = ['pullback','volume_trend','ma_alignment','strength','entry_point','market_cap','volume_ratio','turnover','pe','zt_quality'];
        if (s.factors) {
            factorOrder.forEach(key => {
                const f = s.factors[key];
                if (!f) return;
                const dotCls = f.passed ? 'pass' : 'fail';
                factorBars += `<span class="factor-dot ${dotCls}" title="${f.name}: ${f.detail} (${f.score}/10)"></span>`;
            });
        }

        html += `<div class="rec-card">
            <div class="rec-card-header">
                <div class="rec-rank">${s.rank}</div>
                <div>
                    <span class="stock-code" style="font-size:15px">${s.code}</span>
                    <span style="margin-left:8px;font-size:15px;font-weight:600">${s.name}</span>
                </div>
                <div style="flex:1"></div>
                <div class="rec-stars">${stars}</div>
                <span style="font-size:20px;font-weight:700;color:${color};min-width:36px;text-align:right">${s.adjusted_score}</span>
                <span class="tag tag-${s.recommendation.toLowerCase()}">${levelNames[s.recommendation]}</span>
            </div>
            <div style="display:flex;gap:24px;margin:8px 0;font-size:13px;color:var(--text-secondary)">
                <span>回撤: <b style="color:${s.drop_pct < 0 ? '#E74C3C' : '#27AE60'}">${(s.drop_pct||0).toFixed(2)}%</b></span>
                <span>涨停日: ${s.zt_date || '--'}</span>
                <span>参考价: ${s.ref_price || '--'}</span>
            </div>
            <div>${factorBars}</div>
        </div>`;
    });
    listEl.innerHTML = html;
}

// ---- Screening ----
function setupScreeningPage() { /* No-op: form is static */ }

async function runScreening() {
    const resultBody = document.getElementById('scrResultBody');
    const countEl = document.getElementById('scrResultCount');
    resultBody.innerHTML = '<div class="empty-state"><p>筛选运行中...</p></div>';

    // 收集筛选参数
    const params = new URLSearchParams();
    const dropMin = parseFloat(document.getElementById('scrDropMin').value) || 3;
    const dropMax = parseFloat(document.getElementById('scrDropMax').value) || 10;
    const volMin = parseFloat(document.getElementById('scrVolMin').value) || 1;
    const volMax = parseFloat(document.getElementById('scrVolMax').value) || 5;
    const toMin = parseFloat(document.getElementById('scrToMin').value) || 5;
    const toMax = parseFloat(document.getElementById('scrToMax').value) || 10;
    const mcMin = parseFloat(document.getElementById('scrMcMin').value) || 50;
    const mcMax = parseFloat(document.getElementById('scrMcMax').value) || 200;
    const peMax = parseFloat(document.getElementById('scrPeMax').value) || 50;

    params.set('drop_min', dropMin); params.set('drop_max', dropMax);
    params.set('vol_min', volMin); params.set('vol_max', volMax);
    params.set('turnover_min', toMin); params.set('turnover_max', toMax);
    params.set('mc_min', mcMin); params.set('mc_max', mcMax);
    params.set('pe_max', peMax);

    try {
        const resp = await fetch(`${API_BASE}/screening/run?${params}`, { method: 'POST' });
        const data = await resp.json();

        if (data.status === 'completed' && data.results && data.results.length > 0) {
            countEl.textContent = `${data.results.length} 条结果 | 指数: ${data.index_gain || 0}%`;
            renderScreeningResults(data.results);
        } else if (data.errors && data.errors.length > 0 && data.errors[0].includes('监控列表为空')) {
            resultBody.innerHTML = '<div class="empty-state"><p>监控列表为空</p><p style="color:#8B95A8;font-size:13px;">每日自动从涨停股池添加，或等待下一个交易日</p></div>';
        } else {
            resultBody.innerHTML = '<div class="empty-state"><p>暂无符合条件的股票</p><p style="color:#8B95A8;font-size:13px;">筛选无果恰是市场救你，应果断空仓</p></div>';
        }
    } catch (e) {
        resultBody.innerHTML = `<div class="empty-state"><p>筛选失败</p><p style="color:#E74C3C;font-size:12px;">${e.message || '请确保 FastAPI 服务已启动 (python3 -m backend.main --serve)'}</p></div>`;
    }
}

function renderScreeningResults(results) {
    const body = document.getElementById('scrResultBody');
    let html = '<table class="data-table"><thead><tr><th>#</th><th>代码</th><th>名称</th><th>得分</th><th>回撤</th><th>推荐</th><th>日期</th><th>因子</th></tr></thead><tbody>';
    results.forEach(s => {
        const stars = '\u2605'.repeat(Math.min(4, Math.max(1, Math.round(s.adjusted_score / 25))));
        const tagCls = s.recommendation ? s.recommendation.toLowerCase() : 'pass';
        const tagNames = {strong_buy:'STRONG BUY', buy:'BUY', watch:'WATCH', pass:'PASS'};
        const tagName = tagNames[tagCls] || s.recommendation;

        let factorDots = '';
        if (s.factors) {
            Object.entries(s.factors).forEach(([key, f]) => {
                if (key === 'event_bonus') return;
                const cls = f.passed ? 'pass' : 'fail';
                factorDots += `<span class="factor-dot ${cls}" title="${f.name}: ${f.detail} (${f.score}/10)"></span>`;
            });
        }

        html += `<tr>
            <td>${s.rank}</td>
            <td><span class="stock-code">${s.code}</span></td>
            <td>${s.name}</td>
            <td>${stars} <b>${s.adjusted_score}</b></td>
            <td style="color:${s.drop_pct < 0 ? '#E74C3C' : '#27AE60'}">${(s.drop_pct || 0).toFixed(2)}%</td>
            <td><span class="tag tag-${tagCls}">${tagName}</span></td>
            <td>${s.zt_date || '--'}</td>
            <td>${factorDots}</td>
        </tr>`;
    });
    html += '</tbody></table>';
    body.innerHTML = html;
}

function resetScreening() {
    document.querySelectorAll('#page-screening input[type=number]').forEach(inp => {
        const defaults = {scrDropMin: 3, scrDropMax: 10, scrVolMin: 1, scrVolMax: 5,
                          scrToMin: 5, scrToMax: 10, scrMcMin: 50, scrMcMax: 200, scrPeMax: 50};
        inp.value = defaults[inp.id] || '';
    });
    document.getElementById('scrResultBody').innerHTML = '<p class="empty-state">设置筛选条件后点击「执行筛选」</p>';
    document.getElementById('scrResultCount').textContent = '';
}

// ---- Settings ----
let factorWeights = {
    pullback: 15, volume_trend: 12, ma_alignment: 12, strength: 10,
    entry_point: 10, market_cap: 10, volume_ratio: 8, turnover: 8,
    pe: 8, zt_quality: 7,
};

function setupSettingsPage() {
    // Strategy params form
    document.getElementById('strategyForm').innerHTML = `
        <div class="form-row"><label>回撤区间</label><input type="number" class="input input-sm" value="5"> - <input type="number" class="input input-sm" value="10"> %</div>
        <div class="form-row"><label>PE上限</label><input type="number" class="input input-sm" value="50"></div>
        <div class="form-row"><label>量比区间</label><input type="number" class="input input-sm" value="1"> - <input type="number" class="input input-sm" value="5"></div>
        <div class="form-row"><label>换手率区间</label><input type="number" class="input input-sm" value="5"> - <input type="number" class="input input-sm" value="10"> %</div>
        <div class="form-row"><label>流通市值区间</label><input type="number" class="input input-sm" value="50"> - <input type="number" class="input input-sm" value="200"> 亿</div>
        <div class="form-row"><label>监控周期</label><input type="number" class="input input-sm" value="30"> 天</div>
    `;

    // Factor weights
    const names = {
        pullback:'回撤幅度', volume_trend:'量能趋势', ma_alignment:'均线多头',
        strength:'强势确认', entry_point:'尾盘买点', market_cap:'流通市值',
        volume_ratio:'量比', turnover:'换手率', pe:'市盈率', zt_quality:'涨停质量',
    };
    let html = '';
    for (const [key, val] of Object.entries(factorWeights)) {
        html += `<div class="form-row">
            <label>${names[key] || key}</label>
            <input type="range" min="0" max="25" value="${val}" class="weight-slider" data-key="${key}" oninput="updateWeight(this)">
            <span class="weight-val" id="wv_${key}">${val}%</span>
        </div>`;
    }
    document.getElementById('factorWeightsForm').innerHTML = html;
    updateWeightSum();

    // Next run
    document.getElementById('cfgNextRun').textContent = '下一个交易日 15:10';
}

function updateWeight(slider) {
    const key = slider.dataset.key;
    const val = parseInt(slider.value);
    factorWeights[key] = val;
    document.getElementById(`wv_${key}`).textContent = val + '%';
    updateWeightSum();
}

function updateWeightSum() {
    const sum = Object.values(factorWeights).reduce((a,b) => a+b, 0);
    const el = document.getElementById('weightSum');
    el.textContent = `当前总权重: ${sum}%`;
    el.className = 'weight-sum' + (sum !== 100 ? ' warn' : '');
}

function testEmail() {
    alert('测试邮件功能需要在后端启动后通过 API 调用');
}

async function manualRun() {
    try {
        const resp = await fetch(`${API_BASE}/screening/run`, { method: 'POST' });
        const data = await resp.json();
        alert('筛选任务已触发: ' + data.message);
    } catch(e) {
        alert('触发失败，请确保后端服务已启动');
    }
}

async function saveConfig() {
    alert('配置保存功能开发中');
}

// ---- Pagination ----
function renderPagination(containerId, total, page, size, callback) {
    const totalPages = Math.ceil(total / size);
    if (totalPages <= 1) return;
    const el = document.getElementById(containerId);
    let html = '';
    for (let i = 1; i <= totalPages; i++) {
        html += `<button class="${i === page ? 'active' : ''}" onclick="${callback.name}(${i})">${i}</button>`;
    }
    el.innerHTML = html;
}

// ---- Global ----
function refreshData() {
    const page = location.hash.replace('#', '') || 'dashboard';
    navigateTo(page, false);
}
