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

    def test_docs_publish_directory_matches_frontend_for_national_team(self):
        for rel in ["index.html", "js/app.js", "css/styles.css"]:
            frontend_text = (ROOT / "frontend" / rel).read_text(encoding="utf-8")
            docs_text = (ROOT / "docs" / rel).read_text(encoding="utf-8")
            self.assertEqual(frontend_text, docs_text)


if __name__ == "__main__":
    unittest.main()
