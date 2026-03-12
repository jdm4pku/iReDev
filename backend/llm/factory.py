from typing import Dict, Any, Optional
from pathlib import Path
import yaml

from .base import BaseLLM
from .openai_llm import OpenAILLM
from .claude_llm import ClaudeLLM
# from .huggingface_llm import HuggingFaceLLM
from .gemini_llm import GeminiLLM

class LLMFactory:
    """Factory class for creating LLM instances."""
    
    @staticmethod
    def create_llm(config: Dict[str, Any], config_path: Optional[str] = None) -> BaseLLM:
        """Create an LLM instance based on configuration.
        
        Args:
            config: Configuration dictionary containing LLM settings
            config_path: Path to the configuration file (used when loading global config for rate_limits). If None, uses default path.
            
        Returns:
            An instance of BaseLLM
            
        Raises:
            ValueError: If the LLM type is not supported
        """
        llm_type = config["type"].lower()
        model = config.get("model")
        
        if not model:
            raise ValueError("Model must be specified in the config file")
        
        # Extract rate limit settings from config
        # First check if there are specific rate limits in the LLM config
        rate_limits = config.get("rate_limits", {})
        
        # If not, check if there are global rate limits for this provider type
        global_config = LLMFactory.load_config(config_path)
        if not rate_limits and "rate_limits" in global_config:
            # Map LLM types to provider names in rate_limits section
            provider_map = {
                "openai": "openai",
                "claude": "claude",
                "gemini": "gemini"
            }
            provider_key = provider_map.get(llm_type, llm_type)
            provider_limits = global_config.get("rate_limits", {}).get(provider_key, {})
            if provider_limits:
                rate_limits = provider_limits
        
        if llm_type == "openai":
            return OpenAILLM(
                api_key=config["api_key"],  
                model=model,
                base_url=config.get("base_url"),
                rate_limits=rate_limits
            )
        elif llm_type == "claude":
            return ClaudeLLM(
                api_key=config["api_key"],
                model=model,
                rate_limits=rate_limits
            )
        elif llm_type == "gemini":
            return GeminiLLM(
                api_key=config["api_key"],
                model=model,
                rate_limits=rate_limits
            )
        # elif llm_type == "huggingface":
        #     return HuggingFaceLLM(
        #         model_name=model,
        #         device=config.get("device", "cuda"),
        #         torch_dtype=config.get("torch_dtype", "float16")
        #     )
        else:
            raise ValueError(f"Unsupported LLM type: {llm_type}")
    
    @staticmethod
    def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
        """Load LLM configuration from file.
        
        Args:
            config_path: Path to the configuration file. If None, uses default path.
            
        Returns:
            Configuration dictionary
            
        Raises:
            FileNotFoundError: If the configuration file doesn't exist
        """
        if config_path is None:
            config_path = str(Path(__file__).parent.parent / "config" / "config.yaml")
        
        if not Path(config_path).exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        return config 