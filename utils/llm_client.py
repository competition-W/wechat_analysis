from typing import Optional, Dict
from openai import (
    OpenAI,
    AsyncOpenAI,
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    APIStatusError,
    OpenAIError,
)
from loguru import logger

from config.settings import settings


class LLMClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._client = None
            cls._instance._async_client = None
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
    def async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL,
                timeout=settings.LLM_TIMEOUT,
            )
        return self._async_client

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

    def _record_usage(self, response):
        if hasattr(response, "usage") and response.usage:
            self._total_tokens += response.usage.total_tokens or 0
            self._total_prompt_tokens += response.usage.prompt_tokens or 0
            self._total_completion_tokens += response.usage.completion_tokens or 0

    def _log_llm_error(self, e: Exception, model: str, prompt: str, async_mode: bool = False):
        mode = "异步" if async_mode else "同步"

        if isinstance(e, RateLimitError):
            logger.error(
                f"LLM{mode}调用被限流: "
                f"type={type(e).__name__}, "
                f"status_code={getattr(e, 'status_code', None)}, "
                f"model={model}, "
                f"prompt_len={len(prompt)}, "
                f"timeout={settings.LLM_TIMEOUT}, "
                f"message={str(e)}"
            )

        elif isinstance(e, APITimeoutError):
            logger.error(
                f"LLM{mode}调用超时: "
                f"type={type(e).__name__}, "
                f"model={model}, "
                f"prompt_len={len(prompt)}, "
                f"timeout={settings.LLM_TIMEOUT}, "
                f"message={str(e)}"
            )

        elif isinstance(e, APIConnectionError):
            logger.error(
                f"LLM{mode}连接失败: "
                f"type={type(e).__name__}, "
                f"model={model}, "
                f"prompt_len={len(prompt)}, "
                f"timeout={settings.LLM_TIMEOUT}, "
                f"message={str(e)}"
            )

        elif isinstance(e, APIStatusError):
            logger.error(
                f"LLM{mode}接口状态异常: "
                f"type={type(e).__name__}, "
                f"status_code={getattr(e, 'status_code', None)}, "
                f"model={model}, "
                f"prompt_len={len(prompt)}, "
                f"timeout={settings.LLM_TIMEOUT}, "
                f"message={str(e)}"
            )

        elif isinstance(e, OpenAIError):
            logger.error(
                f"LLM{mode}OpenAI兼容接口异常: "
                f"type={type(e).__name__}, "
                f"model={model}, "
                f"prompt_len={len(prompt)}, "
                f"timeout={settings.LLM_TIMEOUT}, "
                f"message={str(e)}"
            )

        else:
            logger.exception(
                f"LLM{mode}未知异常: "
                f"type={type(e).__name__}, "
                f"model={model}, "
                f"prompt_len={len(prompt)}, "
                f"timeout={settings.LLM_TIMEOUT}"
            )

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

            self._record_usage(response)

            return response.choices[0].message.content or ""

        except Exception as e:
            self._log_llm_error(e, model=model, prompt=prompt, async_mode=False)
            raise

    async def async_chat(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        if model is None:
            model = settings.LLM_MODEL_SENTIMENT

        try:
            response = await self.async_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )

            self._record_usage(response)

            return response.choices[0].message.content or ""

        except Exception as e:
            self._log_llm_error(e, model=model, prompt=prompt, async_mode=True)
            raise