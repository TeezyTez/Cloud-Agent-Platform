"""LLM integration layer."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: Dict[str, int]
    cost: float = 0.0
    latency_ms: float = 0.0
    finish_reason: str = "stop"
    raw_response: Optional[Dict[str, Any]] = None


@dataclass
class Message:
    role: str
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, messages: List[Message], model: Optional[str] = None,
                      temperature: float = 0.7, max_tokens: int = 4096,
                      tools: Optional[List[Dict[str, Any]]] = None) -> LLMResponse:
        pass
    
    @property
    @abstractmethod
    def supported_models(self) -> List[str]:
        pass


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: Optional[str] = None, 
                 default_model: str = "gpt-4o"):
        self.api_key = api_key
        self.base_url = base_url or "https://api.openai.com/v1"
        self.default_model = default_model
        self._client = None
    
    async def _get_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                timeout=120.0,
            )
        return self._client
    
    async def complete(self, messages: List[Message], model: Optional[str] = None,
                      temperature: float = 0.7, max_tokens: int = 4096,
                      tools: Optional[List[Dict[str, Any]]] = None) -> LLMResponse:
        import time
        client = await self._get_client()
        start_time = time.time()
        model = model or self.default_model
        
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        
        try:
            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            latency_ms = (time.time() - start_time) * 1000
            choice = data["choices"][0]
            message = choice["message"]
            
            return LLMResponse(
                content=message.get("content", ""),
                model=model,
                usage=data.get("usage", {}),
                cost=self._calculate_cost(model, data.get("usage", {})),
                latency_ms=latency_ms,
                finish_reason=choice.get("finish_reason", "stop"),
                raw_response=data,
            )
        except Exception as e:
            logger.error("OpenAI API error", error=str(e))
            raise
    
    def _calculate_cost(self, model: str, usage: Dict[str, int]) -> float:
        pricing = {"gpt-4o": {"prompt": 5.0, "completion": 15.0},
                   "gpt-4-turbo": {"prompt": 10.0, "completion": 30.0}}
        rates = pricing.get(model, {"prompt": 10.0, "completion": 30.0})
        return (usage.get("prompt_tokens", 0) / 1000000) * rates["prompt"] + (usage.get("completion_tokens", 0) / 1000000) * rates["completion"]
    
    @property
    def supported_models(self) -> List[str]:
        return ["gpt-4o", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"]


class LLMFactory:
    _providers = {"openai": OpenAIProvider}
    
    @classmethod
    def create(cls, provider: str, api_key: str, base_url: Optional[str] = None, default_model: Optional[str] = None) -> LLMProvider:
        provider_class = cls._providers.get(provider.lower())
        if not provider_class:
            raise ValueError(f"Unknown provider: {provider}")
        return provider_class(api_key=api_key, base_url=base_url, default_model=default_model)
