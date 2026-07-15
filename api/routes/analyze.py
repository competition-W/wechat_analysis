from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from datetime import datetime
import asyncio
import json
import time
from typing import List, Optional

from models.request import AnalyzeRequest, AnalysisType, BatchAnalyzeRequest, RoomData
from config.settings import settings
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
    MemberInfo,
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
        normalized = await asyncio.to_thread(preprocessor.process, messages_dict)
        
        members_list = []
        seen_userids = set()
        
        if request.members:
            members_raw = request.members
            if isinstance(members_raw, str):
                try:
                    req_members = json.loads(members_raw)
                    if isinstance(req_members, list):
                        for m in req_members:
                            if isinstance(m, dict):
                                userid = m.get("userid", "")
                                if userid and userid not in seen_userids:
                                    seen_userids.add(userid)
                                    members_list.append(MemberInfo(
                                        userid=userid,
                                        name=m.get("name", ""),
                                        group_nickname=m.get("group_nickname", ""),
                                        type=m.get("type", 1),
                                        job=m.get("job", ""),
                                        position=m.get("position", ""),
                                    ))
                except:
                    pass
            elif isinstance(members_raw, list):
                for m in members_raw:
                    if isinstance(m, dict):
                        userid = m.get("userid", "")
                        if userid and userid not in seen_userids:
                            seen_userids.add(userid)
                            members_list.append(MemberInfo(
                                userid=userid,
                                name=m.get("name", ""),
                                group_nickname=m.get("group_nickname", ""),
                                type=m.get("type", 1),
                                job=m.get("job", ""),
                                position=m.get("position", ""),
                            ))
        
        for msg in request.messages:
            msg_dict = msg.model_dump() if hasattr(msg, 'model_dump') else {}
            members_raw = msg_dict.get("members", [])
            
            members_data = []
            if members_raw:
                if isinstance(members_raw, str):
                    try:
                        members_data = json.loads(members_raw)
                    except:
                        members_data = []
                elif isinstance(members_raw, list):
                    members_data = members_raw
            
            if members_data and isinstance(members_data, list):
                for m in members_data:
                    if isinstance(m, dict):
                        userid = m.get("userid", "")
                        if userid and userid not in seen_userids:
                            seen_userids.add(userid)
                            members_list.append(MemberInfo(
                                userid=userid,
                                name=m.get("name", ""),
                                group_nickname=m.get("group_nickname", ""),
                                type=m.get("type", 1),
                                job=m.get("job", ""),
                                position=m.get("position", ""),
                            ))
        
        if not normalized:
            return AnalyzeResponse(
                code=0,
                message="success",
                data=ResponseData(
                    room_id=request.room_id,
                    room_name=request.room_name,
                    message_count=0,
                    members=members_list,
                )
            )
        
        analysis_types = request.get_analysis_types()
        msg_count = len(normalized)
        employee_reply_count = sum(1 for m in normalized if m.sender_role in ["员工", "售后", "销售"])
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
                try:
                    return await asyncio.to_thread(summary_generator.generate, normalized)
                except Exception as e:
                    logger.error(f"摘要生成失败: {e}")
                    return None
            return None
        
        async def run_highfreq():
            if AnalysisType.HIGHFREQ in analysis_types:
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
                employee_reply_count=employee_reply_count,
                members=members_list,
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
async def batch_analyze_chat(request: BatchAnalyzeRequest, raw_request: Request):
    body = await raw_request.body()
    total_messages = sum(len(room.messages) for room in request.rooms)
    analysis_types = request.get_analysis_types()
    logger.info(f"收到批量分析请求: rooms={len(request.rooms)}, max_concurrent={request.max_concurrent}, total_messages={total_messages}, analysis_types={[a.value for a in analysis_types]}")
    logger.info(f"原始请求body前500字符: {body[:500]}")

    for i, room in enumerate(request.rooms[:3]):
        sample_msg = room.messages[0] if room.messages else None
        if sample_msg:
            msg_dict = sample_msg.model_dump()
            first_from = msg_dict.get('from') or msg_dict.get('from_')
            first_content = msg_dict.get('content', '')
            first_msgtype = msg_dict.get('msgtype', '空')
            first_members = msg_dict.get('members', '')
            logger.info(f"请求样例[{i}]: room_id={room.room_id}, room_name={room.room_name}, messages_count={len(room.messages)}, first_msg_from={first_from}, first_msg_content={first_content[:50] if first_content else '空'}, first_msg_msgtype={first_msgtype}, first_msg_members类型={type(first_members).__name__}")
    
    start_time = time.time()
    analysis_types = request.get_analysis_types()
    semaphore = asyncio.Semaphore(request.max_concurrent or settings.LLM_MAX_CONCURRENT)
    
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
                employee_reply_count = sum(1 for m in normalized if m.sender_role in ["员工", "售后", "销售"])
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
                        try:
                            return await asyncio.to_thread(summary_generator.generate, normalized)
                        except Exception as e:
                            logger.error(f"群 {room.room_id} 摘要生成失败: {e}")
                            return None
                    return None
                
                async def run_highfreq():
                    if AnalysisType.HIGHFREQ in analysis_types:
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

                logger.info(f"群 {room.room_id} 分析结果检查: sentiment={'有' if sentiment_result else '空'}, sensitive={'有' if sensitive_result else '空'}, summary={'有' if summary_result else '空'}, highfreq={'有' if highfreq_result else '空'}, unanswered={'有' if unanswered_result else '空'}")
                
                members_list = []
                seen_userids = set()
                
                if room.members:
                    members_raw = room.members
                    if isinstance(members_raw, str):
                        try:
                            room_members = json.loads(members_raw)
                            if isinstance(room_members, list):
                                for m in room_members:
                                    if isinstance(m, dict):
                                        userid = m.get("userid", "")
                                        if userid and userid not in seen_userids:
                                            seen_userids.add(userid)
                                            members_list.append(MemberInfo(
                                                userid=userid,
                                                name=m.get("name", ""),
                                                group_nickname=m.get("group_nickname", ""),
                                                type=m.get("type", 1),
                                                job=m.get("job", ""),
                                                position=m.get("position", ""),
                                            ))
                        except:
                            pass
                    elif isinstance(members_raw, list):
                        for m in members_raw:
                            if isinstance(m, dict):
                                userid = m.get("userid", "")
                                if userid and userid not in seen_userids:
                                    seen_userids.add(userid)
                                    members_list.append(MemberInfo(
                                        userid=userid,
                                        name=m.get("name", ""),
                                        group_nickname=m.get("group_nickname", ""),
                                        type=m.get("type", 1),
                                        job=m.get("job", ""),
                                        position=m.get("position", ""),
                                    ))
                
                for msg in room.messages:
                    msg_dict = msg.model_dump() if hasattr(msg, 'model_dump') else {}
                    members_raw = msg_dict.get("members", [])
                    
                    members_data = []
                    if members_raw:
                        if isinstance(members_raw, str):
                            try:
                                members_data = json.loads(members_raw)
                            except:
                                members_data = []
                        elif isinstance(members_raw, list):
                            members_data = members_raw
                    
                    if members_data and isinstance(members_data, list):
                        for m in members_data:
                            if isinstance(m, dict):
                                userid = m.get("userid", "")
                                if userid and userid not in seen_userids:
                                    seen_userids.add(userid)
                                    members_list.append(MemberInfo(
                                        userid=userid,
                                        name=m.get("name", ""),
                                        group_nickname=m.get("group_nickname", ""),
                                        type=m.get("type", 1),
                                        job=m.get("job", ""),
                                        position=m.get("position", ""),
                                    ))
                
                return RoomAnalysisResult(
                    room_id=room.room_id,
                    room_name=room.room_name,
                    status="success",
                    data=ResponseData(
                        room_id=room.room_id,
                        room_name=room.room_name,
                        analysis_time=datetime.now().isoformat(),
                        message_count=len(normalized),
                        employee_reply_count=employee_reply_count,
                        members=members_list,
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

    response_data = BatchResponseData(
        total_rooms=len(results),
        success_count=success_count,
        failed_count=failed_count,
        analysis_time=datetime.now().isoformat(),
        elapsed_seconds=round(elapsed, 2),
        results=list(results),
    )

    response_json = response_data.model_dump_json()
    logger.info(f"返回数据大小: {len(response_json)} 字符")

    return BatchAnalyzeResponse(
        code=0,
        message="success",
        data=response_data,
    )
