from typing import List, Dict, Any, Optional
import anthropic
from .base import BaseLLM
from .rate_limiter import RateLimiter
import logging

class ClaudeLLM(BaseLLM):
    """Anthropic Claude API wrapper."""
    
    def __init__(
        self,
        api_key: str,
        model: str,
        rate_limits: Optional[Dict[str, Any]] = None
    ):
        """Initialize Claude LLM.
        
        Args:
            api_key: Anthropic API key
            model: Model identifier (e.g., "claude-3-sonnet-20240229")
            rate_limits: Optional dictionary with rate limit settings
        """
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        
        # Default rate limits for Claude 3.7 Sonnet
        default_limits = {
            "requests_per_minute": 50,
            "input_tokens_per_minute": 20000,
            "output_tokens_per_minute": 8000,
            "input_token_price_per_million": 3.0,
            "output_token_price_per_million": 15.0
        }
        
        # Use provided rate limits or defaults
        limits = rate_limits or default_limits
        
        # Initialize rate limiter
        self.rate_limiter = RateLimiter(
            provider="Claude",
            requests_per_minute=limits.get("requests_per_minute", default_limits["requests_per_minute"]),
            input_tokens_per_minute=limits.get("input_tokens_per_minute", default_limits["input_tokens_per_minute"]),
            output_tokens_per_minute=limits.get("output_tokens_per_minute", default_limits["output_tokens_per_minute"]),
            input_token_price_per_million=limits.get("input_token_price_per_million", default_limits["input_token_price_per_million"]),
            output_token_price_per_million=limits.get("output_token_price_per_million", default_limits["output_token_price_per_million"])
        )
    
    def _count_tokens(self, text: str) -> int:
        """Count tokens in a string using Claude's tokenizer.
        
        Args:
            text: Text to count tokens for
            
        Returns:
            Token count
        """
        if not text:
            return 0
            
        try:
            # Format text as a message for token counting
            count = self.client.beta.messages.count_tokens(
                model=self.model,
                messages=[
                    {"role": "user", "content": text}
                ]
            )
            return count.input_tokens
        except Exception as e:
            # Log the error but don't fail
            logging.warning(f"Failed to count tokens with Claude tokenizer: {e}")
            # Fallback: rough estimate if tokenizer fails
            return len(text.split()) * 1.3
    
    def _count_messages_tokens(self, messages: List[Dict[str, str]], system_message: Optional[str] = None) -> int:
        """Count tokens in message list with optional system message.
        
        Args:
            messages: List of message dictionaries
            system_message: Optional system message
            
        Returns:
            Total token count
        """
        if not messages:
            return 0
            
        # Convert messages to Claude format
        claude_messages = [self._convert_to_claude_message(msg) for msg in messages 
                          if msg["role"] != "system"]
        
        # Format system message if provided
        system_content = None
        if system_message:
            system_content = system_message
        
        try:
            # Use the API to count tokens for all messages at once
            count = self.client.beta.messages.count_tokens(
                model=self.model,
                messages=claude_messages,
                system=system_content
            )
            return count.input_tokens
        except Exception as e:
            # Log the error but don't fail
            logging.warning(f"Failed to count tokens with Claude tokenizer: {e}")
            
            # Fallback: count tokens individually
            total_tokens = 0
            for msg in claude_messages:
                if "content" in msg and msg["content"]:
                    total_tokens += self._count_tokens(msg["content"])
            
            # Add system message tokens if provided
            if system_message:
                total_tokens += self._count_tokens(system_message)
                
            # Add overhead for message formatting
            total_tokens += 10 * len(claude_messages)  # Add ~10 tokens per message for formatting
            
            return total_tokens
    
    def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None
    ) -> str:
        """Generate a response using Claude API with rate limiting.
        
        Args:
            messages: List of message dictionaries
            temperature: Sampling temperature
            max_output_tokens: Maximum tokens to generate
            
        Returns:
            Generated response text
        """
        max_tokens = max_output_tokens
        # Extract system message if present
        system_message = None
        chat_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                chat_messages.append(self._convert_to_claude_message(msg))
        
        # Count input tokens
        input_tokens = self._count_messages_tokens(messages, system_message)
        
        # Wait if we're approaching rate limits (estimate output tokens as max_output_tokens)
        self.rate_limiter.wait_if_needed(input_tokens, max_tokens)
        
        # Make the API call
        response = self.client.messages.create(
            model=self.model,
            messages=chat_messages,
            system=system_message,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        result_text = response.content[0].text
        
        # Count output tokens and record request
        output_tokens = self._count_tokens(result_text)
        self.rate_limiter.record_request(input_tokens, output_tokens)
        
        return result_text
    
    def format_message(self, role: str, content: str) -> Dict[str, str]:
        """Format message for Claude API.
        
        Args:
            role: Message role (system, user, assistant)
            content: Message content
            
        Returns:
            Formatted message dictionary
        """
        # Store in standard format, conversion happens in generate()
        return {"role": role, "content": content}
    
    def _convert_to_claude_message(self, message: Dict[str, str]) -> Dict[str, str]:
        """Convert standard message format to Claude's format.
        
        Args:
            message: Standard format message
            
        Returns:
            Claude format message
        """
        role_mapping = {
            "user": "user",
            "assistant": "assistant"
        }
        
        role = role_mapping[message["role"]]
        content = message["content"]
        
        return {"role": role, "content": content} 