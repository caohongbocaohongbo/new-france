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


if __name__ == "__main__":
    unittest.main()
