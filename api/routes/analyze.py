from fastapi import APIRouter, HTTPException
from loguru import logger
from datetime import datetime
import asyncio
import time
from typing import List

from models.request import AnalyzeRequest, AnalysisType, BatchAnalyzeRequest, RoomData
from models.response import (
    AnalyzeResponse,
    ResponseData,
    SentimentResult,
    SensitiveResult,
    SensitiveWordItem,
    HighFreqResult,
    HighFreqWordItem,
    BatchAnalyzeResponse,
    BatchResponseData,
    RoomAnalysisResult,
    UnansweredResult,
    UnansweredDetail,
)
from services import (
    Preprocessor,
    SentimentAnalyzer,
    SensitiveWordDetector,
    SummaryGenerator,
    HighFreqAnalyzer,
    UnansweredAnalyzer,
)

router = APIRouter(prefix="/api/v1", tags=["analysis"])

preprocessor = Preprocessor()
sentiment_analyzer = SentimentAnalyzer()
sensitive_detector = SensitiveWordDetector()
summary_generator = SummaryGenerator()
highfreq_analyzer = HighFreqAnalyzer()
unanswered_analyzer = UnansweredAnalyzer()


@router.post("/chat/analyze", response_model=AnalyzeResponse)
async def analyze_chat(request: AnalyzeRequest):
    logger.info(f"收到分析请求: room_id={request.room_id}, messages={len(request.messages)}")
    
    try:
        messages_dict = [msg.model_dump(by_alias=True) for msg in request.messages]
        normalized = preprocessor.process(messages_dict)
        
        if not normalized:
            return AnalyzeResponse(
                code=0,
                message="success",
                data=ResponseData(
                    room_id=request.room_id,
                    room_name=request.room_name,
                    message_count=0,
                )
            )
        
        analysis_types = request.get_analysis_types()
        
        async def run_sentiment():
            if AnalysisType.SENTIMENT in analysis_types:
                return sentiment_analyzer.analyze(normalized)
            return None
        
        async def run_sensitive():
            if AnalysisType.SENSITIVE in analysis_types:
                detected = sensitive_detector.detect(normalized)
                return SensitiveResult(
                    total_hits=detected["total_hits"],
                    words=[SensitiveWordItem(**w) for w in detected["words"]],
                )
            return None
        
        async def run_summary():
            if AnalysisType.SUMMARY in analysis_types:
                return summary_generator.generate(normalized)
            return None
        
        async def run_highfreq():
            if AnalysisType.HIGHFREQ in analysis_types:
                words = highfreq_analyzer.analyze(normalized)
                return HighFreqResult(
                    words=[HighFreqWordItem(**w) for w in words],
                )
            return None
        
        async def run_unanswered():
            if AnalysisType.UNANSWERED in analysis_types:
                result = unanswered_analyzer.analyze(normalized)
                return UnansweredResult(
                    is_missed=result["is_missed"],
                    risk_level=result["risk_level"],
                    missed_messages=[UnansweredDetail(**m) for m in result["missed_messages"]],
                    suggested_action=result["suggested_action"],
                )
            return None
        
        sentiment_result, sensitive_result, summary_result, highfreq_result, unanswered_result = await asyncio.gather(
            run_sentiment(),
            run_sensitive(),
            run_summary(),
            run_highfreq(),
            run_unanswered(),
        )
        
        return AnalyzeResponse(
            code=0,
            message="success",
            data=ResponseData(
                room_id=request.room_id,
                room_name=request.room_name,
                analysis_time=datetime.now().isoformat(),
                message_count=len(normalized),
                sentiment=sentiment_result,
                sensitive_words=sensitive_result,
                summary=summary_result,
                high_freq_words=highfreq_result,
                unanswered_status=unanswered_result,
            )
        )
    
    except Exception as e:
        logger.error(f"分析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@router.post("/chat/batch-analyze", response_model=BatchAnalyzeResponse)
async def batch_analyze_chat(request: BatchAnalyzeRequest):
    logger.info(f"收到批量分析请求: rooms={len(request.rooms)}, max_concurrent={request.max_concurrent}")
    
    start_time = time.time()
    analysis_types = request.get_analysis_types()
    semaphore = asyncio.Semaphore(request.max_concurrent or 5)
    
    async def analyze_single_room(room: RoomData) -> RoomAnalysisResult:
        async with semaphore:
            try:
                messages_dict = [msg.model_dump(by_alias=True) for msg in room.messages]
                normalized = preprocessor.process(messages_dict)
                
                if not normalized:
                    return RoomAnalysisResult(
                        room_id=room.room_id,
                        room_name=room.room_name,
                        status="success",
                        data=ResponseData(
                            room_id=room.room_id,
                            room_name=room.room_name,
                            message_count=0,
                        )
                    )
                
                sentiment_result = None
                sensitive_result = None
                summary_result = None
                highfreq_result = None
                unanswered_result = None
                
                if AnalysisType.SENTIMENT in analysis_types:
                    sentiment_result = sentiment_analyzer.analyze(normalized)
                
                if AnalysisType.SENSITIVE in analysis_types:
                    detected = sensitive_detector.detect(normalized)
                    sensitive_result = SensitiveResult(
                        total_hits=detected["total_hits"],
                        words=[SensitiveWordItem(**w) for w in detected["words"]],
                    )
                
                if AnalysisType.SUMMARY in analysis_types:
                    summary_result = summary_generator.generate(normalized)
                
                if AnalysisType.HIGHFREQ in analysis_types:
                    words = highfreq_analyzer.analyze(normalized)
                    highfreq_result = HighFreqResult(
                        words=[HighFreqWordItem(**w) for w in words],
                    )
                
                if AnalysisType.UNANSWERED in analysis_types:
                    result = unanswered_analyzer.analyze(normalized)
                    unanswered_result = UnansweredResult(
                        is_missed=result["is_missed"],
                        risk_level=result["risk_level"],
                        missed_messages=[UnansweredDetail(**m) for m in result["missed_messages"]],
                        suggested_action=result["suggested_action"],
                    )
                
                return RoomAnalysisResult(
                    room_id=room.room_id,
                    room_name=room.room_name,
                    status="success",
                    data=ResponseData(
                        room_id=room.room_id,
                        room_name=room.room_name,
                        analysis_time=datetime.now().isoformat(),
                        message_count=len(normalized),
                        sentiment=sentiment_result,
                        sensitive_words=sensitive_result,
                        summary=summary_result,
                        high_freq_words=highfreq_result,
                        unanswered_status=unanswered_result,
                    )
                )
            
            except Exception as e:
                logger.error(f"分析群 {room.room_id} 失败: {e}")
                return RoomAnalysisResult(
                    room_id=room.room_id,
                    room_name=room.room_name,
                    status="failed",
                    error_message=str(e),
                )
    
    tasks = [analyze_single_room(room) for room in request.rooms]
    results = await asyncio.gather(*tasks)
    
    elapsed = time.time() - start_time
    success_count = sum(1 for r in results if r.status == "success")
    failed_count = len(results) - success_count
    
    logger.info(f"批量分析完成: total={len(results)}, success={success_count}, failed={failed_count}, elapsed={elapsed:.2f}s")
    
    return BatchAnalyzeResponse(
        code=0,
        message="success",
        data=BatchResponseData(
            total_rooms=len(results),
            success_count=success_count,
            failed_count=failed_count,
            analysis_time=datetime.now().isoformat(),
            elapsed_seconds=round(elapsed, 2),
            results=list(results),
        )
    )
