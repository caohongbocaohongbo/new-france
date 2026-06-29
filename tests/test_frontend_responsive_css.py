from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FrontendResponsiveCssTest(unittest.TestCase):
    def test_screening_filters_are_not_clipped_on_small_screens(self):
        css = (ROOT / "frontend/css/styles.css").read_text(encoding="utf-8")
        mobile_blocks = re.findall(r"@media\s*\(max-width:\s*1200px\)\s*\{([\s\S]*?)\n\}", css)
        mobile_blocks += re.findall(r"@media\s*\(max-width:\s*768px\)\s*\{([\s\S]*?)\n\}", css)
        responsive_css = "\n".join(mobile_blocks)

        self.assertIn("#page-screening.active", responsive_css)
        self.assertIn("overflow: visible", responsive_css)
        self.assertIn(".screening-filters", responsive_css)
        self.assertIn("height: auto", responsive_css)

    def test_national_team_page_shell_and_api_are_present(self):
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        css = (ROOT / "frontend/css/styles.css").read_text(encoding="utf-8")

        self.assertIn('data-page="national-team"', html)
        self.assertIn('id="page-national-team"', html)
        self.assertIn("国家队动向", html)
        self.assertIn("loadNationalTeam", js)
        self.assertIn("/national-team/summary", js)
        self.assertIn("/national-team/holdings", js)
        self.assertIn("/national-team/capital-flows", js)
        self.assertIn("/national-team/refresh", js)
        self.assertIn(".national-team-grid", css)
        self.assertIn(".national-team-panel", css)

    def test_national_team_events_do_not_generate_fake_summary(self):
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")

        self.assertNotIn("基于公开披露记录生成", js)
        self.assertIn("event.summary", js)

    def test_national_team_external_links_use_noopener(self):
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")

        self.assertIn('target="_blank" rel="noopener" class="link"', js)
        self.assertIn('target="_blank" rel="noopener" class="event-source-link"', js)

    def test_national_team_layout_controls_are_stable(self):
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        css = (ROOT / "frontend/css/styles.css").read_text(encoding="utf-8")

        self.assertIn("national-team-toolbar-actions", html)
        self.assertIn("ntEventsScroll", html)
        self.assertIn("ntEventLoadMore", html)
        self.assertIn("loadMoreNationalTeamEvents", js)
        self.assertIn("offset=${ntEventsOffset}", js)
        self.assertIn("官网查看", js)
        self.assertIn(".national-team-toolbar-actions", css)
        self.assertIn(".national-team-panel .pagination", css)
        self.assertIn("margin-top: auto", css)
        self.assertIn("height: calc(100vh - 318px)", css)
        self.assertIn(".national-team-table th", css)
        self.assertIn("background: #fff", css)
        self.assertIn(".events-scroll", css)

    def test_national_team_capital_flow_section_is_present(self):
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        css = (ROOT / "frontend/css/styles.css").read_text(encoding="utf-8")

        self.assertIn("ntCapitalFlows", html)
        self.assertIn("国家队三大主力买入/卖出资金Top10", html)
        self.assertIn("renderNationalTeamCapitalFlows", js)
        self.assertIn("capital-flow-card", js)
        self.assertIn("买入前10", js)
        self.assertIn("卖出前10", js)
        self.assertIn("/national-team/holding-values", js)
        self.assertIn("renderNationalTeamHoldingValues", js)
        self.assertIn("持股金额前10", js)
        self.assertIn("org-info-trigger", js)
        self.assertIn("Central Huijin Investment Ltd.", js)
        self.assertNotIn("国家队三大主力前十大股东", html)
        self.assertNotIn("renderNationalTeamTopHolderRings", js)
        self.assertIn(".national-team-flows", css)
        self.assertIn(".capital-flow-bar", css)
        self.assertIn(".org-info-trigger", css)
        self.assertIn(".holding-value-row", css)

    def test_national_team_source_meta_has_no_extra_top_line(self):
        css = (ROOT / "frontend/css/styles.css").read_text(encoding="utf-8")

        self.assertIn(".national-team-panel .table-note", css)
        self.assertIn("border-top: 0", css)
        self.assertIn("padding-top: 0", css)

    def test_screening_result_tools_table_note_has_no_extra_line(self):
        css = (ROOT / "frontend/css/styles.css").read_text(encoding="utf-8")
        docs_css = (ROOT / "docs/css/styles.css").read_text(encoding="utf-8")
        match = re.search(r"\.result-tools \.table-note\s*\{([\s\S]*?)\n\}", css)

        self.assertIsNotNone(match)
        block = match.group(1)
        self.assertIn("margin: 0", block)
        self.assertIn("padding-top: 0", block)
        self.assertIn("border-top: 0", block)
        self.assertIn(match.group(0), docs_css)

    def test_dashboard_limit_panels_have_matched_height_and_clean_trend_colors(self):
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        css = (ROOT / "frontend/css/styles.css").read_text(encoding="utf-8")
        trend_match = re.search(r"function renderMarketTrend\(data\) \{([\s\S]*?)\n\}\n\nfunction renderRecentRecommendations", js)
        self.assertIsNotNone(trend_match)
        trend_js = trend_match.group(1)

        self.assertIn("align-items: stretch", css)
        self.assertIn(".limit-card,\n.trend-card", css)
        self.assertIn("height: 100%", css)
        self.assertIn("#D60A22", trend_js)
        self.assertIn("#027B66", trend_js)
        self.assertIn("#8a8f99", trend_js)
        self.assertIn("width: 1.2", trend_js)
        self.assertIn("opacity: 0.45", trend_js)
        self.assertIn("dt_list || data.limit_down_list", trend_js)
        self.assertNotIn("#f5a400", trend_js)
        self.assertNotIn("#2932e1", trend_js)
        self.assertNotIn("areaStyle", trend_js)
        self.assertIn("formatter: params =>", trend_js)
        self.assertIn("时间", trend_js)
        self.assertIn("涨停", trend_js)
        self.assertIn("跌停", trend_js)
        self.assertIn("boundaryGap: true", trend_js)
        self.assertIn("barGap: '-100%'", trend_js)
        self.assertIn("barCategoryGap: '42%'", trend_js)
        self.assertIn("axisLine: { lineStyle: { color: '#d7dce5' } }", trend_js)

    def test_trading_status_uses_backend_session_text_and_precise_local_fallback(self):
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")

        self.assertIn("function getLocalMarketStatus", js)
        self.assertIn("market_status_text", js)
        self.assertIn("market_session", js)
        self.assertIn("morningOpen = 9 * 60 + 30", js)
        self.assertIn("morningClose = 11 * 60 + 30", js)
        self.assertIn("afternoonOpen = 13 * 60", js)
        self.assertIn("afternoonClose = 15 * 60", js)
        self.assertIn("data.is_trading_hours ? '' : ' off'", js)
        self.assertIn("local.is_trading_hours ? '' : ' off'", js)
        self.assertNotIn("data.is_trading_hours || data.is_trading_day", js)
        self.assertNotIn("now.getHours() >= 9 && now.getHours() < 15", js)

    def test_dashboard_optional_sources_are_gated_by_backend_status(self):
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        css = (ROOT / "frontend/css/styles.css").read_text(encoding="utf-8")

        self.assertIn('id="dashboardOptionalSources"', html)
        self.assertIn("renderDashboardOptionalSources", js)
        self.assertIn("dashboard_enabled", js)
        self.assertIn("ready_for_promotion", js)
        self.assertIn(".optional-source-panel", css)
        self.assertIn(".optional-source-grid", css)

    def test_overnight_arbitrage_is_promoted_to_top_level_page(self):
        html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        js = (ROOT / "frontend/js/app.js").read_text(encoding="utf-8")
        css = (ROOT / "frontend/css/styles.css").read_text(encoding="utf-8")
        plugin_js = (ROOT / "frontend/js/plugins/overnight_arbitrage.js").read_text(encoding="utf-8")

        self.assertIn('data-page="overnight-arbitrage"', html)
        self.assertIn('id="page-overnight-arbitrage"', html)
        self.assertIn('id="overnightDecisionBody"', html)
        self.assertNotIn('data-screening-mode="overnight"', html)
        self.assertNotIn('id="overnightPanel"', html)
        self.assertIn("'overnight-arbitrage':'尾盘隔夜套利'", js)
        self.assertIn("setupOvernightArbitragePage", js)
        self.assertIn("runOvernightArbitrage", plugin_js)
        self.assertIn("/overnight-arbitrage/run", plugin_js)
        self.assertIn("/overnight-arbitrage/latest", plugin_js)
        self.assertIn("renderOvernightDecision", plugin_js)
        self.assertIn("14:43-14:55", plugin_js)
        self.assertIn(".screening-mode-tabs", css)
        self.assertIn(".overnight-decision-grid", css)

    def test_overnight_arbitrage_plugin_js_is_self_contained(self):
        """插件 JS 必须自带 fallback 工具，便于整目录迁移到新项目。

        若以后有人把 fallback 删掉、直接硬依赖主项目全局 apiFetch / escapeHtml 等，
        此测试会失败，提醒迁移性已经被打破。
        """
        frontend_plugin = (ROOT / "frontend/js/plugins/overnight_arbitrage.js").read_text(encoding="utf-8")
        docs_plugin = (ROOT / "docs/js/plugins/overnight_arbitrage.js").read_text(encoding="utf-8")
        for plugin_js in (frontend_plugin, docs_plugin):
            # 命名空间
            self.assertIn("window.OvernightArbitrage", plugin_js)
            # 必须有 fallback 检测（typeof xxx === 'function'）
            self.assertIn("typeof apiFetch === 'function'", plugin_js)
            self.assertIn("typeof escapeHtml === 'function'", plugin_js)
            self.assertIn("typeof formatNumber === 'function'", plugin_js)
            self.assertIn("typeof formatMaybePct === 'function'", plugin_js)
            self.assertIn("typeof setInlineStatus === 'function'", plugin_js)
            # 必须有内部命名变量 _oaXxx
            self.assertIn("_oaApiFetch", plugin_js)
            self.assertIn("_oaEscapeHtml", plugin_js)
            self.assertIn("_oaSetInlineStatus", plugin_js)
        # frontend 与 docs 镜像保持一致
        self.assertEqual(frontend_plugin, docs_plugin)

    def test_docs_publish_directory_matches_frontend_for_national_team(self):
        for rel in ["index.html", "js/app.js", "css/styles.css"]:
            frontend_text = (ROOT / "frontend" / rel).read_text(encoding="utf-8")
            docs_text = (ROOT / "docs" / rel).read_text(encoding="utf-8")
            self.assertEqual(frontend_text, docs_text)


if __name__ == "__main__":
    unittest.main()
