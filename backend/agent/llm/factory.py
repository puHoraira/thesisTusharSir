"""
LLM Factory for creating provider-agnostic LLM instances.
Supports Google Gemini, OpenAI, and Local Qwen2.
"""

import logging
import os
from typing import Optional, Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from config import (
    LLM_PROVIDER,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    LLM_TEMPERATURE,
    LLM_MAX_TOKENS,
    LLM_TIMEOUT,
)

# Local model configuration
LOCAL_MODEL_PATH = os.getenv(
    "LOCAL_MODEL_PATH",
    "e:/hire/auto-main/drone_model/drone_model/drone-commander-qwen-lora-merged"
)

logger = logging.getLogger("llm_factory")


class LLMProviderUnavailableError(RuntimeError):
    """Raised when a configured LLM provider cannot be used."""

    def __init__(self, provider: str, message: str):
        super().__init__(message)
        self.provider = provider


class GeminiQuotaExceededError(LLMProviderUnavailableError):
    """Raised when Gemini rejects requests because the quota is exhausted."""


class GeminiAccessDeniedError(LLMProviderUnavailableError):
    """Raised when Gemini rejects requests because the project is denied access."""


class LLMFactory:
    """
    Factory class for creating LLM instances.
    
    Supports multiple providers (Gemini, OpenAI) with a common interface.
    The provider is selected based on the LLM_PROVIDER config setting.
    """
    
    @staticmethod
    def create_llm(
        provider: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> BaseChatModel:
        """
        Create an LLM instance based on the configured provider.
        
        Args:
            provider: Override the default provider ("gemini" or "openai")
            temperature: Override the default temperature
            max_tokens: Override the default max tokens
            timeout: Override the default timeout
            
        Returns:
            BaseChatModel: A LangChain chat model instance
            
        Raises:
            ValueError: If the provider is not supported or API key is missing
        """
        provider = provider or LLM_PROVIDER
        temperature = temperature if temperature is not None else LLM_TEMPERATURE
        max_tokens = max_tokens or LLM_MAX_TOKENS
        timeout = timeout or LLM_TIMEOUT
        
        logger.info(f"Creating LLM with provider: {provider}")
        
        if provider.lower() == "gemini":
            return LLMFactory._create_fallback_model("gemini", temperature, max_tokens, timeout)
        elif provider.lower() == "openai":
            return LLMFactory._create_fallback_model("openai", temperature, max_tokens, timeout)
        elif provider.lower() == "local" or provider.lower() == "qwen":
            return LLMFactory._create_local_qwen(temperature, max_tokens)
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}. Use 'gemini', 'openai', or 'local'.")
    
    @staticmethod
    def _create_gemini(
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> BaseChatModel:
        """Create a Google Gemini LLM instance."""
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set in environment variables")
        
        logger.info(f"Initializing Gemini model: {GEMINI_MODEL}")

        return ChatGoogleGenerativeAI(
            model=GEMINI_MODEL,
            api_key=GEMINI_API_KEY,
            temperature=temperature,
            max_output_tokens=max_tokens,
            timeout=timeout,
        )
    
    @staticmethod
    def _create_openai(
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> BaseChatModel:
        """Create an OpenAI LLM instance."""
        if not OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is not set in environment variables")
        
        logger.info(f"Initializing OpenAI model: {OPENAI_MODEL}")
        
        return ChatOpenAI(
            model=OPENAI_MODEL,
            api_key=OPENAI_API_KEY,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    
    @staticmethod
    def _create_local_qwen(
        temperature: float,
        max_tokens: int,
    ) -> BaseChatModel:
        """Create a local Qwen2 LLM instance."""
        from agent.llm.local_qwen import LocalQwenLLM
        
        logger.info(f"Initializing Local Qwen2 model from: {LOCAL_MODEL_PATH}")
        
        return LocalQwenLLM(
            model_path=LOCAL_MODEL_PATH,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    
    @staticmethod
    def get_available_providers() -> list[str]:
        """Return list of available providers based on configured API keys."""
        providers = []
        if GEMINI_API_KEY:
            providers.append("gemini")
        if OPENAI_API_KEY:
            providers.append("openai")
        # Local model always available if files exist
        if os.path.exists(LOCAL_MODEL_PATH):
            providers.append("local")
        return providers

    @staticmethod
    def _create_fallback_model(
        primary_provider: str,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> BaseChatModel:
        fallback_provider = LLMFactory._get_fallback_provider(primary_provider)
        return _FallbackChatModel(
            primary_provider=primary_provider,
            fallback_provider=fallback_provider,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    @staticmethod
    def _get_fallback_provider(primary_provider: str) -> Optional[str]:
        available = LLMFactory.get_available_providers()

        if primary_provider == "gemini" and "openai" in available:
            return "openai"

        if primary_provider == "openai" and "gemini" in available:
            return "gemini"

        return None

    @staticmethod
    def _is_provider_access_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return any(
            token in message
            for token in (
                "permission_denied",
                "denied access",
                "resource_exhausted",
                "quota",
                "rate limit",
                "429",
                "403",
            )
        )

    @staticmethod
    def _translate_provider_error(provider: str, exc: Exception) -> Exception:
        message = str(exc).lower()
        if provider == "gemini":
            if "permission_denied" in message or "403" in message or "denied access" in message:
                return GeminiAccessDeniedError(
                    provider,
                    "Gemini access was denied for this project. Switch LLM_PROVIDER or fix Gemini project access.",
                )
            if "resource_exhausted" in message or "quota" in message or "rate limit" in message or "429" in message:
                return GeminiQuotaExceededError(
                    provider,
                    "Gemini quota is exhausted. Wait for quota reset or switch LLM_PROVIDER to a working provider.",
                )

        return LLMProviderUnavailableError(
            provider,
            f"{provider.title()} could not be used: {exc}",
        )


class _FallbackChatModel:
    """Small adapter that retries on a secondary provider when the primary is unavailable."""

    def __init__(
        self,
        primary_provider: str,
        fallback_provider: Optional[str],
        temperature: float,
        max_tokens: int,
        timeout: float,
    ):
        self._primary_provider = primary_provider
        self._fallback_provider = fallback_provider
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._tools: Optional[Sequence[Any]] = None
        self._primary_model = None
        self._fallback_model = None

    def bind_tools(self, tools: Sequence[Any]):
        self._tools = tools
        self._primary_model = None
        self._fallback_model = None
        return self

    def invoke(self, *args, **kwargs):
        return self._invoke_with_fallback(False, *args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        return await self._invoke_with_fallback(True, *args, **kwargs)

    def __getattr__(self, name: str):
        # Expose model attributes expected by LangChain/LangGraph.
        model = self._get_model(self._primary_provider)
        return getattr(model, name)

    def _get_model(self, provider: str):
        if provider == self._primary_provider and self._primary_model is not None:
            return self._primary_model

        if provider == self._fallback_provider and self._fallback_model is not None:
            return self._fallback_model

        if provider == "gemini":
            model = LLMFactory._create_gemini(self._temperature, self._max_tokens, self._timeout)
        elif provider == "openai":
            model = LLMFactory._create_openai(self._temperature, self._max_tokens, self._timeout)
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")

        if self._tools is not None:
            model = model.bind_tools(self._tools)

        if provider == self._primary_provider:
            self._primary_model = model
        elif provider == self._fallback_provider:
            self._fallback_model = model

        return model

    def _invoke_with_fallback(self, async_mode: bool, *args, **kwargs):
        primary_model = self._get_model(self._primary_provider)

        try:
            if async_mode:
                return primary_model.ainvoke(*args, **kwargs)
            return primary_model.invoke(*args, **kwargs)
        except Exception as primary_exc:
            if not self._fallback_provider or not LLMFactory._is_provider_access_error(primary_exc):
                raise LLMFactory._translate_provider_error(self._primary_provider, primary_exc) from primary_exc

            logger.warning(
                "Primary LLM provider %s failed, falling back to %s: %s",
                self._primary_provider,
                self._fallback_provider,
                primary_exc,
            )

            fallback_model = self._get_model(self._fallback_provider)
            try:
                if async_mode:
                    return fallback_model.ainvoke(*args, **kwargs)
                return fallback_model.invoke(*args, **kwargs)
            except Exception as fallback_exc:
                raise LLMFactory._translate_provider_error(self._fallback_provider, fallback_exc) from fallback_exc
