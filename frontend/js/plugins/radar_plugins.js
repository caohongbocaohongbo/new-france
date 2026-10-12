/* 26 方案：舆情聚合雷达（一级菜单）通用前端渲染。
 * 复用全局 apiFetch / escapeHtml，样式沿用既有 design token 与 .panel/.data-table/.tag/.metric-card。
 */
(function () {
    'use strict';

    const LEVEL_TAG = { 'STRONG_BUY': 'tag-strong_buy', 'BUY': 'tag-buy', 'WATCH': 'tag-watch', 'HOLD': 'tag-expired' };
    const LEVEL_LABEL = { 'STRONG_BUY': 'STRONG BUY', 'BUY': 'BUY', 'WATCH': 'WATCH', 'HOLD': 'HOLD' };

    function tag(level) {
        const cls = LEVEL_TAG[level] || 'tag-watch';
        return '<span class="tag ' + cls + '">' + escapeHtml(LEVEL_LABEL[level] || level) + '</span>';
    }
    function fmt(v, digits) {
        if (v === null || v === undefined || v === '') return '--';
        const n = Number(v);
        return isFinite(n) ? n.toFixed(digits === undefined ? 2 : digits) : '--';
    }
    function clsUpDown(v) {
        if (v === null || v === undefined || v === '') return '';
        const n = Number(v);
        return n > 0 ? 'up' : (n < 0 ? 'down' : '');
    }
    function statCards(cards) {
        return '<div class="metric-cards">' + cards.map(function (c) {
            return '<div class="metric-card"><div class="metric-label">' + escapeHtml(c.label) + '</div>' +
                '<div class="metric-value ' + (c.cls || '') + '">' + escapeHtml(c.value) + '</div>' +
                (c.sub ? '<div class="metric-sub">' + escapeHtml(c.sub) + '</div>' : '') + '</div>';
        }).join('') + '</div>';
    }
    function renderTable(items, cols) {
        if (!items || !items.length) return '<div class="empty-state">暂无数据</div>';
        const head = cols.map(function (c) { return '<th>' + escapeHtml(c[1]) + '</th>'; }).join('');
        const body = items.slice(0, 100).map(function (it) {
            const tds = cols.map(function (c) {
                const key = c[0], fmtFn = c[2];
                const raw = it[key];
                let val = raw;
                if (fmtFn === 'tag') val = tag(raw);
                else if (fmtFn === 'pct') val = '<span class="' + clsUpDown(raw) + '">' + fmt(raw, 2) + '%</span>';
                else if (fmtFn === 'num') val = fmt(raw, 2);
                else if (fmtFn === 'src') val = raw && typeof raw === 'object' ? escapeHtml(Object.keys(raw).join(',')) : escapeHtml(raw || '');
                else val = escapeHtml(raw === null || raw === undefined ? '--' : String(raw));
                return '<td>' + val + '</td>';
            }).join('');
            return '<tr>' + tds + '</tr>';
        }).join('');
        return '<table class="data-table"><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table>';
    }

    // 1. 风险否决
    async function renderRiskOverride(el) {
        const resp = await apiFetch('/screening/latest', { timeout: 20000, retries: 0 });
        const data = await resp.json();
        const results = data.results || [];
        const hit = results.filter(function (r) { return r.risk_override && (r.risk_override.hits || []).length; });
        const rate = results.length ? (hit.length / results.length * 100) : 0;
        el.innerHTML =
            statCards([
                { label: '总推荐数', value: String(results.length) },
                { label: '被风险否决', value: String(hit.length), cls: 'down' },
                { label: '拦截率', value: fmt(rate, 1) + '%', cls: rate > 0 ? 'down' : '' },
                { label: '筛选日期', value: data.date || '--', sub: '主板口径' },
            ]) +
            '<div class="panel"><div class="panel-header"><h2>风险否决明细</h2><span class="table-note">仅展示被 7 条规则命中的推荐</span></div><div class="panel-body">' +
            renderTable(hit, [
                ['code', '代码'], ['name', '名称'],
                ['risk_override', '否决信息', function (v) { return v ? (v.reasons || []).join('；') : '--'; }],
                ['risk_override', '规则', function (v) { return v ? (v.hits || []).join(',') : '--'; }],
                ['risk_override', '等级变化', function (v) { return v ? (v.pre_level + '→' + v.post_level) : '--'; }],
            ]) +
            '</div></div>';
    }

    // 2. 绩效模拟盘
    async function renderPaperTrading(el) {
        const resp = await apiFetch('/paper-trading/summary', { timeout: 20000, retries: 0 });
        const data = await resp.json();
        const m = data.metrics || {};
        const acct = data.account || {};
        el.innerHTML =
            statCards([
                { label: '累计收益', value: fmt(m.total_return_pct, 2) + '%', cls: clsUpDown(m.total_return_pct) },
                { label: '胜率', value: m.win_rate === null || m.win_rate === undefined ? '--' : fmt(m.win_rate, 1) + '%' },
                { label: '盈亏比', value: m.profit_loss_ratio === null || m.profit_loss_ratio === undefined ? '--' : fmt(m.profit_loss_ratio, 2) },
                { label: '最大回撤', value: fmt(m.max_drawdown_pct, 2) + '%', cls: 'down' },
                { label: '虚拟现金', value: '¥' + fmt(acct.cash, 0), sub: '初始 ¥' + fmt(acct.initial_capital, 0) },
            ]) +
            '<div class="panel"><div class="panel-header"><h2>当前持仓</h2>' +
            '<button type="button" class="btn btn-primary btn-sm" onclick="window.radarPaperOpen()">按最新推荐建仓</button> ' +
            '<button type="button" class="btn btn-sm" onclick="window.radarPaperSettle()">T+1 结算</button></div><div class="panel-body" id="paperPosBody">' +
            renderTable(data.open_positions || [], [
                ['code', '代码'], ['name', '名称'], ['level', '等级', 'tag'],
                ['open_price', '开仓价', 'num'], ['shares', '股数', 'num'], ['open_date', '开仓日'],
            ]) +
            '</div></div>' +
            '<div class="panel"><div class="panel-header"><h2>已平仓</h2></div><div class="panel-body">' +
            renderTable(data.closed_positions || [], [
                ['code', '代码'], ['name', '名称'], ['open_price', '开仓价', 'num'], ['close_price', '平仓价', 'num'],
                ['pnl', '盈亏', 'num'], ['pnl_pct', '盈亏%', 'pct'], ['close_date', '平仓日'],
            ]) +
            '</div></div>';
    }

    // 3. 条件盯盘
    async function renderConditionalWatch(el) {
        const resp = await apiFetch('/watch/latest', { timeout: 20000, retries: 0 });
        const data = await resp.json();
        const rules = data.rules || [];
        const alerts = data.alerts || [];
        el.innerHTML =
            '<div class="panel"><div class="panel-header"><h2>新建盯盘规则</h2></div><div class="panel-body">' +
            '<div class="form-row" style="flex-wrap:wrap">' +
            '<input id="cwCode" class="input" placeholder="代码 如 600000" maxlength="6">' +
            '<input id="cwName" class="input" placeholder="名称(可选)">' +
            '<input id="cwCond" class="input" placeholder="条件 如 price >= 12.5 and turnover_rate >= 0.01">' +
            '<button type="button" class="btn btn-primary btn-sm" onclick="window.radarWatchCreate()">创建</button>' +
            '<button type="button" class="btn btn-sm" onclick="window.radarWatchCheck()">立即检查</button>' +
            '</div></div></div>' +
            '<div class="panel"><div class="panel-header"><h2>盯盘规则</h2><span class="table-note">命中后去重告警</span></div><div class="panel-body">' +
            renderTable(rules, [
                ['code', '代码'], ['name', '名称'], ['condition', '条件'], ['enabled', '启用', function (v) { return v ? '是' : '否'; }],
            ]) +
            '</div></div>' +
            '<div class="panel"><div class="panel-header"><h2>触发记录</h2></div><div class="panel-body">' +
            renderTable(alerts.slice(0, 50), [
                ['code', '代码'], ['name', '名称'], ['condition', '条件'], ['price', '触发价', 'num'],
                ['change_pct', '涨跌幅', 'pct'], ['triggered_at', '触发时间'],
            ]) +
            '</div></div>';
    }

    // 4. 舆情热度
    async function renderHotScanner(el) {
        const resp = await apiFetch('/hot/latest', { timeout: 30000, retries: 0 });
        const data = await resp.json();
        const items = data.items || [];
        el.innerHTML =
            statCards([
                { label: '热度榜条数', value: String(items.length) },
                { label: '数据源数', value: String(data.source_count || 0) },
                { label: '扫描日期', value: data.scan_date || '--', sub: data.status === 'empty' ? '尚未扫描' : '' },
                { label: '在监控列表', value: String(items.filter(function (i) { return i.in_watchlist; }).length) },
            ]) +
            '<div class="panel"><div class="panel-header"><h2>舆情热度榜 TOP50</h2>' +
            '<button type="button" class="btn btn-primary btn-sm" onclick="window.radarHotScan()">立即扫描</button></div><div class="panel-body">' +
            (items.length ? renderTable(items, [
                ['rank', '排名'], ['code', '代码'], ['name', '名称'], ['score', '热度分', 'num'],
                ['sources', '来源', 'src'], ['in_watchlist', '监控', function (v) { return v ? '是' : '否'; }],
            ]) : '<div class="empty-state">尚未扫描或无数据，点击右上角"立即扫描"（需国内 IP 访问舆情源）</div>') +
            '</div></div>';
    }

    // 5. 事件雷达
    async function renderEventRadar(el) {
        const resp = await apiFetch('/event-radar/latest', { timeout: 30000, retries: 0 });
        const data = await resp.json();
        const items = data.items || [];
        const pos = items.filter(function (i) { return i.impact_score > 0; }).length;
        const neg = items.filter(function (i) { return i.impact_score < 0; }).length;
        el.innerHTML =
            statCards([
                { label: '事件总数', value: String(items.length) },
                { label: '利好事件', value: String(pos), cls: 'up' },
                { label: '利空事件', value: String(neg), cls: 'down' },
                { label: '在监控列表', value: String(items.filter(function (i) { return i.in_watchlist; }).length) },
            ]) +
            '<div class="panel"><div class="panel-header"><h2>事件雷达 TOP50</h2>' +
            '<button type="button" class="btn btn-primary btn-sm" onclick="window.radarEventScan()">立即扫描</button></div><div class="panel-body">' +
            (items.length ? renderTable(items, [
                ['code', '代码'], ['name', '名称'], ['event_type', '事件类型'],
                ['impact_score', '影响分', function (v) { return '<span class="' + clsUpDown(v) + '">' + (Number(v) > 0 ? '+' : '') + fmt(v, 0) + '</span>'; }],
                ['title', '标题'], ['event_date', '日期'], ['in_watchlist', '监控', function (v) { return v ? '是' : '否'; }],
            ]) : '<div class="empty-state">尚未扫描或无数据，点击右上角"立即扫描"（需国内 IP 访问公告/研报源）</div>') +
            '</div></div>';
    }

    async function setupRadarPage(pageId) {
        const el = document.getElementById('page-' + pageId);
        if (!el) return;
        el.innerHTML = '<div class="loading">加载中...</div>';
        try {
            if (pageId === 'risk-override') await renderRiskOverride(el);
            else if (pageId === 'paper-trading') await renderPaperTrading(el);
            else if (pageId === 'conditional-watch') await renderConditionalWatch(el);
            else if (pageId === 'hot-scanner') await renderHotScanner(el);
            else if (pageId === 'event-radar') await renderEventRadar(el);
        } catch (e) {
            el.innerHTML = '<div class="empty-state">加载失败：' + escapeHtml(String(e)) + '</div>';
        }
    }

    // 暴露给 inline onclick 的操作函数
    function post(path) {
        return apiFetch(path, { method: 'POST', timeout: 30000, retries: 0 });
    }
    window.radarPaperOpen = function () { post('/paper-trading/run-open').then(function (r) { return r.json(); }).then(function () { setupRadarPage('paper-trading'); }).catch(function (e) { window.radarNotify('建仓失败：' + e); }); };
    window.radarPaperSettle = function () { post('/paper-trading/run-settle').then(function (r) { return r.json(); }).then(function () { setupRadarPage('paper-trading'); }).catch(function (e) { window.radarNotify('结算失败：' + e); }); };
    window.radarHotScan = function () { post('/hot/scan').then(function (r) { return r.json(); }).then(function () { setupRadarPage('hot-scanner'); }).catch(function (e) { window.radarNotify('扫描失败：' + e); }); };
    window.radarEventScan = function () { post('/event-radar/scan').then(function (r) { return r.json(); }).then(function () { setupRadarPage('event-radar'); }).catch(function (e) { window.radarNotify('扫描失败：' + e); }); };
    window.radarWatchCheck = function () { post('/watch/check').then(function (r) { return r.json(); }).then(function () { setupRadarPage('conditional-watch'); }).catch(function (e) { window.radarNotify('检查失败：' + e); }); };
    window.radarWatchCreate = function () {
        const code = document.getElementById('cwCode').value.trim();
        const name = document.getElementById('cwName').value.trim();
        const cond = document.getElementById('cwCond').value.trim();
        if (!code || !cond) { window.radarNotify('请填写代码和条件'); return; }
        apiFetch('/watch/rules', { method: 'POST', timeout: 15000, retries: 0, body: JSON.stringify({ code: code, name: name || undefined, condition: cond }) })
            .then(function (r) { return r.json(); }).then(function (d) {
                if (d.status === 'error') { window.radarNotify('创建失败：' + d.message); } else { setupRadarPage('conditional-watch'); }
            }).catch(function (e) { window.radarNotify('创建失败：' + e); });
    };
    window.radarNotify = function (msg) {
        // 页面内轻提示，不弹 alert
        const el = document.querySelector('.page.active .panel-body');
        if (el) {
            const note = document.createElement('div');
            note.className = 'table-note';
            note.textContent = msg;
            el.prepend(note);
        }
    };

    window.setupRadarPage = setupRadarPage;
    window.RADAR_PAGES = ['risk-override', 'paper-trading', 'conditional-watch', 'hot-scanner', 'event-radar'];
})();
