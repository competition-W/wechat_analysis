import unittest
from datetime import date
from unittest.mock import patch

from services import db_dashboard


class DashboardParsingTests(unittest.TestCase):
    def test_extracts_multiple_project_codes_and_removes_duplicates(self):
        value = "客户群 LC-P2026001 / LC-X99 / LC-P2026001"
        self.assertEqual(
            db_dashboard.extract_project_codes(value),
            ["LC-P2026001", "LC-X99"],
        )

    def test_extracts_project_code_followed_by_chinese_text(self):
        self.assertEqual(
            db_dashboard.extract_project_codes("LC-C202604130083项目综合群"),
            ["LC-C202604130083"],
        )

    def test_parses_count_fields(self):
        self.assertEqual(
            db_dashboard.parse_count_map("好评: 3, 差评：2"),
            {"好评": 3, "差评": 2},
        )

    def test_normalizes_key_account_flags(self):
        self.assertEqual(db_dashboard.normalize_key_account("0", "客户甲"), "")
        self.assertEqual(db_dashboard.normalize_key_account("1", "客户甲"), "客户甲")
        self.assertEqual(db_dashboard.normalize_key_account("KA-001", "客户甲"), "KA-001")
        self.assertEqual(db_dashboard.normalize_key_account("0", "客户甲", "KA-002"), "KA-002")

    def test_period_boundaries(self):
        today = date(2026, 7, 6)
        self.assertEqual(
            db_dashboard.resolve_period("week", today=today),
            (date(2026, 7, 6), date(2026, 7, 6), "week"),
        )
        self.assertEqual(
            db_dashboard.resolve_period("month", today=today),
            (date(2026, 7, 1), date(2026, 7, 6), "month"),
        )
        self.assertEqual(
            db_dashboard.resolve_period("quarter", today=today),
            (date(2026, 7, 1), date(2026, 7, 6), "quarter"),
        )

    def test_custom_period_rejects_reversed_dates(self):
        with self.assertRaises(ValueError):
            db_dashboard.resolve_period("custom", "2026-07-06", "2026-07-01")

    def test_dimension_filters_are_combined(self):
        dimension = {
            "regions": ["华东"],
            "aftersalers": ["张三"],
            "projects": [{"category_l1": "环境", "key_account": "客户甲"}],
        }
        self.assertTrue(
            db_dashboard._dimension_matches(
                dimension, "华东", "张三", "环境", "客户甲"
            )
        )
        self.assertFalse(db_dashboard._dimension_matches(dimension, region="华南"))
        self.assertFalse(db_dashboard._dimension_matches(dimension, category="食品"))


    def test_lims_api_dimensions_use_raw_after_saler(self):
        group_name = "Customer LC-P2026001 support"
        records = {
            "LC-P2026001": [{
                "projectCode": "LC-P2026001",
                "afterSaler": "RawAfter",
                "finalAfterSaler": "LegacyFinal",
                "members": "SomeoneElse",
                "orgName": "RegionA",
                "productName": "ProductA",
                "productBigSortOne": "CategoryA",
            }]
        }
        dimensions, quality = db_dashboard._dimensions_from_lims_api(
            {group_name: ["LC-P2026001"]},
            records,
            {"requests": 1, "records": 1, "errors": 0},
        )

        self.assertEqual(dimensions[group_name]["aftersalers"], ["RawAfter"])
        self.assertEqual(dimensions[group_name]["tentative_aftersalers"], [])
        self.assertEqual(quality["groups_with_aftersaler"], 1)

    def test_lims_unavailable_does_not_use_business_table_fallback(self):
        rows = [{"groupName": "Customer LC-P2026001 support", "member": ""}]
        with patch.object(
            db_dashboard,
            "fetch_lims_base_data",
            return_value=({}, {"available": False, "requests": 1, "records": 0, "errors": 1}),
        ):
            dimensions, quality = db_dashboard._load_dimensions(None, rows)

        self.assertEqual(dimensions["Customer LC-P2026001 support"]["projects"], [])
        self.assertEqual(dimensions["Customer LC-P2026001 support"]["dimension_source"], "lims_unavailable")
        self.assertEqual(quality["matched_project_codes"], 0)
        self.assertEqual(quality["lims_source"], "base_data_api_unavailable")

    def test_project_code_diagnostics_lists_missing_groups_and_unmatched_codes(self):
        diagnostics = db_dashboard._project_code_diagnostics(
            ["No project group", "Alpha LC-P2026001", "Beta LC-P404"],
            {
                "No project group": {"codes": []},
                "Alpha LC-P2026001": {"codes": ["LC-P2026001"]},
                "Beta LC-P404": {"codes": ["LC-P404"]},
            },
            {"LC-P2026001": [{"projectCode": "LC-P2026001"}]},
        )

        self.assertEqual(diagnostics["groups_without_project_code"], ["No project group"])
        self.assertEqual(diagnostics["unmatched_codes"], ["LC-P404"])
        self.assertEqual(diagnostics["code_to_groups"]["LC-P404"], ["Beta LC-P404"])
        self.assertEqual(diagnostics["groups_without_lims_link"], ["Beta LC-P404"])


class DashboardCacheTests(unittest.TestCase):
    def setUp(self):
        db_dashboard.clear_cache()

    def test_cache_key_isolated_by_business_filter(self):
        base = {
            "meta": {}, "summary": {}, "service_quality": {}, "communication": {},
            "business": {}, "cross_analysis": {}, "data_quality": {},
        }
        with patch.object(db_dashboard, "_build_overview", return_value=base) as build:
            db_dashboard.get_overview("month", region="华东")
            db_dashboard.get_overview("month", region="华南")
            db_dashboard.get_overview("month", region="华东")
        self.assertEqual(build.call_count, 2)


if __name__ == "__main__":
    unittest.main()
