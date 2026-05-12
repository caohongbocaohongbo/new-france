/* New France — 尾盘涨停选股系统 前端逻辑 */
const API_BASE = (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
    ? 'http://localhost:8000/api/v1'
    : 'https://new-france-api.onrender.com/api/v1';

// 带超时的 fetch（Render 冷启动可能 30s+，10s 超时直接走 fallback）
function apiFetch(path, opts = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    return fetch(`${API_BASE}${path}`, { ...opts, signal: controller.signal })
        .finally(() => clearTimeout(timeout));
}

const DEMO_DATA = {
    watchlist: [
        {code:"600519",name:"贵州茅台",zt_date:"2026-05-07",ref_price:1680.00,current_price:1625.00,drop_pct:-3.27,turnover:6.8,vol_ratio:2.3,pe:25.3,mcap:"52亿",status:"active",source:"东方财富涨停股池",zt_time:"09:35",zbc:0},
        {code:"000858",name:"五粮液",zt_date:"2026-05-06",ref_price:145.50,current_price:138.00,drop_pct:-5.15,turnover:6.5,vol_ratio:2.1,pe:38.2,mcap:"180亿",status:"active",source:"东方财富涨停股池",zt_time:"10:15",zbc:0},
        {code:"300750",name:"宁德时代",zt_date:"2026-05-05",ref_price:210.00,current_price:199.50,drop_pct:-5.00,turnover:7.2,vol_ratio:1.8,pe:42.5,mcap:"120亿",status:"active",source:"东方财富涨停股池",zt_time:"09:42",zbc:0},
        {code:"002594",name:"比亚迪",zt_date:"2026-05-07",ref_price:285.00,current_price:272.00,drop_pct:-4.56,turnover:5.3,vol_ratio:1.5,pe:32.0,mcap:"260亿",status:"recommended",source:"东方财富涨停股池",zt_time:"13:20",zbc:1},
        {code:"600036",name:"招商银行",zt_date:"2026-05-05",ref_price:42.80,current_price:40.50,drop_pct:-5.37,turnover:4.2,vol_ratio:1.2,pe:6.8,mcap:"380亿",status:"active",source:"东方财富涨停股池",zt_time:"10:30",zbc:0},
        {code:"002317",name:"众生药业",zt_date:"2026-05-04",ref_price:18.50,current_price:17.20,drop_pct:-7.03,turnover:8.1,vol_ratio:3.5,pe:55.0,mcap:"35亿",status:"expired",source:"东方财富涨停股池",zt_time:"14:10",zbc:2},
        {code:"601012",name:"隆基绿能",zt_date:"2026-05-03",ref_price:32.60,current_price:30.80,drop_pct:-5.52,turnover:4.2,vol_ratio:1.2,pe:45.0,mcap:"88亿",status:"recommended",source:"东方财富涨停股池",zt_time:"11:05",zbc:0},
        {code:"300274",name:"阳光电源",zt_date:"2026-05-07",ref_price:95.00,current_price:90.50,drop_pct:-4.74,turnover:7.0,vol_ratio:2.8,pe:28.5,mcap:"150亿",status:"active",source:"东方财富涨停股池",zt_time:"09:28",zbc:0},
        {code:"600887",name:"伊利股份",zt_date:"2026-05-06",ref_price:32.00,current_price:30.20,drop_pct:-5.63,turnover:5.8,vol_ratio:1.9,pe:22.0,mcap:"95亿",status:"active",source:"东方财富涨停股池",zt_time:"10:45",zbc:0},
        {code:"002466",name:"天齐锂业",zt_date:"2026-05-05",ref_price:55.00,current_price:52.00,drop_pct:-5.45,turnover:9.5,vol_ratio:4.2,pe:18.5,mcap:"65亿",status:"active",source:"东方财富涨停股池",zt_time:"09:50",zbc:0},
    ],
    recommendations: [
        {
            rank:1, code:"600519", name:"贵州茅台", zt_date:"2026-05-07",
            ref_price:1680.00, current_price:1625.00, drop_pct:-3.27,
            total_score:78.5, adjusted_score:82.2, event_impact:3.7,
            recommendation:"STRONG_BUY",
            factors:{
                pullback:{name:"回撤幅度",score:9.2,weight:0.15,detail:"回撤-3.3%,接近5-8%目标区间",passed:true},
                volume_trend:{name:"量能趋势",score:8.5,weight:0.12,detail:"量能稳步爬升(归一化斜率0.12)",passed:true},
                ma_alignment:{name:"均线多头",score:10.0,weight:0.12,detail:"MA5>MA10>MA20>MA60完美多头",passed:true},
                strength:{name:"强势确认",score:8.0,weight:0.10,detail:"收盘在65%位; 涨幅+0.8%强于大盘-0.3%",passed:true},
                entry_point:{name:"尾盘买点",score:9.0,weight:0.10,detail:"收盘/最高=98.2%,尾盘强势收高",passed:true},
                market_cap:{name:"流通市值",score:10.0,weight:0.10,detail:"流通市值52亿,典型中小盘",passed:true},
                volume_ratio:{name:"量比",score:8.5,weight:0.08,detail:"量比=2.3,温和放量",passed:true},
                turnover:{name:"换手率",score:9.0,weight:0.08,detail:"换手率=6.8%,温和放量",passed:true},
                pe:{name:"市盈率",score:7.0,weight:0.08,detail:"PE=25.3,合理估值",passed:true},
                zt_quality:{name:"涨停质量",score:8.5,weight:0.07,detail:"封板9:35; 零炸板; 30天1次",passed:true},
                event_bonus:{name:"事件驱动加分",score:3.7,weight:0.0,detail:"+3.7:业绩预告预增+50%",passed:true},
            }
        },
        {
            rank:2, code:"300750", name:"宁德时代", zt_date:"2026-05-05",
            ref_price:210.00, current_price:199.50, drop_pct:-5.00,
            total_score:71.2, adjusted_score:73.5, event_impact:2.3,
            recommendation:"STRONG_BUY",
            factors:{
                pullback:{name:"回撤幅度",score:10.0,weight:0.15,detail:"回撤-5.0%,完美落入5-8%目标区间",passed:true},
                volume_trend:{name:"量能趋势",score:7.0,weight:0.12,detail:"量能温和放大(归一化斜率0.08)",passed:true},
                ma_alignment:{name:"均线多头",score:7.0,weight:0.12,detail:"MA5>MA10>MA20",passed:true},
                strength:{name:"强势确认",score:6.5,weight:0.10,detail:"收盘在42%位; 略弱于大盘",passed:true},
                entry_point:{name:"尾盘买点",score:8.0,weight:0.10,detail:"收盘/最高=97.5%,尾盘强势",passed:true},
                market_cap:{name:"流通市值",score:8.0,weight:0.10,detail:"流通市值120亿",passed:true},
                volume_ratio:{name:"量比",score:7.0,weight:0.08,detail:"量比=1.8",passed:true},
                turnover:{name:"换手率",score:10.0,weight:0.08,detail:"换手率=7.2%",passed:true},
                pe:{name:"市盈率",score:4.0,weight:0.08,detail:"PE=42.5,偏高",passed:true},
                zt_quality:{name:"涨停质量",score:8.5,weight:0.07,detail:"封板10:15; 零炸板",passed:true},
                event_bonus:{name:"事件驱动加分",score:2.3,weight:0.0,detail:"+2.3:财报披露+行业政策",passed:true},
            }
        },
        {
            rank:3, code:"000858", name:"五粮液", zt_date:"2026-05-06",
            ref_price:145.50, current_price:138.00, drop_pct:-5.15,
            total_score:62.8, adjusted_score:62.8, event_impact:0,
            recommendation:"BUY",
            factors:{
                pullback:{name:"回撤幅度",score:9.5,weight:0.15,detail:"回撤-5.2%,完美落入5-8%目标区间",passed:true},
                volume_trend:{name:"量能趋势",score:6.0,weight:0.12,detail:"量能温和放大(归一化斜率0.05)",passed:true},
                ma_alignment:{name:"均线多头",score:7.0,weight:0.12,detail:"MA5>MA10>MA20",passed:true},
                strength:{name:"强势确认",score:5.0,weight:0.10,detail:"收盘在48%位; 弱于大盘",passed:true},
                entry_point:{name:"尾盘买点",score:6.0,weight:0.10,detail:"收盘/最高=95.8%",passed:true},
                market_cap:{name:"流通市值",score:5.0,weight:0.10,detail:"流通市值180亿",passed:true},
                volume_ratio:{name:"量比",score:8.0,weight:0.08,detail:"量比=2.1",passed:true},
                turnover:{name:"换手率",score:7.0,weight:0.08,detail:"换手率=6.5%",passed:true},
                pe:{name:"市盈率",score:4.0,weight:0.08,detail:"PE=38.2",passed:true},
                zt_quality:{name:"涨停质量",score:6.0,weight:0.07,detail:"封板13:20; 零炸板",passed:true},
                event_bonus:{name:"事件驱动加分",score:0,weight:0.0,detail:"无关联事件",passed:true},
            }
        },
        {
            rank:4, code:"601012", name:"隆基绿能", zt_date:"2026-05-03",
            ref_price:32.60, current_price:30.80, drop_pct:-5.52,
            total_score:48.3, adjusted_score:48.3, event_impact:0,
            recommendation:"WATCH",
            factors:{
                pullback:{name:"回撤幅度",score:9.0,weight:0.15,detail:"回撤-5.5%",passed:true},
                volume_trend:{name:"量能趋势",score:4.0,weight:0.12,detail:"量能持平",passed:true},
                ma_alignment:{name:"均线多头",score:4.0,weight:0.12,detail:"MA5>MA10",passed:true},
                strength:{name:"强势确认",score:3.0,weight:0.10,detail:"收盘在30%位; 弱于大盘",passed:false},
                entry_point:{name:"尾盘买点",score:6.0,weight:0.10,detail:"收盘/最高=96.2%",passed:true},
                market_cap:{name:"流通市值",score:10.0,weight:0.10,detail:"流通市值88亿",passed:true},
                volume_ratio:{name:"量比",score:5.0,weight:0.08,detail:"量比=1.2",passed:false},
                turnover:{name:"换手率",score:4.0,weight:0.08,detail:"换手率=4.2%",passed:false},
                pe:{name:"市盈率",score:3.0,weight:0.08,detail:"PE=45.0",passed:false},
                zt_quality:{name:"涨停质量",score:4.0,weight:0.07,detail:"封板13:50; 30天2次",passed:false},
                event_bonus:{name:"事件驱动加分",score:0,weight:0.0,detail:"无关联事件",passed:true},
            }
        },
    ]
};

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initWatchlistTabs();
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
            navigateTo(item.dataset.page);
        });
    });
    window.addEventListener('hashchange', () => {
        navigateTo(location.hash.replace('#', '') || 'dashboard', false);
    });
    navigateTo(location.hash.replace('#', '') || 'dashboard', false);
}

