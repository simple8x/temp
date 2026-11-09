from pathlib import Path
import unittest


class ReviewAppContractTests(unittest.TestCase):
    def test_offline_app_uses_clean_source_as_default(self):
        html = (Path(__file__).parents[1] / "review_app.html").read_text(encoding="utf-8")
        self.assertIn('id="showCandidates" type="checkbox"', html)
        self.assertIn('const img=$("showCandidates").checked&&overlayImage?overlayImage:baseImage;', html)
        self.assertIn('template.source_image', html)

    def test_app_exports_pdf_point_coordinate_schema(self):
        html = (Path(__file__).parents[1] / "review_app.html").read_text(encoding="utf-8")
        self.assertIn('coordinate_system:"pdf_points_origin_top_left"', html)
        self.assertIn('function pdfPoint(x,y)', html)
        self.assertIn('VERIFIED_CONNECTED', html)
        self.assertIn('VERIFIED_SEPARATE', html)

    def test_app_has_no_remote_script_or_stylesheet_dependencies(self):
        html = (Path(__file__).parents[1] / "review_app.html").read_text(encoding="utf-8")
        self.assertNotIn("<script src=", html)
        self.assertNotIn('<link rel="stylesheet" href="http', html)


if __name__ == "__main__":
    unittest.main()
