import json
from datetime import datetime

from services.preprocessor import NormalizedMessage, Preprocessor, parse_members_payload
from services.unanswered import UnansweredAnalyzer
from services.qxchat_analyzer import QxChatAnalyzer


def message(
    msgid: str,
    role: str,
    content: str,
    msgtime: str,
    userid: str = "wmCustomer001",
) -> NormalizedMessage:
    return NormalizedMessage(
        msgid=msgid,
        seq=1,
        roomid="room-1",
        from_userid=userid,
        sender_name="客户甲" if role == "客户" else "徐工",
        sender_position="",
        sender_job="",
        sender_role=role,
        text_content=content,
        msgtime=msgtime,
        msgtype="text",
        raw_content=content,
    )


def analyzer() -> UnansweredAnalyzer:
    return object.__new__(UnansweredAnalyzer)


def test_preprocessor_infers_external_customer_and_internal_employee_without_members():
    result = Preprocessor().process([
        {
            "msgid": "c1", "roomid": "room-1", "from": "wmCustomer001",
            "truename": "客户甲", "content": json.dumps({"content": "请问进度？"}),
            "msgtime": "2026-07-11 10:00:00",
        },
        {
            "msgid": "e1", "roomid": "room-1", "from": "XuJun",
            "truename": "徐工", "content": json.dumps({"content": "下午给您。"}),
            "msgtime": "2026-07-11 11:00:00",
        },
    ])
    assert [item.sender_role for item in result] == ["客户", "员工"]


def test_preprocessor_does_not_expose_roomid_as_sender_name():
    result = Preprocessor().process([{
        "msgid": "x1", "roomid": "room-1", "from": "room-1",
        "truename": "room-1", "content": "请问进度？",
        "msgtime": "2026-07-11 10:00:00",
    }])
    assert result[0].sender_name == "未知发送人"
    assert result[0].sender_role == "未知"


def test_parse_members_accepts_string_member_type():
    members = parse_members_payload('[{"userid":"wmCustomer001","name":"客户甲","type":"2"}]')
    assert members["wmCustomer001"].is_customer is True


def test_external_userid_remains_customer_when_member_type_is_missing():
    result = Preprocessor().process([{
        "msgid": "c1", "roomid": "room-1", "from": "wmCustomer001",
        "truename": "客户甲", "members": '[{"userid":"wmCustomer001","name":"客户甲"}]',
        "content": "请问进度？", "msgtime": "2026-07-11 10:00:00",
    }])
    assert result[0].sender_role == "客户"


def test_employee_reply_closes_previous_customer_message():
    result = analyzer().analyze([
        message("c1", "客户", "请问什么时候出结果？", "2026-07-11 10:00:00"),
        message("e1", "员工", "预计下午给您。", "2026-07-11 11:00:00", "XuJun"),
    ], analysis_time=datetime(2026, 7, 11, 12, 0, 0))
    assert result["is_missed"] is False
    assert result["decision_status"] == "answered"


def test_phone_number_and_closing_message_do_not_need_reply():
    result = analyzer().analyze([
        message("c1", "客户", "15642622729", "2026-07-11 10:00:00"),
        message("c2", "客户", "好的，谢谢", "2026-07-11 10:01:00"),
    ], analysis_time=datetime(2026, 7, 11, 12, 0, 0))
    assert result["is_missed"] is False
    assert result["decision_status"] == "no_action_needed"


def test_message_inside_observation_window_is_pending():
    result = analyzer().analyze([
        message("c1", "客户", "请问什么时候出结果？", "2026-07-11 11:30:00"),
    ], analysis_time=datetime(2026, 7, 11, 12, 0, 0))
    assert result["is_missed"] is False
    assert result["decision_status"] == "pending"
    assert result["review_required"] is True


def test_llm_can_select_specific_candidate_and_returns_original_time():
    instance = analyzer()

    class FakeClient:
        def chat(self, *_args, **_kwargs):
            return json.dumps({
                "is_missed": True,
                "risk_level": "high",
                "missed_msgids": ["c2"],
                "explanation": "客户在催促进度。",
                "suggested_action": "回复进度。",
            }, ensure_ascii=False)

    instance.llm_client = FakeClient()
    result = instance.analyze([
        message("c1", "客户", "补充一下样本编号", "2026-07-11 09:00:00"),
        message("c2", "客户", "请问什么时候出结果？", "2026-07-11 10:00:00"),
    ], analysis_time=datetime(2026, 7, 11, 12, 0, 0))
    assert result["is_missed"] is True
    assert [item["msgid"] for item in result["missed_messages"]] == ["c2"]
    assert result["missed_messages"][0]["msgtime"] == "2026-07-11 10:00:00"
    assert result["missed_messages"][0]["time_source"] == "original_message"


def test_model_failure_never_defaults_to_missed():
    result = analyzer()._fallback_result(
        [message("c1", "客户", "请问进度？", "2026-07-11 10:00:00")],
        datetime(2026, 7, 11, 12, 0, 0),
    )
    assert result["is_missed"] is False
    assert result["decision_status"] == "insufficient_data"
    assert result["review_required"] is True


def test_qxchat_module_accepts_any_later_employee_reply_even_after_one_hour():
    result = QxChatAnalyzer()._compute_unanswered([{
        "room_id": "room-1", "room_name": "测试群", "messages": [
            {
                "msgid": "c1", "roomid": "room-1", "from": "wmCustomer001",
                "truename": "客户甲", "content": "请问什么时候出结果？",
                "msgtime": "2026-01-01 10:00:00",
            },
            {
                "msgid": "e1", "roomid": "room-1", "from": "XuJun",
                "truename": "徐工", "content": "今天给您。",
                "msgtime": "2026-01-01 15:00:00",
            },
        ],
    }])
    assert result["total_missed"] == 0


def test_qxchat_module_handles_single_unanswered_customer_request():
    result = QxChatAnalyzer()._compute_unanswered([{
        "room_id": "room-1", "room_name": "测试群", "messages": [{
            "msgid": "c1", "roomid": "room-1", "from": "wmCustomer001",
            "truename": "客户甲", "content": "请问什么时候出结果？",
            "msgtime": "2026-01-01 10:00:00",
        }],
    }])
    assert result["total_missed"] == 1
    assert result["missed_details"][0]["sender_role"] == "客户"
    assert result["missed_details"][0]["time_source"] == "original_message"
