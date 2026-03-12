from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseLLM(ABC):
    """Base class for LLM wrappers."""
    
    @abstractmethod
    def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None
    ) -> str:
        """Generate a response from the LLM.
        
        Args:
            messages: List of message dictionaries with 'role' and 'content' keys
            temperature: Sampling temperature (0.0 to 1.0)
            max_output_tokens: Maximum number of tokens to generate
            
        Returns:
            The generated response text
        """
        pass
    
    @abstractmethod
    def format_message(self, role: str, content: str) -> Dict[str, str]:
        """Format a message for the specific LLM API.
        
        Args:
            role: The role of the message sender
            content: The content of the message
            
        Returns:
            Formatted message dictionary
        """
        pass 