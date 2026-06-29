from langchain_deepseek import ChatDeepSeek

from personal_assistant.api.schemas import LLMConfig
from personal_assistant.config import Settings


def build_llm(settings: Settings, config: LLMConfig | None = None) -> ChatDeepSeek:
    config = config or LLMConfig()
    return ChatDeepSeek(
        api_base=config.base_url or settings.llm_base_url,
        api_key=config.api_key or settings.llm_api_key,
        model=config.model or settings.llm_model,
        temperature=(
            settings.llm_temperature
            if config.temperature is None
            else config.temperature
        ),
    )
