from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from enum import Enum


class AnalysisType(str, Enum):
    SENTIMENT = "sentiment"
    SENSITIVE = "sensitive"
    SUMMARY = "summary"
    HIGHFREQ = "highfreq"
    UNANSWERED = "unanswered"


class MessageItem(BaseModel):
    seq: Optional[int] = None
    msgid: Optional[str] = None
    action: Optional[str] = None
    from_: Optional[str] = Field(default=None, alias="from")
    tolist: Optional[str] = None
    roomid: Optional[str] = None
    msgtime: Optional[str] = None
    msgtype: Optional[str] = None
    content: Optional[str] = None
    resultAsrTime: Optional[str] = None
    resultContent: Optional[str] = None
    filepath: Optional[str] = None
    openId: Optional[str] = None
    truename: Optional[str] = None
    avatar: Optional[str] = None
    position: Optional[str] = None
    reOpenId: Optional[str] = None
    reTruename: Optional[str] = None
    reAvatar: Optional[str] = None
    rePosition: Optional[str] = None
    isSingle: Optional[str] = None
    contentItem: Optional[str] = None
    rawJson: Optional[str] = None
    job: Optional[str] = None
    members: Optional[str] = None

    class Config:
        populate_by_name = True


class AnalyzeRequest(BaseModel):
    room_id: str = Field(..., description="群聊唯一标识")
    room_name: Optional[str] = Field(default=None, description="群名称")
    members: Optional[str] = Field(default=None, description="群成员信息，JSON字符串数组格式")
    analysis_type: Optional[List[AnalysisType]] = Field(
        default=None,
        description="需启用的分析类型，默认全部"
    )
    messages: List[MessageItem] = Field(..., description="原始消息数组")
    
    def get_analysis_types(self) -> List[AnalysisType]:
        if self.analysis_type is None or len(self.analysis_type) == 0:
            return list(AnalysisType)
        return self.analysis_type


class RoomData(BaseModel):
    room_id: str = Field(..., description="群聊唯一标识")
    room_name: Optional[str] = Field(default=None, description="群名称")
    members: Optional[str] = Field(default=None, description="群成员信息，JSON字符串数组格式，与messages[].members相同")
    messages: List[MessageItem] = Field(..., description="该群的消息数组")


class BatchAnalyzeRequest(BaseModel):
    rooms: List[RoomData] = Field(..., description="多个群的数据列表")
    analysis_type: Optional[List[AnalysisType]] = Field(
        default=None,
        description="需启用的分析类型，默认全部"
    )
    max_concurrent: Optional[int] = Field(
        default=15,
        description="最大并发处理数，默认15（覆盖 settings.LLM_MAX_CONCURRENT=10，可按需调整）"
    )
    
    def get_analysis_types(self) -> List[AnalysisType]:
        if self.analysis_type is None or len(self.analysis_type) == 0:
            return list(AnalysisType)
        return self.analysis_type
