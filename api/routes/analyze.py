from fastapi import APIRouter, HTTPException
from loguru import logger
from datetime import datetime
import asyncio
import time
from typing import List, Optional

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

SUMMARY_SHORT_CIRCUIT_THRESHOLD = 5
HIGHFREQ_SHORT_CIRCUIT_MSG_THRESHOLD = 3
HIGHFREQ_SHORT_CIRCUIT_CHARS_THRESHOLD = 50
DEFAULT_SUMMARY_FOR_SHORT = "群内互动较少，暂无核心议题。"


@router.post("/chat/analyze", response_model=AnalyzeResponse)
async def analyze_chat(request: AnalyzeRequest):
    logger.info(f"收到分析请求: room_id={request.room_id}, messages={len(request.messages)}")
    
    try:
        messages_dict = [msg.model_dump(by_alias=True) for msg in request.messages]
        normalized = await asyncio.to_thread(preprocessor.process, messages_dict)
        
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
        msg_count = len(normalized)
        total_chars = sum(len(m.text_content or "") for m in normalized)
        
        async def run_sentiment():
            if AnalysisType.SENTIMENT in analysis_types:
                try:
                    return await asyncio.to_thread(sentiment_analyzer.analyze, normalized)
                except Exception as e:
                    logger.error(f"情感分析失败: {e}")
                    return None
            return None
        
        async def run_sensitive():
            if AnalysisType.SENSITIVE in analysis_types:
                try:
                    detected = await asyncio.to_thread(sensitive_detector.detect, normalized)
                    return SensitiveResult(
                        total_hits=detected["total_hits"],
                        words=[SensitiveWordItem(**w) for w in detected["words"]],
                    )
                except Exception as e:
                    logger.error(f"敏感词检测失败: {e}")
                    return None
            return None
        
        async def run_summary():
            if AnalysisType.SUMMARY in analysis_types:
                if msg_count < SUMMARY_SHORT_CIRCUIT_THRESHOLD:
                    logger.info(f"消息数 {msg_count} < {SUMMARY_SHORT_CIRCUIT_THRESHOLD}，摘要短路返回默认值")
                    return DEFAULT_SUMMARY_FOR_SHORT
                try:
                    return await asyncio.to_thread(summary_generator.generate, normalized)
                except Exception as e:
                    logger.error(f"摘要生成失败: {e}")
                    return None
            return None
        
        async def run_highfreq():
            if AnalysisType.HIGHFREQ in analysis_types:
                if msg_count < HIGHFREQ_SHORT_CIRCUIT_MSG_THRESHOLD:
                    logger.info(f"消息数 {msg_count} < {HIGHFREQ_SHORT_CIRCUIT_MSG_THRESHOLD}，高频词短路返回空列表")
                    return HighFreqResult(words=[])
                if total_chars < HIGHFREQ_SHORT_CIRCUIT_CHARS_THRESHOLD:
                    logger.info(f"总字数 {total_chars} < {HIGHFREQ_SHORT_CIRCUIT_CHARS_THRESHOLD}，高频词短路返回空列表")
                    return HighFreqResult(words=[])
                try:
                    words = await asyncio.to_thread(highfreq_analyzer.analyze, normalized)
                    return HighFreqResult(
                        words=[HighFreqWordItem(**w) for w in words],
                    )
                except Exception as e:
                    logger.error(f"高频词分析失败: {e}")
                    return HighFreqResult(words=[])
            return None
        
        async def run_unanswered():
            if AnalysisType.UNANSWERED in analysis_types:
                try:
                    result = await asyncio.to_thread(unanswered_analyzer.analyze, normalized)
                    return UnansweredResult(
                        is_missed=result["is_missed"],
                        risk_level=result["risk_level"],
                        missed_messages=[UnansweredDetail(**m) for m in result["missed_messages"]],
                        suggested_action=result["suggested_action"],
                    )
                except Exception as e:
                    logger.error(f"漏回分析失败: {e}")
                    return None
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
                normalized = await asyncio.to_thread(preprocessor.process, messages_dict)
                
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
                
                msg_count = len(normalized)
                total_chars = sum(len(m.text_content or "") for m in normalized)
                
                async def run_sentiment():
                    if AnalysisType.SENTIMENT in analysis_types:
                        try:
                            return await asyncio.to_thread(sentiment_analyzer.analyze, normalized)
                        except Exception as e:
                            logger.error(f"群 {room.room_id} 情感分析失败: {e}")
                            return None
                    return None
                
                async def run_sensitive():
                    if AnalysisType.SENSITIVE in analysis_types:
                        try:
                            detected = await asyncio.to_thread(sensitive_detector.detect, normalized)
                            return SensitiveResult(
                                total_hits=detected["total_hits"],
                                words=[SensitiveWordItem(**w) for w in detected["words"]],
                            )
                        except Exception as e:
                            logger.error(f"群 {room.room_id} 敏感词检测失败: {e}")
                            return None
                    return None
                
                async def run_summary():
                    if AnalysisType.SUMMARY in analysis_types:
                        if msg_count < SUMMARY_SHORT_CIRCUIT_THRESHOLD:
                            return DEFAULT_SUMMARY_FOR_SHORT
                        try:
                            return await asyncio.to_thread(summary_generator.generate, normalized)
                        except Exception as e:
                            logger.error(f"群 {room.room_id} 摘要生成失败: {e}")
                            return None
                    return None
                
                async def run_highfreq():
                    if AnalysisType.HIGHFREQ in analysis_types:
                        if msg_count < HIGHFREQ_SHORT_CIRCUIT_MSG_THRESHOLD:
                            return HighFreqResult(words=[])
                        if total_chars < HIGHFREQ_SHORT_CIRCUIT_CHARS_THRESHOLD:
                            return HighFreqResult(words=[])
                        try:
                            words = await asyncio.to_thread(highfreq_analyzer.analyze, normalized)
                            return HighFreqResult(
                                words=[HighFreqWordItem(**w) for w in words],
                            )
                        except Exception as e:
                            logger.error(f"群 {room.room_id} 高频词分析失败: {e}")
                            return HighFreqResult(words=[])
                    return None
                
                async def run_unanswered():
                    if AnalysisType.UNANSWERED in analysis_types:
                        try:
                            result = await asyncio.to_thread(unanswered_analyzer.analyze, normalized)
                            return UnansweredResult(
                                is_missed=result["is_missed"],
                                risk_level=result["risk_level"],
                                missed_messages=[UnansweredDetail(**m) for m in result["missed_messages"]],
                                suggested_action=result["suggested_action"],
                            )
                        except Exception as e:
                            logger.error(f"群 {room.room_id} 漏回分析失败: {e}")
                            return None
                    return None
                
                sentiment_result, sensitive_result, summary_result, highfreq_result, unanswered_result = await asyncio.gather(
                    run_sentiment(),
                    run_sensitive(),
                    run_summary(),
                    run_highfreq(),
                    run_unanswered(),
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