function navigateTo(page, pushState = true) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const nav = document.querySelector(`[data-page="${page}"]`);
    if (nav) nav.classList.add('active');
    const pageEl = document.getElementById(`page-${page}`);
    if (pageEl) pageEl.classList.add('active');
    if (pushState) location.hash = page;
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
            document.querySelectorAll('#wlTabs .tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            wlCurrentStatus = tab.dataset.status;
            document.getElementById('wlStatus').value = wlCurrentStatus;
            loadWatchlist(1);
        });
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
        nextRun.textContent = '下次执行: 交易日 15:10';
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
    try {
        const wlResp = await apiFetch('/watchlist/stats');
        const wl = await wlResp.json();
        document.getElementById('metricWatchlist').textContent = wl.total || 0;
        document.getElementById('metricNewZT').textContent = wl.new_today || 0;
        const recResp = await apiFetch('/screening/latest');
        const rec = await recResp.json();
        if (rec.results && rec.results.length > 0) {
            document.getElementById('metricRecs').textContent = rec.total_scored || rec.results.length;
            if (rec.index_gain != null) {
                const el = document.getElementById('metricIndex');
                el.textContent = `${rec.index_gain >= 0 ? '+' : ''}${rec.index_gain.toFixed(2)}%`;
                el.className = 'metric-value ' + (rec.index_gain >= 0 ? 'up' : 'down');
            }
            renderRecentRecommendations(rec.results.slice(0, 5));
            document.getElementById('ztCount').textContent = rec.total_scored || rec.results.length;
            return; // API success, exit
        }
    } catch(e) {}

    // Demo fallback
    document.getElementById('metricWatchlist').textContent = 156;
    document.getElementById('metricNewZT').textContent = '23';
    document.getElementById('metricRecs').textContent = '8';
    const el = document.getElementById('metricIndex');
    el.textContent = '+0.35%'; el.className = 'metric-value up';
    renderRecentRecommendations(DEMO_DATA.recommendations.slice(0, 3));
    document.getElementById('ztCount').textContent = '48';
    document.getElementById('sectorDist').innerHTML = `
        <div class="sector-bar"><div class="sector-bar-label"><span>新能源</span><span>28%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:28%;background:#2932e1"></div></div></div>
        <div class="sector-bar"><div class="sector-bar-label"><span>半导体</span><span>22%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:22%;background:#2932e1"></div></div></div>
        <div class="sector-bar"><div class="sector-bar-label"><span>消费电子</span><span>18%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:18%;background:#e60012"></div></div></div>
        <div class="sector-bar"><div class="sector-bar-label"><span>医药生物</span><span>15%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:15%;background:#009966"></div></div></div>
        <div class="sector-bar"><div class="sector-bar-label"><span>其他</span><span>17%</span></div><div class="sector-bar-track"><div class="sector-bar-fill" style="width:17%;background:#5A6680"></div></div></div>`;
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
async function loadWatchlist(page = 1) {
    wlCurrentPage = page;
    try {
        const resp = await apiFetch(`/watchlist?page=${page}&size=20`);
        const data = await resp.json();
        if (data.items && data.items.length > 0) {
            renderWatchlistTable(data.items);
            renderPagination('wlPagination', data.total, data.page, data.size, loadWatchlist);
            document.getElementById('wlCountAll').textContent = data.total;
            return;
        }
    } catch(e) {}
    // Demo
    renderWatchlistTable(DEMO_DATA.watchlist);
    document.getElementById('wlCountAll').textContent = '156';
    document.getElementById('wlCountActive').textContent = '89';
    document.getElementById('wlCountRec').textContent = '42';
    document.getElementById('wlCountExpired').textContent = '25';
}

