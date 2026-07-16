import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.main import app
from services import aftersaler_mapping
from services import db_dashboard
from services.report_aggregator import aggregate_report


def snapshot(*rules):
    return {
        "available": True,
        "version_id": 7,
        "effective_month": "2026-07",
        "revision": 3,
        "rules": list(rules),
        "reason": "ok",
    }


def rule(rule_id, keyword, region, raw, actual, product_name="测试产品"):
    return {
        "id": rule_id,
        "version_id": 7,
        "product_name": product_name,
        "product_keywords": [keyword],
        "region_name": region,
        "lims_aftersaler": raw,
        "actual_aftersaler": actual,
    }


class AftersalerMatchingTests(unittest.TestCase):
    def test_product_normalization_ignores_case_spaces_and_separators(self):
        self.assertEqual(
            aftersaler_mapping.normalize_product_token(" TCR / BCR（panel） "),
            "tcrbcrpanel",
        )
        resolved = aftersaler_mapping.resolve_final_aftersaler(
            "TCR/BCR panel", "华南区", "来智健",
            snapshot(rule(1, "TCR_BCR", "华南区", "来智健", "刘安民")),
        )
        self.assertEqual(resolved["final_aftersaler"], "刘安民")
        self.assertEqual(resolved["aftersaler_source"], "mapping")

    def test_exact_region_wins_over_national_rule(self):
        rules = snapshot(
            rule(1, "RIP", "全国", "来智健", "全国负责人"),
            rule(2, "RIP", "北京区", "来智健", "北京负责人"),
        )
        result = aftersaler_mapping.resolve_final_aftersaler(
            "RIP-seq", "北京区", "来智健", rules,
        )
        self.assertEqual(result["final_aftersaler"], "北京负责人")
        self.assertEqual(result["mapping_rule_id"], 2)

    def test_longest_product_keyword_wins(self):
        rules = snapshot(
            rule(1, "CUT", "华东一区", "杨嘉俊", "短词负责人"),
            rule(2, "CUT&Tag", "华东一区", "杨嘉俊", "完整词负责人"),
        )
        result = aftersaler_mapping.resolve_final_aftersaler(
            "Ultra CUT & Tag", "华东一区", "杨嘉俊", rules,
        )
        self.assertEqual(result["final_aftersaler"], "完整词负责人")

    def test_no_match_and_conflict_fall_back_to_lims(self):
        missing = aftersaler_mapping.resolve_final_aftersaler(
            "WGBS", "北京区", "来智健", snapshot(),
        )
        self.assertEqual(missing["final_aftersaler"], "来智健")
        self.assertEqual(missing["aftersaler_source"], "lims_fallback")

        conflicted = aftersaler_mapping.resolve_final_aftersaler(
            "ATAC-ChIP", "北京区", "杨嘉俊",
            snapshot(
                rule(1, "ATAC", "北京区", "杨嘉俊", "甲"),
                rule(2, "ChIP", "北京区", "杨嘉俊", "乙"),
            ),
        )
        self.assertTrue(conflicted["mapping_conflict"])
        self.assertEqual(conflicted["final_aftersaler"], "杨嘉俊")

    def test_dashboard_record_ignores_upstream_final_aftersaler(self):
        mapping = snapshot(rule(9, "ATAC", "北京区", "杨嘉俊", "吴志浩"))
        normalized = db_dashboard.normalize_lims_api_record(
            {
                "projectCode": "LC-P1",
                "productBigSortThree": "ATAC-seq",
                "orgName": "北京区",
                "afterSaler": "杨嘉俊",
                "finalAfterSaler": "上游旧值",
            },
            "LC-P1",
            mapping,
        )
        self.assertEqual(normalized["raw_aftersaler"], "杨嘉俊")
        self.assertEqual(normalized["final_aftersaler"], "吴志浩")
        self.assertEqual(normalized["aftersaler_source"], "mapping")

    def test_preview_reports_matched_fallback_and_conflict_counts(self):
        mapping = snapshot(rule(1, "ATAC", "北京区", "杨嘉俊", "吴志浩"))
        result = aftersaler_mapping.preview_records([
            {"productBigSortThree": "ATAC", "orgName": "北京区", "afterSaler": "杨嘉俊"},
            {"productBigSortThree": "WGBS", "orgName": "北京区", "afterSaler": "来智健"},
        ], mapping)
        self.assertEqual(result["matched_records"], 1)
        self.assertEqual(result["fallback_records"], 1)
        self.assertEqual(result["match_rate"], 50.0)

    def test_report_aggregator_uses_final_owner(self):
        mapping = snapshot(rule(1, "ATAC", "北京区", "杨嘉俊", "吴志浩"))
        report = aggregate_report([], [{
            "projectCode": "LC-P1",
            "productBigSortThree": "ATAC",
            "orgName": "北京区",
            "afterSaler": "杨嘉俊",
        }], mapping_snapshot=mapping)
        self.assertEqual(report["after_sales_distribution"][0]["name"], "吴志浩")


class AftersalerAdminKeyTests(unittest.TestCase):
    def test_admin_key_is_fail_closed_and_compared(self):
        with patch("config.settings.settings.DASHBOARD_ADMIN_KEY", ""):
            with self.assertRaises(RuntimeError):
                aftersaler_mapping.verify_admin_key("anything")
        with patch("config.settings.settings.DASHBOARD_ADMIN_KEY", "secret"):
            with self.assertRaises(PermissionError):
                aftersaler_mapping.verify_admin_key("wrong")
            aftersaler_mapping.verify_admin_key("secret")

    def test_admin_api_is_disabled_without_configured_key(self):
        with patch("config.settings.settings.DASHBOARD_ADMIN_KEY", ""):
            response = TestClient(app).get(
                "/api/v1/dashboard/aftersaler-mapping/versions",
                headers={"X-Dashboard-Admin-Key": "anything"},
            )
        self.assertEqual(response.status_code, 503)

    def test_admin_api_returns_versions_after_authentication(self):
        with (
            patch("config.settings.settings.DASHBOARD_ADMIN_KEY", "secret"),
            patch.object(aftersaler_mapping, "list_versions", return_value=[{
                "id": 1, "effective_month": "1900-01", "revision": 1,
                "rule_count": 84,
            }]),
        ):
            response = TestClient(app).get(
                "/api/v1/dashboard/aftersaler-mapping/versions",
                headers={"X-Dashboard-Admin-Key": "secret"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"][0]["rule_count"], 84)


if __name__ == "__main__":
    unittest.main()
