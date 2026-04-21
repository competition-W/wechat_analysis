from typing import List, Dict, Set
from collections import defaultdict
from loguru import logger

try:
    import ahocorasick
    HAS_AHOCORASICK = True
except ImportError:
    HAS_AHOCORASICK = False
    logger.warning("pyahocorasick未安装，使用正则表达式匹配敏感词")

from config.settings import settings
from services.preprocessor import NormalizedMessage


class SensitiveWordDetector:
    def __init__(self):
        self.sensitive_words = set(settings.sensitive_words_list)
        self.automaton = None
        
        if HAS_AHOCORASICK and self.sensitive_words:
            self._build_automaton()
    
    def _build_automaton(self):
        self.automaton = ahocorasick.Automaton()
        for word in self.sensitive_words:
            self.automaton.add_word(word, word)
        self.automaton.make_automaton()
    
    def detect(self, messages: List[NormalizedMessage]) -> dict:
        word_hits = defaultdict(list)
        total_hits = 0
        
        for msg in messages:
            if not msg.text_content or msg.msgtype != "text":
                continue
            
            found_words = self._find_words(msg.text_content)
            
            for word in found_words:
                word_hits[word].append({
                    "sender_name": msg.sender_name,
                    "sender_job": msg.sender_job,
                    "sender_position": msg.sender_position,
                    "content": msg.text_content[:200] if msg.text_content else "",
                    "msgtime": msg.msgtime,
                })
                total_hits += 1
        
        words_list = []
        for word, hits in word_hits.items():
            words_list.append({
                "word": word,
                "count": len(hits),
                "hits": hits[:10],
            })
        
        words_list.sort(key=lambda x: x["count"], reverse=True)
        
        return {
            "total_hits": total_hits,
            "words": words_list,
        }
    
    def _find_words(self, text: str) -> Set[str]:
        if not text:
            return set()
        
        found = set()
        
        if HAS_AHOCORASICK and self.automaton:
            for _, word in self.automaton.iter(text):
                found.add(word)
        else:
            for word in self.sensitive_words:
                if word in text:
                    found.add(word)
        
        return found