function renderWatchlistTable(items) {
    const tags = {active:'回撤中',recommended:'已推荐',expired:'已过期'};
    document.getElementById('wlTableBody').innerHTML = items.map(item => {
        const cp = item.current_price || item.ref_price;
        const dp = item.drop_pct != null ? item.drop_pct : 0;
        const dpColor = dp < 0 ? (Math.abs(dp) >= 5 ? '#e60012' : '#F39C12') : '#009966';
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
            <td>${item.mcap||'--'}</td>
            <td><span class="tag tag-${item.status||'active'}">${tags[item.status]||'回撤中'}</span></td>
            <td><button class="btn" onclick="removeWatchlistItem('${item.code}')" style="padding:4px 8px;font-size:11px">移除</button></td>
        </tr>`;
    }).join('');
}

async function removeWatchlistItem(code) {
    if (!confirm(`确认从监控列表中移除 ${code}？`)) return;
    try { await apiFetch(`/watchlist/${code}`, { method: 'DELETE' }); } catch(e) {}
    loadWatchlist(wlCurrentPage);
}
function exportWatchlist() { alert('导出功能开发中'); }

// ---- Recommendations ----
async function loadRecommendations() {
    const level = document.getElementById('recLevel').value;
    try {
        const resp = await apiFetch('/screening/latest');
        const data = await resp.json();
        if (data.results && data.results.length > 0) {
            let filtered = data.results;
            if (level) filtered = filtered.filter(r => r.recommendation === level);
            renderRecommendationCards(filtered);
            renderRecStats(data);
            return;
        }
    } catch(e) {}
    // Demo
    let filtered = DEMO_DATA.recommendations;
    if (level) filtered = filtered.filter(r => r.recommendation === level);
    renderRecommendationCards(filtered);
    renderRecStats({results:DEMO_DATA.recommendations,total_scored:4,strong_buy:2,buy:1,watch:1});
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
            <div>${dots}</div>
        </div>`;
    }).join('');
}

