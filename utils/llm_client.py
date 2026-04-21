from typing import Optional, Dict, Any
from openai import OpenAI
from loguru import logger

from config.settings import settings


class LLMClient:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._client = None
            cls._instance._total_tokens = 0
            cls._instance._total_prompt_tokens = 0
            cls._instance._total_completion_tokens = 0
        return cls._instance
    
    @property
    def client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL,
                timeout=settings.LLM_TIMEOUT,
            )
        return self._client
    
    @property
    def token_stats(self) -> Dict[str, int]:
        return {
            "total_tokens": self._total_tokens,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
        }
    
    def reset_token_stats(self):
        self._total_tokens = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
    
    def chat(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        if model is None:
            model = settings.LLM_MODEL_SENTIMENT
        
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            if hasattr(response, 'usage') and response.usage:
                self._total_tokens += response.usage.total_tokens
                self._total_prompt_tokens += response.usage.prompt_tokens
                self._total_completion_tokens += response.usage.completion_tokens
            
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            raise
    
    async def async_chat(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        from openai import AsyncOpenAI
        
        if model is None:
            model = settings.LLM_MODEL_SENTIMENT
        
        async_client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            timeout=settings.LLM_TIMEOUT,
        )
        
        try:
            response = await async_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            if hasattr(response, 'usage') and response.usage:
                self._total_tokens += response.usage.total_tokens
                self._total_prompt_tokens += response.usage.prompt_tokens
                self._total_completion_tokens += response.usage.completion_tokens
            
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"LLM异步调用失败: {e}")
            raise
