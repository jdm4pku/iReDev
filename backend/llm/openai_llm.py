from typing import List, Dict, Any, Optional
import openai
import tiktoken
from .base import BaseLLM
from .rate_limiter import RateLimiter

class OpenAILLM(BaseLLM):
    """OpenAI API wrapper."""
    
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        rate_limits: Optional[Dict[str, Any]] = None
    ):
        """Initialize OpenAI LLM.
        
        Args:
            api_key: OpenAI API key
            base_url: Optional base URL for OpenAI-compatible APIs (e.g., Azure, ChatAnywhere)
            model: Model identifier (e.g., "gpt-4", "gpt-3.5-turbo")
            rate_limits: Optional dictionary with rate limit settings
        """
        self.client = openai.OpenAI(api_key=api_key,base_url=base_url) if base_url else openai.OpenAI(api_key=api_key)
        self.model = model
        
        try:
            # Initialize tokenizer for the model
            self.tokenizer = tiktoken.encoding_for_model(model)
        except:
            # Fallback to cl100k_base for new models
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        
        # Default rate limits for GPT-4o-mini
        default_limits = {
            "requests_per_minute": 500,
            "input_tokens_per_minute": 200000,
            "output_tokens_per_minute": 100000,
            "input_token_price_per_million": 0.15,
            "output_token_price_per_million": 0.60
        }
        
        # Use provided rate limits or defaults
        limits = rate_limits or default_limits
        
        # Initialize rate limiter
        self.rate_limiter = RateLimiter(
            provider="OpenAI",
            requests_per_minute=limits.get("requests_per_minute", default_limits["requests_per_minute"]),
            input_tokens_per_minute=limits.get("input_tokens_per_minute", default_limits["input_tokens_per_minute"]),
            output_tokens_per_minute=limits.get("output_tokens_per_minute", default_limits["output_tokens_per_minute"]),
            input_token_price_per_million=limits.get("input_token_price_per_million", default_limits["input_token_price_per_million"]),
            output_token_price_per_million=limits.get("output_token_price_per_million", default_limits["output_token_price_per_million"])
        )
    
    def _count_tokens(self, text: str) -> int:
        """Count tokens in a string using the model's tokenizer.
        
        Args:
            text: Text to count tokens for
            
        Returns:
            Token count
        """
        if not text:
            return 0
            
        try:
            return len(self.tokenizer.encode(text))
        except Exception as e:
            # Log the error but don't fail
            import logging
            logging.warning(f"Failed to count tokens with OpenAI tokenizer: {e}")
            # Fallback: rough estimate if tokenizer fails
            return len(text.split()) * 1.3
    
    def _count_messages_tokens(self, messages: List[Dict[str, str]]) -> int:
        """Count tokens in all messages.
        
        Args:
            messages: List of message dictionaries
            
        Returns:
            Total token count
        """
        if not messages:
            return 0
            
        total_tokens = 0
        
        # Count tokens in each message
        for message in messages:
            if "content" in message and message["content"]:
                total_tokens += self._count_tokens(message["content"])
            
        # Add overhead for message formatting (varies by model, but ~4 tokens per message)
        total_tokens += 4 * len(messages)
        
        # Add tokens for model overhead (varies by model)
        total_tokens += 3  # Every reply is primed with <|start|>assistant<|message|>
        
        return total_tokens
    
    def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None
    ) -> str:
        """Generate a response using OpenAI API with rate limiting.
        
        Args:
            messages: List of message dictionaries
            temperature: Sampling temperature
            max_output_tokens: Maximum tokens to generate
            
        Returns:
            Generated response text
        """
        max_tokens = max_output_tokens
        # Count input tokens
        input_tokens = self._count_messages_tokens(messages)
        
        # Wait if we're approaching rate limits (estimate output tokens as max_output_tokens)
        self.rate_limiter.wait_if_needed(input_tokens, max_tokens)
        
        # Make the API call
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens if max_tokens else None
        )
        
        result_text = response.choices[0].message.content or ""
        
        # Count output tokens and record request
        output_tokens = response.usage.completion_tokens if hasattr(response, 'usage') else self._count_tokens(result_text)
        input_tokens = response.usage.prompt_tokens if hasattr(response, 'usage') else input_tokens
        
        self.rate_limiter.record_request(input_tokens, output_tokens)
        
        return result_text
    
    def format_message(self, role: str, content: str) -> Dict[str, str]:
        """Format message for OpenAI API.
        
        Args:
            role: Message role (system, user, assistant)
            content: Message content
            
        Returns:
            Formatted message dictionary
        """
        # OpenAI uses standard role names
        return {"role": role, "content": content} 