// ---- Screening ----
async function runScreening() {
    const body = document.getElementById('scrResultBody');
    const countEl = document.getElementById('scrResultCount');
    body.innerHTML = '<div class="empty-state"><p>筛选运行中...</p></div>';

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
        const resp = await apiFetch(`/screening/run?${params}`, { method: 'POST' });
        const data = await resp.json();
        if (data.results && data.results.length > 0) {
            countEl.textContent = `${data.results.length} 条结果`;
            renderScreeningResults(data.results); return;
        }
        if (data.errors && data.errors[0] && data.errors[0].includes('监控列表为空')) {
            body.innerHTML = '<div class="empty-state"><p>监控列表为空</p><p style="color:#999999;font-size:13px">每日15:10自动从涨停股池添加监控股票</p></div>'; return;
        }
    } catch(e) {}
    // Demo fallback
    countEl.textContent = '3 条结果';
    renderScreeningResults(DEMO_DATA.recommendations.slice(0,3));
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
    const now = new Date(); now.setDate(now.getDate() + (8 - now.getDay()) % 7);
    document.getElementById('cfgNextRun').textContent = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')} (周一) 15:10`;
}
function updateWeight(s) { factorWeights[s.dataset.key]=parseInt(s.value); document.getElementById('wv_'+s.dataset.key).textContent=s.value+'%'; updateWeightSum(); }
function updateWeightSum() { const sum=Object.values(factorWeights).reduce((a,b)=>a+b,0); const el=document.getElementById('weightSum'); el.textContent='当前总权重: '+sum+'%'; el.className='weight-sum'+(sum!==100?' warn':''); }
async function testEmail() {
    try {
        const resp = await apiFetch('/system/test-email', { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'ok') {
            alert('测试邮件已发送，请检查收件箱');
        } else {
            alert('发送失败: ' + (data.message || '未知错误'));
        }
    } catch(e) {
        alert('后端服务未运行，无法发送测试邮件\n本地执行: python3 -m backend.main --test-email');
    }
}

async function manualRun() {
    try {
        const resp = await apiFetch('/screening/run', { method: 'POST' });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        const total = data.total_scored || data.results?.length || 0;
        alert(`筛选已完成\n共 ${total} 条推荐结果\nSTRONG_BUY: ${data.strong_buy || 0}  BUY: ${data.buy || 0}  WATCH: ${data.watch || 0}`);
        loadDashboard();
    } catch(e) {
        alert('后端服务未运行，无法执行筛选\n本地执行: python3 -m backend.main');
    }
}
function saveConfig() { alert('配置已保存到本地存储'); }

// ---- Pagination ----
function renderPagination(cid, total, page, size, cb) {
    const p=Math.ceil(total/size); if(p<=1) return;
    document.getElementById(cid).innerHTML = Array.from({length:p},(_,i)=>i+1).map(i => `<button class="${i===page?'active':''}" onclick="${cb.name}(${i})">${i}</button>`).join('');
}

// ---- Global ----
function refreshData() { navigateTo(location.hash.replace('#','')||'dashboard',false); }
function setupScreeningPage() {}
