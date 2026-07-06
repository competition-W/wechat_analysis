"""
Database dashboard service - reads analyzed data from MySQL
"""

import re
import json
from typing import Any, Dict, List, Optional
from loguru import logger

_pymysql = None
def _get_mysql():
    global _pymysql
    if _pymysql is None:
        import pymysql
        _pymysql = pymysql
    return _pymysql

_conn = None
def get_connection():
    global _conn
    from config.settings import settings
    try:
        if _conn is None or not _conn.open:
            pymysql = _get_mysql()
            _conn = pymysql.connect(
                host=settings.MYSQL_HOST,
                port=settings.MYSQL_PORT,
                user=settings.MYSQL_USER,
                password=settings.MYSQL_PASSWORD,
                database=settings.MYSQL_DATABASE,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
            )
        return _conn
    except Exception as e:
        logger.error(f"DB connection failed: {e}")
        raise

def close_connection():
    global _conn
    if _conn and _conn.open:
        _conn.close()
        _conn = None

def parse_emotion_field(field_value) -> dict:
    """Parse customer/sale emotion field"""
    if not field_value:
        return {}
    result = {}
    try:
        parts = str(field_value).split(",")
        for part in parts:
            part = part.strip()
            if ":" not in part:
                continue
            k, v = part.split(":", 1)
            result[k.strip().strip(chr(34)+chr(39))] = int(v.strip())
    except Exception as e:
        logger.warning(f'parse_emotion_field: {e}')
    return result

def parse_send_detail(field_value) -> dict:
    """Parse send message detail"""
    if not field_value:
        return {}
    result = {}
    try:
        for part in str(field_value).split(","):
            p = part.strip()
            if ":" not in p:
                continue
            k, v = p.split(":", 1)
            result[k.strip()] = int(v.strip())
    except Exception as e:
        logger.warning(f'parse_send_detail: {e}')
    return result

def parse_high_freq(field_value) -> list:
    """Parse high frequency words"""
    if not field_value:
        return []
    result = []
    try:
        for part in str(field_value).split(","):
            p = part.strip()
            if ":" not in p:
                continue
            k, v = p.split(":", 1)
            result.append({"word": k.strip(), "count": int(v.strip())})
    except Exception as e:
        logger.warning(f'parse_high_freq: {e}')
    return result

def get_project_codes(name: str) -> list:
    """Extract LC project codes from group name"""
    if not name:
        return []
    return re.findall(r"LC-[A-Z]+\d+", name)

def get_summary(date_str: str = None) -> dict:
    """Get dashboard summary stats"""
    conn = get_connection()
    cur = conn.cursor()
    where = ""
    p = []
    if date_str:
        where = "WHERE DATE(CREATEDTIME) = %s"
        p.append(date_str)
    cur.execute(
        "SELECT COUNT(*) as n, COALESCE(SUM(messageToDayCount),0) as msgs,"
        "COALESCE(SUM(saleAfterCount),0) as sa,"
        "SUM(CASE WHEN isMissedMessage='1' THEN 1 ELSE 0 END) as miss "
        f"FROM qx_analysis_result {where}", p)
    s = cur.fetchone()
    cur.execute(
        f"SELECT customerEmotionAnalysis, saleEmotionAnalysis "
        f"FROM qx_analysis_result {where}", p)

    cg, cb, sp, sn = 0, 0, 0, 0
    for r in cur.fetchall():
        ce = parse_emotion_field(r['customerEmotionAnalysis'])
        cg += ce.get(chr(22994)+chr(35780), 0)
        cb += ce.get(chr(24046)+chr(35780), 0)
        se = parse_emotion_field(r['saleEmotionAnalysis'])
        sp += se.get(chr(31215)+chr(26497)+chr(30340), 0)
        sn += se.get(chr(24577)+chr(24230)+chr(24694)+chr(21133)+chr(30340), 0)

    cur.execute(
        'SELECT COUNT(*) as n FROM qx_analysis_result'
        ' WHERE groupName LIKE %s'
        + (' AND DATE(CREATEDTIME)=%s' if date_str else ''),
        ['LC-%'] + ([date_str] if date_str else []))
    lc_n = cur.fetchone()['n']

    return {
        'total_groups': s['n'],
        'total_messages': s['msgs'],
        'total_sale_after': s['sa'],
        'date_range': date_str or 'all',
        'sentiment': {
            'customer_good': cg,
            'customer_bad': cb,
            'sale_positive': sp,
            'sale_negative': sn,
        },
        'missed_groups': s['miss'],
        'lc_groups': lc_n,
    }

