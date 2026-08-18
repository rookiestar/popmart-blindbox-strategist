import importlib.util
import pathlib
import sys
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).parents[1]
    / "scripts"
    / "qiandao_market_snapshot.py"
)
spec = importlib.util.spec_from_file_location("qiandao_market_snapshot", MODULE_PATH)
snapshot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = snapshot
assert spec.loader is not None
spec.loader.exec_module(snapshot)


class QianDaoSnapshotTests(unittest.TestCase):
    def test_valid_json_is_not_html_unescaped(self):
        page = (
            '<script type="application/json" id="__NUXT_DATA__">'
            '["A &quot;B&quot;"]'
            "</script>"
        )
        self.assertEqual(
            snapshot.extract_flat_payload(page),
            ["A &quot;B&quot;"],
        )

    def test_decode_nuxt_reactive_payload(self):
        flat = [
            ["ShallowReactive", 1],
            {"name": 2, "items": 3},
            "root",
            [4],
            {"value": 5},
            42,
        ]
        self.assertEqual(
            snapshot.decode_nuxt_payload(flat),
            {"name": "root", "items": [{"value": 42}]},
        )

    def test_decode_nuxt_special_values_and_integer_primitive(self):
        flat = [
            {"missing": -1, "number": 1, "not_a_number": -3},
            42,
        ]
        decoded = snapshot.decode_nuxt_payload(flat)
        self.assertIsNone(decoded["missing"])
        self.assertEqual(decoded["number"], 42)
        self.assertTrue(snapshot.math.isnan(decoded["not_a_number"]))

    def test_extract_design_fields_and_filter_category(self):
        decoded = {
            "state": {
                "$svue-query": {
                    "queries": [
                        {
                            "state": {
                                "data": {
                                    "pages": [
                                        {
                                            "items": [
                                                {
                                                    "spuShow": {
                                                        "id": "1",
                                                        "name": "Hero",
                                                        "category_name": "Demo Series",
                                                        "is_category": False,
                                                        "strikePrice": 55.5,
                                                        "stock_order_min_sell_price": 60,
                                                        "stock_order_max_buy_price": 50,
                                                        "wish_count": 100,
                                                        "wish_count_3d": 3,
                                                    }
                                                },
                                                {
                                                    "spuShow": {
                                                        "id": "2",
                                                        "name": "Other",
                                                        "category_name": "Other Series",
                                                        "is_category": False,
                                                        "strikePrice": 10,
                                                    }
                                                },
                                            ]
                                        }
                                    ]
                                }
                            }
                        }
                    ]
                }
            }
        }
        records = snapshot.extract_spu_records(
            decoded, category="Demo", retail_price=69
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "Hero")
        self.assertEqual(records[0]["average_sold_price"], 55.5)
        self.assertEqual(records[0]["highest_buy_order"], 50)
        self.assertEqual(records[0]["lowest_sell_order"], 60)
        self.assertAlmostEqual(records[0]["premium_to_retail_pct"], -19.6)

    def test_snapshot_reports_coverage_count_and_local_timestamp(self):
        page = (
            '<script id="__NUXT_DATA__">'
            '[[1],{"spuShow":2},{"id":3,"name":4,"category_name":5,'
            '"strikePrice":6},"1","Hero","Demo",55]'
            "</script>"
        )
        result = snapshot.build_snapshot("Demo", page, category="Demo")
        self.assertEqual(result["record_count"], 1)
        self.assertRegex(result["observed_at"], r"[+-]\d{2}:\d{2}$")

    def test_category_rows_are_excluded(self):
        decoded = {
            "spuShow": {
                "id": "series",
                "name": "Demo Series",
                "is_category": True,
                "strikePrice": 500,
            }
        }
        self.assertEqual(snapshot.extract_spu_records(decoded), [])

    def test_secret_designs_are_excluded_unless_explicitly_included(self):
        decoded = {
            "items": [
                {
                    "spuShow": {
                        "id": "1",
                        "name": "Regular",
                        "category_name": "Demo",
                        "strikePrice": 50,
                    }
                },
                {
                    "spuShow": {
                        "id": "2",
                        "name": "隐藏款 Secret",
                        "category_name": "Demo",
                        "strikePrice": 100,
                    }
                },
            ]
        }

        regular_only = snapshot.extract_spu_records(decoded)
        with_secret = snapshot.extract_spu_records(decoded, include_secret=True)

        self.assertEqual([record["name"] for record in regular_only], ["Regular"])
        self.assertEqual(
            [record["name"] for record in with_secret],
            ["Regular", "隐藏款 Secret"],
        )

    def test_coverage_mismatch_error_lists_the_actual_record_names(self):
        import contextlib
        import io
        import tempfile

        page = (
            '<script id="__NUXT_DATA__">'
            '[[1,2],{"spuShow":3},{"spuShow":4},'
            '{"id":5,"name":6,"category_name":7,"strikePrice":8},'
            '{"id":9,"name":10,"category_name":11,"strikePrice":12},'
            '"1","Hero","Demo",55,"2","Sidekick","Demo",40]'
            "</script>"
        )
        with tempfile.NamedTemporaryFile(
            mode="w+",
            suffix=".html",
            encoding="utf-8",
            delete=False,
        ) as page_file:
            page_file.write(page)
            path = page_file.name

        try:
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = snapshot.main(
                    ["Demo", "--html-file", path, "--expected-count", "3"]
                )
        finally:
            pathlib.Path(path).unlink()

        self.assertEqual(exit_code, 2)
        message = stderr.getvalue()
        self.assertIn("expected 3, got 2", message)
        self.assertIn("Hero", message)
        self.assertIn("Sidekick", message)
        self.assertIn("--include-secret", message)


if __name__ == "__main__":
    unittest.main()
