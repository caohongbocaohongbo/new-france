/* 14 方案新功能通用前端渲染（独立插件，复用 apiFetch/escapeHtml）。 */
(function () {
    'use strict';
    const PAGES = {
        'emotion': { path: '/emotion/latest', title: '情绪周期', kind: 'emotion' },
        'lhb': { path: '/lhb/latest', title: '龙虎榜', kind: 'lhb' },
        'tail-raid': { path: '/tail-raid/latest', title: '尾盘抢筹', kind: 'table', key: 'items',
            cols: [['code','代码'],['name','名称'],['price','价格'],['change_pct','涨幅'],['volume_ratio','量比'],['main_inflow_ratio','主力占比'],['score','尾盘分']] },
        'board': { path: '/board/latest', title: '板块轮动', kind: 'table', key: 'items',
            cols: [['name','板块'],['change_pct','涨幅'],['net_inflow','主力净流入'],['zt_count','涨停家数'],['max_height','最高连板'],['score','板块分'],['stage','阶段'],['mainline','主线']] },
        'zt-seal': { path: '/zt-seal/latest', title: '涨停封单', kind: 'table', key: 'items',
            cols: [['code','代码'],['name','名称'],['seal_amount','封单额'],['seal_vol','封单量'],['seal_ratio','封成比'],['break_count','炸板次数']] },
        'volume-profile': { path: '/volume-profile/latest', title: '分价成本带', kind: 'table', key: 'items',
            cols: [['code','代码'],['name','名称'],['vwap','VWAP'],['poc_price','最大量价'],['profit_ratio','获利盘比例']] },
        'factor-lab': { path: '/factor-lab/stats/latest', title: '因子实验室', kind: 'table', key: 'items',
            cols: [['factor','因子'],['sample_count','样本数'],['ic','IC'],['ir','IR']] },
        'l2': { path: '/l2/latest', title: '真实 L2 升级', kind: 'l2' },
        'tier-flow': { path: '/principal-capital/tier-flow/latest', title: '分层资金流', kind: 'table', key: 'items',
            cols: [['code','代码'],['name','名称'],['super_net','超大单净'],['big_net','大单净'],['smart_ratio','聪明钱占比'],['state','状态']] },
        'low-position': { path: '/low-position/latest', title: '低位涨停选股', kind: 'table', key: 'items',
            cols: [['code','代码'],['name','名称'],['price','价格'],['pullback_pct','回撤'],['price_percentile','百分位'],['zt_count_250d','涨停次数'],['low_score','低位分']] },
        'resonance': { path: '/resonance/latest', title: '四维共振', kind: 'resonance' },
    };

    function fmt(v) {
        if (v === null || v === undefined || v === '') return '--';
        const n = Number(v);
        if (!Number.isFinite(n)) return escapeHtml(String(v));
        if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿';
        return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    }

    function signalBadge(signal) {
        var color = { RED: '#dc2626', GREEN: '#16a34a', YELLOW: '#ca8a04' }[signal] || '#64748b';
        return '<span class="badge" style="background:' + color + ';color:#fff">' + escapeHtml(String(signal || '--')) + '</span>';
    }

    function fmtPct(v) {
        if (v === null || v === undefined || v === '') return '--';
        var n = Number(v);
        if (!Number.isFinite(n)) return '--';
        return (n * 100).toFixed(1) + '%';
    }

    function renderTable(items, cols) {
        if (!items || !items.length) return '<div class="empty-state">暂无数据，请先运行对应任务</div>';
        let head = cols.map(function (c) { return '<th>' + escapeHtml(c[1]) + '</th>'; }).join('');
        let body = items.slice(0, 100).map(function (it) {
            let tds = cols.map(function (c) {
                let v = it[c[0]];
                if (c[0] === 'mainline') return '<td>' + (v ? '✅' : '—') + '</td>';
                if (c[0] === 'signal') return '<td>' + signalBadge(v) + '</td>';
                if (c[0] === 'stage' || c[0] === 'd1_state') return '<td><span class="badge">' + escapeHtml(String(v == null ? '--' : v)) + '</span></td>';
                if (c[0] === 'pullback_pct' || c[0] === 'price_percentile') return '<td>' + fmtPct(v) + '</td>';
                return '<td>' + fmt(v) + '</td>';
            }).join('');
            return '<tr>' + tds + '</tr>';
        }).join('');
        return '<table class="data-table"><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table>';
    }

    function renderEmotion(d) {
        if (!d || d.status !== 'completed') return renderEmpty(d);
        const m = d.metrics || {};
        const ladder = m.ladder || {};
        const gauge = d.score == null ? '--' : Number(d.score).toFixed(1);
        return '<div class="metric-cards">' +
            card('情绪得分', gauge) + card('情绪阶段', d.regime) + card('涨停家数', m.zt_count) + card('空间板高度', m.max_height) +
            '</div>' +
            '<div class="metric-cards" style="margin-top:12px">' +
            card('炸板率', m.break_rate != null ? m.break_rate + '%' : '--') + card('晋级率', m.promotion_rate != null ? m.promotion_rate + '%' : '--') +
            card('首板', ladder.first) + card('2板', ladder.second) + card('3板', ladder.third) + card('4板+', ladder.higher) +
            '</div>' +
            '<div class="hint">操作结论：' + escapeHtml(d.action || '--') + '，建议仓位 ' + escapeHtml(d.position || '--') + '</div>';
    }

    function renderL2(d) {
        if (!d || d.status !== 'completed') return renderEmpty(d);
        let rows = (d.sources || []).map(function (s) {
            return '<tr><td>' + escapeHtml(s.source) + '</td><td>' + escapeHtml(s.capability) + '</td><td>' + escapeHtml(s.cost) + '</td><td>' + escapeHtml(s.fit) + '</td></tr>';
        }).join('');
        return '<div class="hint">' + escapeHtml(d.conclusion || '') + '</div>' +
            '<div class="hint">' + escapeHtml(d.recommended_architecture || '') + '</div>' +
            '<table class="data-table"><thead><tr><th>方案</th><th>能力</th><th>成本/门槛</th><th>适用</th></tr></thead><tbody>' + rows + '</tbody></table>';
    }

    function renderLhb(d) {
        if (!d || d.status !== 'completed') return renderEmpty(d);
        let stockRows = (d.items || []).slice(0, 100).map(function (it) {
            return '<tr><td>' + escapeHtml(it.code) + '</td><td>' + escapeHtml(it.name) + '</td><td>' + fmt(it.net_buy) + '</td><td>' + escapeHtml(String(it.reason || '')) + '</td></tr>';
        }).join('');
        let seatRows = (d.seat_stats || []).slice(0, 30).map(function (s) {
            return '<tr><td>' + escapeHtml(s.normalized_name) + '</td><td>' + escapeHtml(s.seat_type || '') + '</td><td>' + escapeHtml(s.known_label || '') + '</td><td>' + s.appearances + '</td><td>' + fmt(s.total_amount) + '</td></tr>';
        }).join('');
        return '<h3 class="section-title">上榜个股（' + (d.count || 0) + '）</h3>' +
            '<table class="data-table"><thead><tr><th>代码</th><th>名称</th><th>净买额</th><th>上榜原因</th></tr></thead><tbody>' + (stockRows || '<tr><td colspan="4">暂无</td></tr>') + '</tbody></table>' +
            '<h3 class="section-title">席位画像（Top 30）</h3>' +
            '<table class="data-table"><thead><tr><th>席位</th><th>类型</th><th>标签</th><th>上榜次数</th><th>金额</th></tr></thead><tbody>' + (seatRows || '<tr><td colspan="5">暂无</td></tr>') + '</tbody></table>';
    }

    function renderEmpty(d) {
        const msg = (d && d.reason) || (d && d.note) || '暂无数据，请先运行对应任务';
        return '<div class="empty-state">' + escapeHtml(msg) + '</div>';
    }

    function card(label, value) {
        return '<div class="metric-card"><div class="metric-label">' + escapeHtml(label) + '</div><div class="metric-value">' + escapeHtml(String(value == null ? '--' : value)) + '</div></div>';
    }

    // ==== 17 四维共振：upgrade_hint banner + 可点击表格 + 详情图表（K线副图 + 共振分副图） ====
    function renderResonance(d) {
        if (!d || d.status !== 'completed') return renderEmpty(d);
        var banner = '';
        if (d.upgrade_hint) {
            banner = '<div class="upgrade-hint-banner">' + escapeHtml(d.upgrade_hint) + '</div>';
        }
        var stats = '';
        if (d.signal_counts) {
            stats = '<div class="metric-cards">' +
                card('红灯', d.signal_counts.RED) + card('黄灯', d.signal_counts.YELLOW) +
                card('绿灯', d.signal_counts.GREEN) + card('盘中实时', d.is_intraday ? '是' : '否') +
                '</div>';
        }
        var items = d.items || [];
        var body = items.map(function (it) {
            return '<tr class="resonance-row" data-code="' + escapeHtml(String(it.code || '')) + '" data-name="' + escapeHtml(String(it.name || '')) + '" style="cursor:pointer">' +
                '<td>' + escapeHtml(String(it.code || '')) + '</td>' +
                '<td>' + escapeHtml(String(it.name || '')) + '</td>' +
                '<td>' + fmt(it.resonance_score) + '</td>' +
                '<td>' + signalBadge(it.signal) + '</td>' +
                '<td><span class="badge">' + escapeHtml(String(it.d1_state || '--')) + '</span></td>' +
                '<td>' + fmt(it.d1_score) + '</td>' +
                '<td>' + fmt(it.d2_score) + '</td>' +
                '<td>' + fmt(it.d3_score) + '</td>' +
                '<td>' + fmt(it.d4_score) + '</td>' +
                '</tr>';
        }).join('');
        return banner + stats +
            '<table class="data-table"><thead><tr><th>代码</th><th>名称</th><th>共振分</th><th>信号</th><th>D1状态</th><th>D1</th><th>D2</th><th>D3</th><th>D4</th></tr></thead><tbody>' +
            (body || '<tr><td colspan="9">暂无数据</td></tr>') + '</tbody></table>' +
            '<div id="resonance-detail"></div>';
    }

    function bindResonanceRows() {
        document.querySelectorAll('.resonance-row').forEach(function (tr) {
            tr.addEventListener('click', function () {
                openResonanceDetail(tr.getAttribute('data-code'), tr.getAttribute('data-name'));
            });
        });
    }

    window.closeResonanceDetail = function () {
        var el = document.getElementById('resonance-detail');
        if (el) el.innerHTML = '';
    };

    async function openResonanceDetail(code, name) {
        var el = document.getElementById('resonance-detail');
        if (!el) return;
        el.innerHTML = '<div class="loading">加载 ' + escapeHtml(code) + ' 历史...</div>';
        try {
            var p1 = apiFetch('/resonance/' + encodeURIComponent(code), { timeout: 20000, retries: 0 });
            var p2 = apiFetch('/resonance/' + encodeURIComponent(code) + '/kline?days=60', { timeout: 20000, retries: 0 });
            var results = await Promise.all([p1, p2]);
            var hData = await results[0].json();
            var kData = await results[1].json();
            el.innerHTML = renderResonanceDetail(code, name, hData.records || [], kData.records || []);
            drawResonanceKline(code, kData.records || []);
            drawResonanceScore(code, hData.records || []);
        } catch (e) {
            el.innerHTML = '<div class="empty-state">加载失败：' + escapeHtml(String(e)) + '</div>';
        }
    }

    function renderResonanceDetail(code, name, history, kline) {
        var back = '<button class="btn" style="margin-bottom:10px" onclick="closeResonanceDetail()">← 返回</button>';
        if (!history.length) {
            return back + '<div class="empty-state">暂无历史数据（新上线第一天，积累数据后显示折线）</div>';
        }
        var klineNote = kline.length ? '' : '<div class="hint">无K线数据（或 K线源不可用），仅显示共振分副图。</div>';
        return back +
            '<h3 class="section-title">' + escapeHtml(code) + ' ' + escapeHtml(name) + ' · 四维共振 + K线副图</h3>' +
            klineNote +
            '<div id="res-chart-kline" style="width:100%;height:280px"></div>' +
            '<div id="res-chart-score" style="width:100%;height:320px"></div>' +
            '<div class="hint">提示：信号仅为辅助参考，不构成投资建议。</div>';
    }

    function drawResonanceKline(code, kline) {
        var el = document.getElementById('res-chart-kline');
        if (!el || typeof echarts === 'undefined') return;
        if (!kline || !kline.length) { el.innerHTML = ''; return; }
        var chart = echarts.init(el);
        var dates = kline.map(function (k) { return (k.date || '').slice(5); });
        var ohlc = kline.map(function (k) { return [k.open, k.close, k.low, k.high]; });
        chart.setOption({
            tooltip: { trigger: 'axis' },
            xAxis: { type: 'category', data: dates },
            yAxis: { scale: true },
            grid: { left: 55, right: 16, top: 16, bottom: 24 },
            series: [{
                type: 'candlestick', data: ohlc,
                itemStyle: { color: '#ef4444', color0: '#16a34a', borderColor: '#ef4444', borderColor0: '#16a34a' },
            }],
        });
        window.addEventListener('resize', function () { chart.resize(); });
    }

    function drawResonanceScore(code, history) {
        var el = document.getElementById('res-chart-score');
        if (!el || typeof echarts === 'undefined') return;
        var chart = echarts.init(el);
        var dates = history.map(function (h) { return (h.date || '').slice(5); });
        var colors = history.map(function (h) {
            return { RED: '#fca5a5', GREEN: '#86efac', YELLOW: '#fde68a' }[h.signal] || '#f1f5f9';
        });
        chart.setOption({
            tooltip: { trigger: 'axis' },
            legend: { data: ['共振分', 'D1', 'D2', 'D3', 'D4'] },
            xAxis: { type: 'category', data: dates },
            yAxis: { type: 'value', min: 0, max: 100 },
            grid: { left: 40, right: 16, top: 40, bottom: 24 },
            series: [
                { name: '共振分', type: 'bar', data: history.map(function (h) { return h.resonance_score; }),
                  itemStyle: { color: function (p) { return colors[p.dataIndex]; } } },
                { name: 'D1', type: 'line', smooth: true, data: history.map(function (h) { return h.d1_score; }) },
                { name: 'D2', type: 'line', smooth: true, data: history.map(function (h) { return h.d2_score; }) },
                { name: 'D3', type: 'line', smooth: true, data: history.map(function (h) { return h.d3_score; }) },
                { name: 'D4', type: 'line', smooth: true, data: history.map(function (h) { return h.d4_score; }) },
            ],
        });
        window.addEventListener('resize', function () { chart.resize(); });
    }

    async function setupResearchPage(pageId) {
        const cfg = PAGES[pageId];
        const el = document.getElementById('page-' + pageId);
        if (!el || !cfg) return;
        el.innerHTML = '<div class="loading">加载中...</div>';
        try {
            const resp = await apiFetch(cfg.path, { timeout: 15000, retries: 0 });
            const data = await resp.json();
            if (cfg.kind === 'emotion') el.innerHTML = renderEmotion(data);
            else if (cfg.kind === 'l2') el.innerHTML = renderL2(data);
            else if (cfg.kind === 'lhb') el.innerHTML = renderLhb(data);
            else if (cfg.kind === 'resonance') { el.innerHTML = renderResonance(data); bindResonanceRows(); }
            else el.innerHTML = renderTable(data[cfg.key], cfg.cols);
        } catch (e) {
            el.innerHTML = '<div class="empty-state">加载失败：' + escapeHtml(String(e)) + '</div>';
        }
    }

    window.setupResearchPage = setupResearchPage;
    window.RESEARCH_PAGES = Object.keys(PAGES);
})();
