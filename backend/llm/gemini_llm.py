from typing import List, Dict, Any, Optional
import tiktoken
import google.generativeai as genai
from .base import BaseLLM
from .rate_limiter import RateLimiter

class GeminiLLM(BaseLLM):
    """Google Gemini API wrapper."""
    
    def __init__(
        self,
        api_key: str,
        model: str,
        rate_limits: Optional[Dict[str, Any]] = None
    ):
        """Initialize Gemini LLM.
        
        Args:
            api_key: Google API key
            model: Model identifier (e.g., "gemini-1.5-flash", "gemini-1.5-pro")
            rate_limits: Optional dictionary with rate limit settings
        """
        genai.configure(api_key=api_key)
        self.model_name = model
        self.model = genai.GenerativeModel(model)
        
        try:
            # Initialize tokenizer for token counting
            # Gemini doesn't have a direct tokenizer in the public API
            # Using tiktoken cl100k_base as a reasonable approximation
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except:
            # Fallback to basic word counting if tokenizer fails
            self.tokenizer = None
        
        # Default rate limits for Gemini (adjust based on actual API limits)
        default_limits = {
            "requests_per_minute": 60,
            "input_tokens_per_minute": 100000,
            "output_tokens_per_minute": 50000,
            "input_token_price_per_million": 0.125,  # Approximate for gemini-1.5-flash
            "output_token_price_per_million": 0.375  # Approximate for gemini-1.5-flash
        }
        
        # Use provided rate limits or defaults
        limits = rate_limits or default_limits
        
        # Initialize rate limiter
        self.rate_limiter = RateLimiter(
            provider="Gemini",
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
            if self.tokenizer:
                return len(self.tokenizer.encode(text))
            else:
                # Fallback: rough estimate if tokenizer not available
                return len(text.split()) * 1.3
        except Exception as e:
            # Log the error but don't fail
            import logging
            logging.warning(f"Failed to count tokens for Gemini: {e}")
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
            
        # Add overhead for message formatting (estimated)
        total_tokens += 4 * len(messages)
        
        return total_tokens
    
    def _convert_messages_to_gemini_format(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Convert standard message format to Gemini-specific format.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content' keys
            
        Returns:
            List of Gemini-formatted messages
        """
        gemini_messages = []
        
        # Gemini uses "user" and "model" for roles
        role_mapping = {
            "user": "user",
            "assistant": "model",
            "system": "user"  # Gemini doesn't have a system role, handle specifically
        }
        
        # Check if first message is a system message
        if messages and messages[0].get("role") == "system":
            # For system message, we'll add it as a user message with a prefix
            system_content = messages[0].get("content", "")
            if system_content:
                # Add the rest of the messages
                for message in messages[1:]:
                    role = role_mapping.get(message.get("role", "user"), "user")
                    content = message.get("content", "")
                    gemini_messages.append({"role": role, "parts": content})
        else:
            # No system message, just convert roles
            for message in messages:
                role = role_mapping.get(message.get("role", "user"), "user")
                content = message.get("content", "")
                gemini_messages.append({"role": role, "parts": content})
        
        return gemini_messages
    
    def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None
    ) -> str:
        """Generate a response using Gemini API with rate limiting.
        
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
        
        # Wait if we're approaching rate limits
        self.rate_limiter.wait_if_needed(input_tokens, max_tokens if max_tokens else 1000)
        
        # Format messages for Gemini API
        gemini_messages = self._convert_messages_to_gemini_format(messages)
        
        # Check if we need to start a chat or just generate
        if len(gemini_messages) > 1:
            # Start a chat with history
            history = gemini_messages[:-1]  # All but the last message
            last_message = gemini_messages[-1]  # The last message to send
            
            chat = self.model.start_chat(
                history=history,
            )
            
            # Send the last message to get a response
            response = chat.send_message(last_message.get("parts", ""))
            result_text = response.text
        else:
            # Single message, use generate_content
            content = gemini_messages[0].get("parts", "") if gemini_messages else ""
        
            response = self.model.generate_content(
                content,
                generation_config={
                    "temperature": temperature,
                    "max_tokens": max_tokens if max_tokens else None
                }
            )
            
            result_text = response.text
        
        # Estimate output tokens (Gemini API doesn't provide usage stats)
        output_tokens = self._count_tokens(result_text)
        
        # Record the request
        self.rate_limiter.record_request(input_tokens, output_tokens)
        
        return result_text
    
    def format_message(self, role: str, content: str) -> Dict[str, str]:
        """Format message for standard API.
        
        Args:
            role: Message role (system, user, assistant)
            content: Message content
            
        Returns:
            Formatted message dictionary
        """
        # Standard format - conversion to Gemini format happens in generate method
        return {"role": role, "content": content}