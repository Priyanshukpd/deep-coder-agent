"""
LLM Provider Factory — Centralized logic for instantiating the correct LLM provider.
"""

from agent.config import AgentConfig
from agent.core.llm_provider import TogetherProvider
from agent.core.openai_provider import OpenAIProvider

def create_provider(config: AgentConfig):
    """
    Create an LLM provider instance based on the configuration.
    
    Args:
        config: The agent configuration.
        
    Returns:
        An instance of TogetherProvider or OpenAIProvider.
    """
    if config.provider == "openai":
        return OpenAIProvider(config)
    
    # Default to Together
    return TogetherProvider(config)
