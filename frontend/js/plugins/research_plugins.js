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
        'smart-picker': { title: '智能选股器', kind: 'smart-picker' },
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

    // ==== 智能选股器（22 聚合中枢：单端点总榜 + 共振 + 质量卡 + 详情图表） ====
    let _spItems = [];
    let _spItemMap = {};
    let _spState = { q: "", pool: "all", min_hit: 1, sort: "hub_score", order: "desc", market: "main", limit: 50, offset: 0 };
    const _SP_POOLS = [
        { k: "all", label: "总榜" },
        { k: "resonance", label: "🔥 共振池" },
        { k: "tech", label: "经典技术指标" },
        { k: "trend", label: "趋势强度" },
        { k: "pattern", label: "形态突破" },
        { k: "chip", label: "筹码集中度" },
    ];
    const _SP_STRAT_LABEL = { tech: "经典技术指标", trend: "趋势强度", pattern: "形态突破", chip: "筹码集中度" };

    function _spQstr() {
        var s = _spState;
        var parts = ["limit=" + s.limit, "offset=" + s.offset, "pool=" + s.pool,
                     "min_hit=" + s.min_hit, "sort=" + s.sort, "order=" + s.order, "market=" + s.market];
        if (s.q) parts.push("q=" + encodeURIComponent(s.q));
        return parts.join("&");
    }

    async function _spFetch() {
        var resp = await apiFetch("/smart-picker/latest?" + _spQstr(), { timeout: 15000, retries: 0 });
        return await resp.json();
    }

    async function setupSmartPicker(el) {
        el.innerHTML = '<div class="loading">加载中...</div>';
        try {
            var data = await _spFetch();
            _spItems = data.items || [];
            _spItemMap = {};
            _spItems.forEach(function (it) { _spItemMap[String(it.code)] = it; });
            el.innerHTML = _spRenderAll(data);
            bindSmartPickerControls();
            bindSmartPickerRows();
            loadSmartPickerPerf();
        } catch (e) {
            el.innerHTML = '<div class="empty-state">加载失败：' + escapeHtml(String(e)) + '</div>';
        }
    }

    async function _spReload() {
        var box = document.getElementById("sp-table-wrap");
        if (box) box.innerHTML = '<div class="loading">加载中...</div>';
        try {
            var data = await _spFetch();
            _spItems = data.items || [];
            _spItemMap = {};
            _spItems.forEach(function (it) { _spItemMap[String(it.code)] = it; });
            if (box) box.innerHTML = _spRenderTable(data);
            bindSmartPickerRows();
        } catch (e) {
            if (box) box.innerHTML = '<div class="empty-state">加载失败：' + escapeHtml(String(e)) + '</div>';
        }
    }

    function _spPct(v) {
        return (v == null) ? "--" : Number(v).toFixed(1) + "%";
    }

    function _spHintHtml(data) {
        var hints = [];
        if (data.status === "degraded") {
            hints.push("部分策略不可用（已按权重自动重分配）：" + Object.keys(data.strategies || {})
                .filter(function (k) { return !(data.strategies[k] || {}).available; })
                .map(function (k) { return _SP_STRAT_LABEL[k] || k; }).join("、"));
        }
        if (!data.zt_gate_applied && data.status === "completed") hints.push("涨停门控未生效（zt_pool 不可用），结果可能含当日涨停票");
        if (data.data_age_days != null) hints.push("数据 " + (data.data_age_days === 0 ? "当日" : data.data_age_days + " 个交易日前"));
        if (data.source === "snapshot") hints.push("来源：data-snapshots 兜底快照");
        return hints.length ? '<div class="hint">' + hints.map(escapeHtml).join(" · ") + "</div>" : "";
    }

    function _spBadgesHtml(it) {
        var b = it.badges || {};
        var parts = [];
        if (b.tier_state) parts.push('<span class="badge">' + escapeHtml(b.tier_state) + "</span>");
        if (b.fund_flow && b.fund_flow.main_net_inflow != null) {
            var amt = Number(b.fund_flow.main_net_inflow);
            parts.push('<span class="badge">净流入 ' + (Math.abs(amt) >= 1e8 ? (amt / 1e8).toFixed(2) + "亿" : (amt / 1e4).toFixed(0) + "万") + "</span>");
        }
        if (b.radar && b.radar.strength != null) parts.push('<span class="badge">雷达 ' + Number(b.radar.strength).toFixed(0) + "</span>");
        return parts.join(" ");
    }

    function _spRenderTable(data) {
        if (!data || data.status !== "completed") {
            return '<div class="empty-state">' + escapeHtml((data && data.reason) || "暂无数据，请先运行任务") + "</div>";
        }
        if (!data.items || !data.items.length) return '<div class="empty-state">当前筛选条件无命中</div>';
        var body = data.items.map(function (it) {
            var fire = it.resonance ? "🔥 " : "";
            return '<tr class="sp-row' + (it.resonance ? " sp-flag-row" : "") + '" data-sp-code="' + escapeHtml(String(it.code)) +
                '" data-sp-name="' + escapeHtml(String(it.name || "")) + '" style="cursor:pointer">' +
                "<td>" + escapeHtml(fire + String(it.code || "")) + "</td>" +
                "<td>" + escapeHtml(String(it.name || "")) + "</td>" +
                "<td>" + fmt(it.price) + "</td>" +
                "<td>" + fmtPct(it.change_pct) + "</td>" +
                "<td>" + (it.hit_strategies || 0) + "</td>" +
                "<td>" + fmt(it.hub_score) + " <span class='muted'>" + _spPct(it.hub_score_pct) + "</span></td>" +
                "<td>" + _spBadgesHtml(it) + "</td>" +
                "</tr>";
        }).join("");
        return '<table class="data-table"><thead><tr>' +
            "<th>代码</th><th>名称</th><th>价格</th><th>涨跌幅</th><th>命中策略</th><th>综合分(排名)</th><th>交叉信号</th>" +
            "</tr></thead><tbody>" + body + "</tbody></table>";
    }

    function _spRenderAll(data) {
        var counts = data.signal_counts || {};
        var by = counts.by_strategy || {};
        var cards = '<div class="metric-cards">' +
            card("总命中", (data.count != null ? data.count : counts.total) || 0) +
            card("🔥 共振池", counts.resonance || 0) +
            card("tech", by.tech || 0) + card("trend", by.trend || 0) +
            card("pattern", by.pattern || 0) + card("chip", by.chip || 0) + "</div>";
        var pools = _SP_POOLS.map(function (p) {
            return '<button class="tab-btn' + (_spState.pool === p.k ? " active" : "") + '" data-sp-pool="' + p.k + '">' + p.label + "</button>";
        }).join("");
        var sortOpts = ["hub_score:综合分", "hit_strategies:命中策略数", "price:价格", "change_pct:涨跌幅",
                        "total_amount:成交额", "code:代码"].map(function (kv) {
            var parts = kv.split(":");
            return '<option value="' + parts[0] + '"' + (_spState.sort === parts[0] ? " selected" : "") + ">" + parts[1] + "</option>";
        }).join("");
        var marketOpts = ["main:主板", "gem:主板+创业板", "star:主板+科创板"].map(function (kv) {
            var parts = kv.split(":");
            return '<option value="' + parts[0] + '"' + (_spState.market === parts[0] ? " selected" : "") + ">" + parts[1] + "</option>";
        }).join("");
        return '<div class="smart-picker-tabs">' + pools + "</div>" +
            '<div class="sp-filters">' +
            '<input id="sp-q" type="text" placeholder="搜索代码/名称" value="' + escapeHtml(_spState.q) + '">' +
            '<select id="sp-min-hit">' + [1, 2, 3, 4].map(function (n) {
                return '<option value="' + n + '"' + (_spState.min_hit === n ? " selected" : "") + ">≥" + n + " 策略</option>";
            }).join("") + "</select>" +
            '<select id="sp-sort">' + sortOpts + "</select>" +
            '<select id="sp-order"><option value="desc" selected>降序</option><option value="asc">升序</option></select>' +
            '<select id="sp-market">' + marketOpts + "</select>" +
            '<button id="sp-prev" class="btn">上一页</button><button id="sp-next" class="btn">下一页</button>' +
            "</div>" +
            _spHintHtml(data) + cards +
            '<div id="sp-perf" class="sp-perf"></div>' +
            '<div id="sp-table-wrap">' + _spRenderTable(data) + "</div>" +
            '<div id="sp-detail"></div>' +
            '<div class="hint">' + escapeHtml(data.disclaimer || "仅为辅助参考，不构成投资建议") + "</div>";
    }

    function bindSmartPickerControls() {
        document.querySelectorAll(".smart-picker-tabs .tab-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                _spState.pool = btn.getAttribute("data-sp-pool");
                _spState.offset = 0;
                document.querySelectorAll(".smart-picker-tabs .tab-btn").forEach(function (b) {
                    b.classList.toggle("active", b.getAttribute("data-sp-pool") === _spState.pool);
                });
                _spReload();
            });
        });
        var q = document.getElementById("sp-q");
        if (q) q.addEventListener("change", function () { _spState.q = q.value.trim(); _spState.offset = 0; _spReload(); });
        var mh = document.getElementById("sp-min-hit");
        if (mh) mh.addEventListener("change", function () { _spState.min_hit = parseInt(mh.value, 10); _spState.offset = 0; _spReload(); });
        var st = document.getElementById("sp-sort");
        if (st) st.addEventListener("change", function () { _spState.sort = st.value; _spState.offset = 0; _spReload(); });
        var od = document.getElementById("sp-order");
        if (od) od.addEventListener("change", function () { _spState.order = od.value; _spState.offset = 0; _spReload(); });
        var mk = document.getElementById("sp-market");
        if (mk) mk.addEventListener("change", function () { _spState.market = mk.value; _spState.offset = 0; _spReload(); });
        var prev = document.getElementById("sp-prev");
        if (prev) prev.addEventListener("click", function () { _spState.offset = Math.max(0, _spState.offset - _spState.limit); _spReload(); });
        var next = document.getElementById("sp-next");
        if (next) next.addEventListener("click", function () { _spState.offset += _spState.limit; _spReload(); });
    }

    async function loadSmartPickerPerf() {
        var box = document.getElementById("sp-perf");
        if (!box) return;
        try {
            var resp = await apiFetch("/smart-picker/perf?days=20&window=t1", { timeout: 15000, retries: 0 });
            var data = await resp.json();
            var rows = ["tech", "trend", "pattern", "chip", "resonance"].map(function (k) {
                var s = (data.summary || {})[k];
                if (!s) return "<tr><td>" + escapeHtml(k) + "</td><td>--</td><td>--</td><td>--</td></tr>";
                var ret = s.insufficient ? "样本偏少(" + s.n + "/" + (data.sample_min || 20) + ")" : _spPct(s.avg_ret * 100);
                var win = s.insufficient ? "--" : _spPct(s.win_rate * 100);
                return "<tr><td>" + escapeHtml(k) + "</td><td>" + s.n + "</td><td>" + ret + "</td><td>" + win + "</td></tr>";
            }).join("");
            box.innerHTML = '<h4 class="section-title">信号质量卡（近 ' + (data.days || 20) + ' 交易日 T+1）</h4>' +
                '<table class="data-table"><thead><tr><th>策略</th><th>样本数</th><th>平均收益</th><th>胜率</th></tr></thead><tbody>' +
                rows + "</tbody></table>";
        } catch (e) {
            box.innerHTML = "";
        }
    }
    // ==== 智能选股器详情（22 聚合中枢：命中解释 + 预计算图表，复用 17 ECharts 模式） ====
    function _spDates(records) {
        return (records || []).map(function (k) { return (k.date || "").slice(5); });
    }

    function _spKlineSeries(records) {
        return {
            dates: _spDates(records),
            ohlc: (records || []).map(function (k) { return [k.open, k.close, k.low, k.high]; }),
        };
    }

    window.closeSmartPickerDetail = function () {
        var el = document.getElementById("sp-detail");
        if (el) el.innerHTML = "";
    };

    function bindSmartPickerRows() {
        document.querySelectorAll(".sp-row").forEach(function (tr) {
            tr.addEventListener("click", function () {
                openSmartPickerDetail(tr.getAttribute("data-sp-code"), tr.getAttribute("data-sp-name"));
            });
        });
    }

    function _spMetaHtml(item) {
        if (!item) return "";
        var lines = [];
        Object.keys(_SP_STRAT_LABEL).forEach(function (k) {
            var hit = (item.hits || {})[k];
            if (!hit) return;
            lines.push("<li><b>" + _SP_STRAT_LABEL[k] + "</b>：" + escapeHtml(hit.explain || "") + "</li>");
        });
        return '<div class="hint">命中 ' + (item.hit_strategies || 0) + " 个策略（综合分 " + fmt(item.hub_score) +
            "，当日排名前 " + _spPct(Math.max(0, 100 - (item.hub_score_pct || 0))) + "）</div><ul>" + lines.join("") + "</ul>";
    }

    function _drawTechKline(records, series) {
        var el = document.getElementById("sp-chart-1");
        if (!el || typeof echarts === "undefined") return;
        var chart = echarts.init(el);
        var ma = (series && series.ma) || {};
        var k = _spKlineSeries(records);
        chart.setOption({
            tooltip: { trigger: "axis" },
            legend: { data: ["MA5", "MA20"] },
            xAxis: { type: "category", data: k.dates },
            yAxis: { scale: true },
            grid: { left: 55, right: 16, top: 32, bottom: 24 },
            series: [
                { name: "K线", type: "candlestick", data: k.ohlc, itemStyle: { color: "#ef4444", color0: "#16a34a", borderColor: "#ef4444", borderColor0: "#16a34a" } },
                { name: "MA5", type: "line", data: ma.ma5 || [], showSymbol: false, smooth: true, lineStyle: { width: 1 } },
                { name: "MA20", type: "line", data: ma.ma20 || [], showSymbol: false, smooth: true, lineStyle: { width: 1 } },
            ],
        });
    }

    function _drawTechMacd(records, series) {
        var el = document.getElementById("sp-chart-2");
        if (!el || typeof echarts === "undefined") return;
        var chart = echarts.init(el);
        var macd = (series && series.macd) || {};
        var k = _spKlineSeries(records);
        chart.setOption({
            tooltip: { trigger: "axis" },
            legend: { data: ["DIF", "DEA", "MACD"] },
            xAxis: { type: "category", data: k.dates },
            yAxis: { type: "value" },
            grid: { left: 55, right: 16, top: 32, bottom: 24 },
            series: [
                { name: "MACD", type: "bar", data: macd.hist || [], itemStyle: { color: function (p) { return (p.value >= 0) ? "#ef4444" : "#16a34a"; } } },
                { name: "DIF", type: "line", data: macd.dif || [], showSymbol: false, lineStyle: { width: 1 } },
                { name: "DEA", type: "line", data: macd.dea || [], showSymbol: false, lineStyle: { width: 1 } },
            ],
        });
    }

    function _drawTechKdj(records, series) {
        var el = document.getElementById("sp-chart-3");
        if (!el || typeof echarts === "undefined") return;
        var chart = echarts.init(el);
        var kdj = (series && series.kdj) || {};
        var k = _spKlineSeries(records);
        chart.setOption({
            tooltip: { trigger: "axis" },
            legend: { data: ["K", "D", "J"] },
            xAxis: { type: "category", data: k.dates },
            yAxis: { type: "value" },
            grid: { left: 55, right: 16, top: 32, bottom: 24 },
            series: [
                { name: "K", type: "line", data: kdj.k || [], showSymbol: false, lineStyle: { width: 1 } },
                { name: "D", type: "line", data: kdj.d || [], showSymbol: false, lineStyle: { width: 1 } },
                { name: "J", type: "line", data: kdj.j || [], showSymbol: false, lineStyle: { width: 1 } },
            ],
        });
    }

    async function openSmartPickerDetail(code, name) {
        var el = document.getElementById("sp-detail");
        if (!el) return;
        code = String(code || "");
        el.innerHTML = '<div class="loading">加载 ' + escapeHtml(code) + ' 详情...</div>';
        try {
            var itemResp = await apiFetch("/smart-picker/" + encodeURIComponent(code), { timeout: 15000, retries: 0 });
            var itemData = await itemResp.json();
            var item = itemData.item || _spItemMap[code] || null;
            var chartResp = await apiFetch("/smart-picker/" + encodeURIComponent(code) + "/chart?days=80", { timeout: 20000, retries: 0 });
            var chartData = await chartResp.json();
            var records = (chartData && chartData.records) || [];
            var series = (chartData && chartData.series) || {};
            el.innerHTML =
                '<button class="btn" style="margin-bottom:10px" onclick="closeSmartPickerDetail()">← 返回</button>' +
                '<h3 class="section-title">' + escapeHtml(code) + " " + escapeHtml(name || "") + " · 智能选股详情</h3>" +
                _spMetaHtml(item) +
                (chartData.cached ? '<div class="hint">图表已预计算（cached）</div>' : "") +
                '<div id="sp-chart-1" style="width:100%;height:280px"></div>' +
                '<div id="sp-chart-2" style="width:100%;height:220px"></div>' +
                '<div id="sp-chart-3" style="width:100%;height:220px"></div>' +
                '<div class="hint">提示：信号仅为辅助参考，不构成投资建议。</div>';
            if (records && records.length) {
                _drawTechKline(records, series);
                _drawTechMacd(records, series);
                _drawTechKdj(records, series);
            } else {
                document.getElementById("sp-chart-1").innerHTML =
                    '<div class="empty-state">无 K 线数据（' + escapeHtml((chartData && chartData.reason) || "kline_unavailable") + "）</div>";
            }
        } catch (e) {
            el.innerHTML = '<div class="empty-state">加载失败：' + escapeHtml(String(e)) + "</div>";
        }
    }


    async function setupResearchPage(pageId) {

        const cfg = PAGES[pageId];
        const el = document.getElementById('page-' + pageId);
        if (!el || !cfg) return;
        // smart-picker 自己加载 tech-indicators + trend-strength 两个源，不走通用单 path
        if (cfg.kind === 'smart-picker') { await setupSmartPicker(el); return; }
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
