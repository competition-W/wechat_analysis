import unittest
from datetime import date
from unittest.mock import patch

from services import db_dashboard


class DashboardParsingTests(unittest.TestCase):
    def test_extracts_multiple_project_codes_and_removes_duplicates(self):
        value = "客户群 LC-P2026001 / LC-X99 / LC-SP2026002 / LC-P2026001"
        self.assertEqual(
            db_dashboard.extract_project_codes(value),
            ["LC-P2026001", "LC-SP2026002"],
        )

    def test_extracts_supported_project_code_followed_by_chinese_text(self):
        self.assertEqual(
            db_dashboard.extract_project_codes("LC-SP202604130083项目综合群"),
            ["LC-SP202604130083"],
        )

    def test_ignores_non_focus_group_codes(self):
        self.assertEqual(db_dashboard.extract_project_codes("LC-C202604130083项目综合群"), [])
        self.assertFalse(db_dashboard.is_focus_group_name("普通交流群"))
        self.assertTrue(db_dashboard.is_focus_group_name("客户 LC-P202604130083 项目群"))

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
            "projects": [{"category_l1": "环境", "category_l2": "常规转录组", "key_account": "客户甲"}],
        }
        self.assertTrue(
            db_dashboard._dimension_matches(
                dimension, "华东", "张三", "常规转录组", "客户甲"
            )
        )
        self.assertFalse(db_dashboard._dimension_matches(dimension, region="华南"))
        self.assertFalse(db_dashboard._dimension_matches(dimension, category="食品"))

    def test_lims_record_keeps_work_unit_and_l2_category(self):
        item = db_dashboard.normalize_lims_api_record(
            {
                "projectCode": "LC-P2026001",
                "workUnit": "客户单位A",
                "productBigSortTwo": "常规转录组",
                "productBigSortThree": "mRNA",
            },
            "LC-P2026001",
        )

        self.assertEqual(item["work_unit"], "客户单位A")
        self.assertEqual(item["category_l2"], "常规转录组")

    def test_time_period_breakdown_uses_real_msgtime_and_separates_weekend(self):
        groups = {
            "客户 LC-P2026001 项目群": {
                "dimension": {"aftersalers": ["张三"]},
            }
        }
        chat_rows_by_group = {
            "客户 LC-P2026001 项目群": [
                {"raw_json": '{"msgtime":"2026-07-06 09:00:00"}'},
                {"raw_json": '{"msgtime":"2026-07-06 13:00:00"}'},
                {"raw_json": '{"msgtime":"2026-07-06 18:00:00"}'},
                {"raw_json": '{"msgtime":"2026-07-11 10:00:00"}'},
            ]
        }

        result = db_dashboard._time_period_breakdown(groups, {}, chat_rows_by_group)
        item = result["items"][0]

        self.assertEqual(item["morning"]["count"], 1)
        self.assertEqual(item["afternoon"]["count"], 1)
        self.assertEqual(item["after_hours"]["count"], 1)
        self.assertEqual(item["weekend"]["count"], 1)
        self.assertIn("不包含周末", result["after_hours"])

    def test_chat_msgtime_prefers_raw_json_msgtime(self):
        row = {
            "msgtime": "2026-07-06 09:00:00",
            "raw_json": '{"msgtime":"2026-07-06 10:30:00"}',
        }
        self.assertEqual(
            db_dashboard.chat_msgtime(row).strftime("%Y-%m-%d %H:%M:%S"),
            "2026-07-06 10:30:00",
        )

    def test_evidence_message_uses_raw_chat_msgtime(self):
        missed = [{"msgid": "m1", "content": "请问进度", "msgtime": "2026-07-06 09:00:00"}]
        raw_messages = [{
            "msgid": "m1",
            "content": "请问进度",
            "sender_name": "客户",
            "msgtime": "2026-07-06 18:30:00",
        }]

        result = db_dashboard._evidence_messages("unanswered", "", "", missed, raw_messages)

        self.assertEqual(result[0]["msgtime"], "2026-07-06 18:30:00")
        self.assertEqual(result[0]["sender_name"], "客户")

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

    def test_latest_rows_filters_non_focus_groups_from_rows_and_raw_count(self):
        latest_rows = [
            {"groupName": "客户 LC-P2026001 项目群"},
            {"groupName": "普通交流群"},
        ]
        raw_rows = [
            {"groupName": "客户 LC-P2026001 项目群"},
            {"groupName": "普通交流群"},
            {"groupName": "客户 LC-SP2026002 特殊项目群"},
        ]
        with patch.object(db_dashboard, "_query", side_effect=[latest_rows, raw_rows]):
            rows, raw_count = db_dashboard._latest_rows(None, date(2026, 7, 1), date(2026, 7, 8))

        self.assertEqual([row["groupName"] for row in rows], ["客户 LC-P2026001 项目群"])
        self.assertEqual(raw_count, 2)

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