def get_groups(date_str=None, page=1, page_size=20, search=None,
            sort_by="messageToDayCount", sort_order="DESC"):
    """Get paginated group list with analysis data"""
    conn = get_connection()
    cur = conn.cursor()
    where = []
    params = []
    if date_str:
        where.append('DATE(a.CREATEDTIME) = %s')
        params.append(date_str)
    if search:
        where.append('a.groupName LIKE %s')
        params.append(f'%{search}%')
    ws = ("WHERE " + " AND ".join(where)) if where else ""
    allowed = {'messageToDayCount', 'saleAfterCount', 'id', 'CREATEDTIME', 'groupName'}
    if sort_by not in allowed:
        sort_by = 'messageToDayCount'
    if sort_order.upper() not in ('ASC', 'DESC'):
        sort_order = 'DESC'
    cur.execute(f'SELECT COUNT(*) as total FROM qx_analysis_result a {ws}', params)
    total = cur.fetchone()['total']
    offset = (page - 1) * page_size
    cur.execute(
        'SELECT a.id, a.groupName, a.member, a.messageToDayCount, '
        'a.saleAfterCount, a.isMissedMessage, a.customerEmotionAnalysis, '
        'a.saleEmotionAnalysis, a.highFrequencyWords, a.CREATEDTIME '
        'FROM qx_analysis_result a ' + ws + 'ORDER BY a.' + sort_by + ' ' + sort_order + ' LIMIT %s OFFSET %s',
        params + [page_size, offset])
    items = []
    for row in cur.fetchall():
        items.append({
            'id': row['id'],
            'group_name': row['groupName'],
            'member_count': len(row['member'].split(',')) if row['member'] else 0,
            'members': row['member'],
            'message_count': row['messageToDayCount'],
            'sale_after_count': row['saleAfterCount'],
            'has_missed': row['isMissedMessage'] == '1',
            'project_codes': get_project_codes(row['groupName'] or ''),
            'customer_emotion': parse_emotion_field(row['customerEmotionAnalysis']),
            'sale_emotion': parse_emotion_field(row['saleEmotionAnalysis']),
            'high_freq_words': parse_high_freq(row['highFrequencyWords'])[:5],
            'created_time': str(row['CREATEDTIME']) if row['CREATEDTIME'] else None,
        })
    return {
        'total': total, 'page': page, 'page_size': page_size,
        'total_pages': max(1, (total + page_size - 1) // page_size),
        'items': items,
    }

def get_group_detail(group_id: int) -> Optional[dict]:
    """Get full analysis detail for a single group"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('SELECT * FROM qx_analysis_result WHERE id = %s', (group_id,))
    row = cur.fetchone()
    if not row:
        return None
    missed_list = []
    if row['missedMessageList']:
        missed_list = [m.strip() for m in row['missedMessageList'].split(',') if m.strip()]
    return {
        'id': row['id'],
        'group_name': row['groupName'],
        'member': row['member'],
        'member_count': len(row['member'].split(',')) if row['member'] else 0,
        'message_count': row['messageToDayCount'],
        'sale_after_count': row['saleAfterCount'],
        'send_detail': parse_send_detail(row['sendMessageDetail']),
        'core_summary': row['coreInfoSummary'],
        'customer_emotion': parse_emotion_field(row['customerEmotionAnalysis']),
        'customer_negative_info': row['customerNegativeEmotionInfo'],
        'sale_emotion': parse_emotion_field(row['saleEmotionAnalysis']),
        'sale_negative_info': row['saleNegativeEmotionInfo'],
        'high_freq_words': parse_high_freq(row['highFrequencyWords']),
        'sensitive_words': row['sensitiveWords'],
        'has_missed': row['isMissedMessage'] == '1',
        'missed_list': missed_list,
        'project_codes': get_project_codes(row['groupName'] or ''),
        'created_time': str(row['CREATEDTIME']) if row['CREATEDTIME'] else None,
    }

def get_timeseries(days: int = 30) -> list:
    """Get time series data for charts"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT DATE(CREATEDTIME) as date,'
        'COUNT(*) as group_count,'
        'COALESCE(SUM(messageToDayCount), 0) as message_count,'
        'COALESCE(SUM(saleAfterCount), 0) as sale_after_count,'
        'SUM(CASE WHEN isMissedMessage = ' + chr(34) + '1' + chr(34) + ' THEN 1 ELSE 0 END) as missed_count '
        'FROM qx_analysis_result '
        ' WHERE CREATEDTIME >= DATE_SUB(NOW(), INTERVAL %s DAY) '
        'GROUP BY DATE(CREATEDTIME) ORDER BY DATE(CREATEDTIME)',
        (days,))
    result = []
    for row in cur.fetchall():
        result.append({
            'date': str(row['date']),
            'group_count': row['group_count'],
            'message_count': row['message_count'],
            'sale_after_count': row['sale_after_count'],
            'missed_count': row['missed_count'],
        })
    return result

def get_sentiment_timeline(days: int = 30) -> list:
    """Get sentiment trend over time"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT DATE(CREATEDTIME) as date, customerEmotionAnalysis, saleEmotionAnalysis '
        'FROM qx_analysis_result '
        ' WHERE CREATEDTIME >= DATE_SUB(NOW(), INTERVAL %s DAY) ORDER BY CREATEDTIME',
        (days,))
    daily = {}
    for row in cur.fetchall():
        d = str(row['date'])
        if d not in daily:
            daily[d] = {'cust_good': 0, 'cust_bad': 0, 'sale_pos': 0, 'sale_neg': 0}
        ce = parse_emotion_field(row['customerEmotionAnalysis'])
        daily[d]['cust_good'] += ce.get(chr(22994)+chr(35780), 0)
        daily[d]['cust_bad'] += ce.get(chr(24046)+chr(35780), 0)
        se = parse_emotion_field(row['saleEmotionAnalysis'])
        daily[d]['sale_pos'] += se.get(chr(31215)+chr(26497)+chr(30340), 0)
        daily[d]['sale_neg'] += se.get(chr(24577)+chr(24230)+chr(24694)+chr(21133)+chr(30340), 0)
    result = [{'date': d, **daily[d]} for d in sorted(daily.keys())]
    return result