import unittest
from contextlib import contextmanager
from datetime import date
from io import BytesIO
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
            "projects": [{"region": "华东", "raw_aftersaler": "张三", "category_l1": "环境", "category_l2": "常规转录组", "key_account": "客户甲"}],
        }
        self.assertTrue(
            db_dashboard._dimension_matches(
                dimension, "华东", "张三", "常规转录组", "客户甲"
            )
        )
        self.assertFalse(db_dashboard._dimension_matches(dimension, region="华南"))
        self.assertFalse(db_dashboard._dimension_matches(dimension, category="食品"))

    def test_all_products_excludes_projects_outside_the_three_allowed_categories(self):
        dimension = {
            "regions": ["华东"],
            "aftersalers": ["张三"],
            "projects": [
                {"project_code": "LC-P1", "region": "华东", "category_l2": "常规转录组"},
                {"project_code": "LC-P2", "region": "华东", "category_l2": "蛋白质组"},
            ],
        }

        scoped = db_dashboard._scope_dimension(dimension)

        self.assertTrue(db_dashboard._dimension_matches(dimension))
        self.assertEqual([item["project_code"] for item in scoped["projects"]], ["LC-P1"])
        self.assertEqual(scoped["codes"], ["LC-P1"])
        self.assertFalse(db_dashboard._dimension_matches({
            **dimension,
            "projects": [{"category_l2": "蛋白质组"}],
        }))

    def test_project_filters_must_match_the_same_lims_record(self):
        dimension = {
            "aftersalers": ["张三"],
            "projects": [
                {"region": "华东", "raw_aftersaler": "张三", "category_l2": "常规转录组", "key_account": "客户甲"},
                {"region": "华南", "raw_aftersaler": "李四", "category_l2": "微生物", "key_account": "客户乙"},
            ],
        }

        self.assertFalse(db_dashboard._dimension_matches(
            dimension, region="华东", category="微生物", key_account="客户乙"
        ))
        self.assertTrue(db_dashboard._dimension_matches(
            dimension, region="华南", category="微生物", key_account="客户乙"
        ))
        self.assertFalse(db_dashboard._dimension_matches(
            dimension, region="华南", aftersaler="张三", category="微生物", key_account="客户乙"
        ))

    def test_overview_applies_product_scope_to_topics_accounts_and_cross_analysis(self):
        rows = [
            {
                "groupName": "允许产品群", "CREATEDTIME": "2026-07-08 10:00:00",
                "messageToDayCount": 10, "isMissedMessage": "0",
                "customerEmotionAnalysis": "好评:1", "saleEmotionAnalysis": "积极:1",
                "highFrequencyWords": "允许主题:3",
            },
            {
                "groupName": "其它产品群", "CREATEDTIME": "2026-07-08 10:00:00",
                "messageToDayCount": 99, "isMissedMessage": "1",
                "customerEmotionAnalysis": "差评:9", "saleEmotionAnalysis": "负向:9",
                "highFrequencyWords": "其它主题:20",
            },
        ]
        dimensions = {
            "允许产品群": {
                "codes": ["LC-P1", "LC-P3", "LC-P4"], "regions": ["华东"], "aftersalers": ["张三"],
                "projects": [
                    {
                        "project_code": "LC-P1", "region": "华东", "sales_person": "销售甲",
                        "raw_aftersaler": "张三", "analysis_simple_remark": "问题项目",
                        "category_l2": "常规转录组", "category_l3": "mRNA",
                        "key_account": "重点客户甲", "customer_name": "客户甲",
                        "work_unit": "单位甲", "active_day": 12,
                    },
                    {
                        "project_code": "LC-P3", "region": "华东", "sales_person": "销售甲",
                        "raw_aftersaler": "张三", "analysis_simple_remark": "正常交付",
                        "category_l2": "表观组学", "category_l3": "甲基化",
                        "active_day": 8,
                    },
                    {
                        "project_code": "LC-P4", "region": "华东", "sales_person": "销售甲",
                        "raw_aftersaler": "张三", "analysis_simple_remark": "暂不交付",
                        "category_l2": "微生物", "category_l3": "扩增子",
                        "active_day": 5,
                    },
                ],
            },
            "其它产品群": {
                "codes": ["LC-P2"], "regions": ["华南"], "aftersalers": ["李四"],
                "projects": [{
                    "project_code": "LC-P2", "region": "华南", "sales_person": "销售乙",
                    "raw_aftersaler": "李四", "analysis_simple_remark": "问题项目",
                    "category_l2": "蛋白质组", "category_l3": "蛋白",
                    "key_account": "其它重点客户", "customer_name": "客户乙",
                    "work_unit": "单位乙", "active_day": 20,
                }],
            },
        }

        @contextmanager
        def fake_database(_operation):
            yield object()

        with (
            patch.object(db_dashboard, "database", fake_database),
            patch.object(db_dashboard, "_latest_rows", return_value=(rows, 2)),
            patch.object(db_dashboard, "_load_dimensions", return_value=(dimensions, {})),
            patch.object(db_dashboard, "_raw_analysis_count", return_value=1),
            patch.object(db_dashboard, "_query_group_rows", return_value=[]),
            patch.object(db_dashboard, "_query_chat_rows", return_value=[]),
        ):
            result = db_dashboard._build_overview(
                date(2026, 7, 1), date(2026, 7, 8), "custom"
            )
            microbe_result = db_dashboard._build_overview(
                date(2026, 7, 1), date(2026, 7, 8), "custom", category="微生物"
            )

        self.assertEqual(result["summary"]["total_groups"], 1)
        self.assertEqual(result["summary"]["total_messages"], 10)
        self.assertEqual(result["communication"]["high_frequency"], [
            {"word": "允许主题", "count": 3}
        ])
        self.assertEqual(
            [item["key_account"] for item in result["business"]["key_accounts"]],
            ["重点客户甲"],
        )
        self.assertEqual(
            [item["region"] for item in result["cross_analysis"]["region_sales"]],
            ["华东"],
        )
        self.assertEqual(result["service_quality"]["unanswered"]["missed_groups"], 0)
        self.assertEqual(result["project_attention"]["total_projects"], 2)
        self.assertEqual(
            [item["status"] for item in result["project_attention"]["summary"]],
            ["问题项目", "暂不交付"],
        )
        self.assertEqual(
            [item["project_code"] for item in result["project_attention"]["items"]],
            ["LC-P1", "LC-P4"],
        )
        self.assertEqual(
            [item["project_code"] for item in microbe_result["project_attention"]["items"]],
            ["LC-P4"],
        )

    def test_excel_export_splits_dashboard_modules_and_raw_sources_into_sheets(self):
        from openpyxl import load_workbook

        overview = {
            "summary": {
                "total_groups": 1, "total_messages": 10, "project_groups": 1,
                "regions": 1, "aftersaler_count": 1, "product_categories": 1,
                "key_accounts": 1, "short_active_ratio": 100,
            },
            "communication": {
                "trend": [{"date": "2026-07-08", "messages": 10, "groups": 1, "missed": 0}],
                "high_frequency": [{"word": "转录", "count": 3}],
                "active_duration": [{"range": "8-30天", "label": "短期服务", "count": 1, "percentage": 100}],
            },
            "business": {
                "regions": [{"region": "华东", "group_count": 1}],
                "aftersalers": [{"name": "张三", "group_count": 1}],
                "product_categories": [{"category": "常规转录组", "project_count": 1}],
                "key_accounts": [{"key_account": "重点客户甲", "project_count": 1}],
            },
            "project_attention": {
                "target_statuses": ["问题项目", "暂不交付"],
                "total_projects": 1,
                "summary": [{"status": "问题项目", "project_count": 1}],
                "items": [{"status": "问题项目", "project_code": "LC-P1"}],
            },
            "cross_analysis": {
                "region_sales": [], "region_after": [], "region_product": [],
            },
            "service_quality": {
                "unanswered": {"total_groups": 1, "missed_groups": 0},
                "sentiment": {"customer_good": 1, "customer_bad": 0},
            },
            "data_quality": {"project_codes": 1, "matched_project_codes": 1},
        }
        scope = {
            "project_codes": ["LC-P1"], "group_names": ["允许产品群"],
            "raw_rows": [{"groupName": "允许产品群"}],
            "group_rows": [{"name": "允许产品群"}],
            "chat_rows": [{"roomid": "room-1", "content": "测试"}],
        }

        @contextmanager
        def fake_database(_operation):
            yield object()

        with (
            patch.object(db_dashboard, "get_overview", return_value=overview),
            patch.object(db_dashboard, "database", fake_database),
            patch.object(db_dashboard, "_query_qx_raw_scope", return_value=scope),
            patch.object(db_dashboard, "fetch_lims_base_data", return_value=({
                "LC-P1": [{
                    "projectCode": "LC-P1", "productBigSortTwo": "常规转录组",
                    "productName": "RNA", "customerName": "客户甲",
                }]
            }, {"requests": 1, "records": 1, "errors": 0})),
        ):
            content = db_dashboard.get_export_excel(
                "custom", "2026-07-01", "2026-07-08"
            )

        workbook = load_workbook(BytesIO(content), read_only=True)
        self.assertTrue({
            "导出说明", "经营摘要", "高频关注主题", "重点客户", "项目状态关注", "服务质量",
            "analysis原始数据", "group原始数据", "chat原始数据", "LIMS原始数据",
        }.issubset(set(workbook.sheetnames)))
        self.assertEqual(workbook["高频关注主题"]["A2"].value, "转录")
        self.assertEqual(workbook["项目状态关注"]["A2"].value, "问题项目")
        self.assertEqual(workbook["LIMS原始数据"]["B2"].value, "LC-P1")

    def test_lims_record_keeps_work_unit_and_l2_category(self):
        item = db_dashboard.normalize_lims_api_record(
            {
                "projectCode": "LC-P2026001",
                "workUnit": "客户单位A",
                "productBigSortTwo": "常规转录组",
                "productBigSortThree": "mRNA",
                "analysisSimpleRemark": "  问题项目  ",
            },
            "LC-P2026001",
        )

        self.assertEqual(item["work_unit"], "客户单位A")
        self.assertEqual(item["category_l2"], "常规转录组")
        self.assertEqual(item["analysis_simple_remark"], "问题项目")

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

    def test_extract_missed_messages_parses_pure_text_with_multiple_messages(self):
        """用户实际场景：missedMessageList 存的是 '<sender>:<content>' 纯文本。"""
        text = '南枝:这个组Ss_Tcorolla只有2、3吗 \n 1呢,南枝:@李祖杰 这会能电话沟通下吗'
        result = db_dashboard._extract_missed_messages(text)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["sender_name"], "南枝")
        self.assertIn("这个组Ss_Tcorolla", result[0]["content"])
        self.assertEqual(result[1]["sender_name"], "南枝")
        self.assertIn("@李祖杰", result[1]["content"])

    def test_extract_missed_messages_handles_json_with_msgTime_alias(self):
        """Java 端写入时使用 msgTime 字段名也应能解析。"""
        text = '[{"id":"m1","msgTime":"2026-07-11 09:30:00","text":"怎么弄","sender":"A"}]'
        result = db_dashboard._extract_missed_messages(text)
        self.assertEqual(result[0]["msgtime"], "2026-07-11 09:30:00")
        self.assertEqual(result[0]["content"], "怎么弄")

    def test_extract_missed_messages_fallback_for_plain_text(self):
        """不是 sender:content 格式也不是 JSON 时，整段当作一条 content。"""
        text = "这是一段普通文本，没有冒号分隔"
        result = db_dashboard._extract_missed_messages(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], text)
        self.assertEqual(result[0]["sender_name"], "")

    def test_pure_text_missed_messages_get_msgtime_via_raw_chat_match(self):
        """纯文本格式解析出的漏回消息，能通过 content 匹配拿到 qx_chat 的 msgtime。"""
        text = '南枝:这个组Ss_Tcorolla只有2、3吗 \n 1呢,南枝:@李祖杰 这会能电话沟通下吗'
        missed = db_dashboard._extract_missed_messages(text)
        raw_messages = [{
            "msgid": "r1",
            "sender_name": "南枝",
            "content": "这个组Ss_Tcorolla只有2、3吗\n 1呢",
            "msgtime": "2026-07-11 13:42:15",
        }, {
            "msgid": "r2",
            "sender_name": "南枝",
            "content": "@李祖杰 这会能电话沟通下吗",
            "msgtime": "2026-07-11 13:45:30",
        }]

        display = db_dashboard._evidence_messages("unanswered", "", "", missed, raw_messages)

        self.assertEqual(len(display), 2)
        self.assertEqual(display[0]["msgtime"], "2026-07-11 13:42:15")
        self.assertEqual(display[1]["msgtime"], "2026-07-11 13:45:30")

    def test_get_evidence_uses_CREATEDTIME_as_final_fallback(self):
        """display 和 raw 都没有 msgtime 时，用分析行的 CREATEDTIME 日期兜底。"""
        from contextlib import contextmanager

        mock_row = {
            "id": 1,
            "groupName": "客户 LC-P2026001 项目群",
            "isMissedMessage": "1",
            "missedMessageList": "客户A:催一下结果",
            "CREATEDTIME": "2026-07-11 14:00:00",
            "messageToDayCount": 0,
            "highFrequencyWords": "",
            "customerEmotionAnalysis": "",
            "saleEmotionAnalysis": "",
            "afterSalesTopicAnalysis": "",
        }
        test_dimension = {
            "客户 LC-P2026001 项目群": {
                "codes": ["LC-P2026001"], "regions": [], "aftersalers": [],
                "projects": [{
                    "project_code": "LC-P2026001", "category_l2": "常规转录组",
                    "region": "", "raw_aftersaler": "",
                }],
            },
        }

        @contextmanager
        def fake_database(_operation):
            yield object()

        with (
            patch.object(db_dashboard, "database", fake_database),
            patch.object(db_dashboard, "_latest_rows", return_value=([mock_row], 1)),
            patch.object(db_dashboard, "_load_dimensions", return_value=(test_dimension, {"matched_project_codes": 1})),
            patch.object(db_dashboard, "_query_group_rows", return_value=[]),
            patch.object(db_dashboard, "_query_chat_rows", return_value=[]),
        ):
            response = db_dashboard.get_evidence(
                "unanswered", "custom", "2026-07-11", "2026-07-11",
            )

        matched = [
            item for item in response.get("items", [])
            if item.get("group_name") == "客户 LC-P2026001 项目群"
        ]
        self.assertTrue(matched, "应该有一条匹配客户群LC-P2026001的证据")
        self.assertTrue(matched[0]["msg_times"], "msg_times 应该有兜底日期")
        self.assertTrue(matched[0]["msg_times"][0].startswith("2026-07-11"))

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
