from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


class CustomerSentimentStats(BaseModel):
    good_reviews: int = 0
    bad_reviews: int = 0


class EmployeeSentimentStats(BaseModel):
    positive: int = 0
    bad_attitude: int = 0


class SentimentSummary(BaseModel):
    customer: CustomerSentimentStats = Field(default_factory=CustomerSentimentStats)
    employee: EmployeeSentimentStats = Field(default_factory=EmployeeSentimentStats)


class SentimentDetailItem(BaseModel):
    msgid: str
    sender_name: str
    content: str
    msgtime: str
    confidence: Optional[float] = None


class SentimentDetails(BaseModel):
    customer_good: List[SentimentDetailItem] = Field(default_factory=list)
    customer_bad: List[SentimentDetailItem] = Field(default_factory=list)
    employee_positive: List[SentimentDetailItem] = Field(default_factory=list)
    employee_bad_attitude: List[SentimentDetailItem] = Field(default_factory=list)


class SentimentResult(BaseModel):
    summary: SentimentSummary = Field(default_factory=SentimentSummary)
    details: SentimentDetails = Field(default_factory=SentimentDetails)


class SensitiveWordHit(BaseModel):
    sender_name: Optional[str] = None
    sender_job: Optional[str] = None
    sender_position: Optional[str] = None
    content: Optional[str] = None
    msgtime: Optional[str] = None


class SensitiveWordItem(BaseModel):
    word: str
    count: int = 0
    hits: List[SensitiveWordHit] = Field(default_factory=list)


class SensitiveResult(BaseModel):
    total_hits: int = 0
    words: List[SensitiveWordItem] = Field(default_factory=list)


class HighFreqWordItem(BaseModel):
    word: str
    count: int = 0
    aliases: List[str] = Field(default_factory=list)


class HighFreqResult(BaseModel):
    words: List[HighFreqWordItem] = Field(default_factory=list)


class UnansweredDetail(BaseModel):
    msgid: str
    sender_name: str
    msgtime: str
    content: str


class UnansweredResult(BaseModel):
    is_missed: bool = False
    risk_level: str = "low"
    missed_messages: List[UnansweredDetail] = Field(default_factory=list)
    suggested_action: Optional[str] = None


class MemberInfo(BaseModel):
    userid: str
    name: str = ""
    group_nickname: str = ""
    type: int = 1
    job: str = ""
    position: str = ""


class ResponseData(BaseModel):
    room_id: str
    room_name: Optional[str] = None
    analysis_time: str = Field(default_factory=lambda: datetime.now().isoformat())
    message_count: int = 0
    employee_reply_count: int = 0
    members: List[MemberInfo] = Field(default_factory=list)
    sentiment: Optional[SentimentResult] = None
    sensitive_words: Optional[SensitiveResult] = None
    summary: Optional[str] = None
    high_freq_words: Optional[HighFreqResult] = None
    unanswered_status: Optional[UnansweredResult] = None


class AnalyzeResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Optional[ResponseData] = None


class RoomAnalysisResult(BaseModel):
    room_id: str
    room_name: Optional[str] = None
    status: str = "success"
    error_message: Optional[str] = None
    data: Optional[ResponseData] = None


class BatchResponseData(BaseModel):
    total_rooms: int = 0
    success_count: int = 0
    failed_count: int = 0
    analysis_time: str = Field(default_factory=lambda: datetime.now().isoformat())
    elapsed_seconds: float = 0.0
    results: List[RoomAnalysisResult] = Field(default_factory=list)


class BatchAnalyzeResponse(BaseModel):
    code: int = 0
    message: str = "success"
    data: Optional[BatchResponseData] = None
