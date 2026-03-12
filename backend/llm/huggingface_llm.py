# from typing import List, Dict, Any, Optional
# from openai import OpenAI
# import torch
# import tiktoken
# from .base import BaseLLM

# class HuggingFaceLLM(BaseLLM):
#     """HuggingFace model wrapper using vLLM's OpenAI-compatible API."""
    
#     def __init__(
#         self,
#         model_name: str,
#         api_base: str = "http://localhost:8000/v1",
#         api_key: str = "EMPTY",
#         device: str = None,  # Kept for backward compatibility
#         torch_dtype: torch.dtype = None,  # Kept for backward compatibility
#         max_input_tokens: int = 10000  # Maximum input tokens allowed
#     ):
#         """Initialize HuggingFace LLM via vLLM API.
        
#         Args:
#             model_name: Name of the model
#             api_base: Base URL for the vLLM API endpoint
#             api_key: API key (typically "EMPTY" for local vLLM deployments)
#             device: Ignored (handled by vLLM server)
#             torch_dtype: Ignored (handled by vLLM server)
#             max_input_tokens: Maximum number of input tokens allowed
#         """
#         self.model_name = model_name
#         self.client = OpenAI(
#             api_key=api_key,
#             base_url=api_base,
#         )
#         self.max_input_tokens = max_input_tokens
#         # Initialize tokenizer based on model
#         try:
#             self.tokenizer = tiktoken.encoding_for_model(model_name)
#         except KeyError:
#             # Fall back to cl100k_base for unknown models (used by GPT-4, GPT-3.5-turbo)
#             self.tokenizer = tiktoken.get_encoding("cl100k_base")
    
#     def _count_tokens(self, messages: List[Dict[str, str]]) -> int:
#         """Count the number of tokens in a list of messages.
        
#         Args:
#             messages: List of message dictionaries
            
#         Returns:
#             Total token count
#         """
#         token_count = 0
        
#         for message in messages:
#             # Count tokens in content
#             token_count += len(self.tokenizer.encode(message["content"]))
#             # Add overhead for message format (role, etc.)
#             token_count += 4  # Approximate tokens for message formatting
            
#         # Add tokens for the formatting between messages
#         token_count += 2  # Final assistant message tokens
        
#         return token_count
    
#     def _truncate_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
#         """Truncate messages to stay within the token limit.
        
#         Args:
#             messages: List of message dictionaries
            
#         Returns:
#             Truncated list of message dictionaries
#         """
#         if not messages:
#             return []
            
#         system_messages = [m for m in messages if m["role"].lower() == "system"]
#         non_system_messages = [m for m in messages if m["role"].lower() != "system"]
        
#         # Always keep system messages intact
#         result = system_messages.copy()
#         token_budget = self.max_input_tokens - self._count_tokens(result)
        
#         # Process non-system messages from newest to oldest
#         for message in reversed(non_system_messages):
#             message_tokens = self._count_tokens([message])
            
#             if message_tokens <= token_budget:
#                 # We can include the entire message
#                 result.insert(len(system_messages), message)
#                 token_budget -= message_tokens
#             elif message["role"].lower() == "user" and token_budget > 20:
#                 # For user messages, we can truncate content if needed
#                 # Keep enough tokens for comprehension (at least some portion)
#                 content = message["content"]
#                 # Estimate how much content to keep
#                 keep_ratio = token_budget / message_tokens
#                 # Truncate from beginning to keep most recent content
#                 if keep_ratio < 0.5:
#                     # If we need to cut more than half, add indicator of truncation
#                     truncated_content = f"[...truncated...] {content[int(len(content) * (1 - keep_ratio + 0.1)):].strip()}"
#                 else:
#                     truncated_content = content[int(len(content) * (1 - keep_ratio)):].strip()
                
#                 truncated_message = {
#                     "role": message["role"],
#                     "content": truncated_content
#                 }
                
#                 # Verify the truncated message fits
#                 truncated_tokens = self._count_tokens([truncated_message])
#                 if truncated_tokens <= token_budget:
#                     result.insert(len(system_messages), truncated_message)
#                     token_budget -= truncated_tokens
            
#             # If we can't fit any more messages, stop
#             if token_budget <= 20:  # Keep some buffer
#                 break
                
#         # Ensure the messages are in the correct order (system first, then chronological)
#         result.sort(key=lambda m: 0 if m["role"].lower() == "system" else 1)
        
#         return result
    
#     def generate(
#         self,
#         messages: List[Dict[str, str]],
#         temperature: float = 0.7,
#         max_output_tokens: Optional[int] = None
#     ) -> str:
#         """Generate a response using the vLLM API.
        
#         Args:
#             messages: List of message dictionaries
#             temperature: Sampling temperature
#             max_output_tokens: Maximum tokens to generate
            
#         Returns:
#             Generated response text
#         """
#         max_output_tokens = max_output_tokens if max_output_tokens is not None else self.max_output_tokens
#         # Check token count and truncate if needed
#         total_tokens = self._count_tokens(messages)
#         if total_tokens > self.max_input_tokens:
#             messages = self._truncate_messages(messages)
            
#         # vLLM expects strictly alternating user/assistant roles with an optional system message at the beginning
#         # Prepare the messages with the proper format
#         formatted_messages = []
        
#         # First, check for a system message to include at the beginning
#         system_messages = [m for m in messages if m["role"].lower() == "system"]
#         if system_messages:
#             # Use the last system message if multiple exist
#             formatted_messages.append({
#                 "role": "system",
#                 "content": system_messages[-1]["content"]
#             })
        
#         # Filter out system messages and process the rest
#         user_assistant_messages = [m for m in messages if m["role"].lower() != "system"]
        
#         # Ensure messages alternate between user and assistant
#         current_role = "user"  # Start with user message
        
#         for message in user_assistant_messages:
#             role = message["role"].lower()
            
#             # Map roles to either user or assistant
#             if role in ["user", "human"]:
#                 mapped_role = "user"
#             else:
#                 mapped_role = "assistant"
            
#             # If this message would create consecutive messages with the same role,
#             # skip adding it to avoid the alternating pattern error
#             if formatted_messages and mapped_role == formatted_messages[-1]["role"]:
#                 continue
            
#             # Add the properly mapped message
#             formatted_messages.append({
#                 "role": mapped_role,
#                 "content": message["content"]
#             })
        
#         # Make sure the last message is from the user, so the model will respond as assistant
#         if not formatted_messages or formatted_messages[-1]["role"] != "user":
#             # If we don't have any messages or the last one isn't from user, we need to add a user message
#             # Use an empty message or the last assistant message as context
#             formatted_messages.append({
#                 "role": "user",
#                 "content": "Please continue." if not formatted_messages else 
#                            f"Based on your last response: '{formatted_messages[-1]['content']}', please continue."
#             })
        
#         # Call the API
#         response = self.client.chat.completions.create(
#             model=self.model_name,
#             messages=formatted_messages,
#             temperature=temperature,
#             max_tokens=max_output_tokens
#         )
        
#         # Extract the generated text
#         return response.choices[0].message.content
    
#     def format_message(self, role: str, content: str) -> Dict[str, str]:
#         """Format message for OpenAI API compatible format.
        
#         Args:
#             role: Message role (system, user, assistant)
#             content: Message content
            
#         Returns:
#             Formatted message dictionary
#         """
#         # Map to standard OpenAI roles if needed
#         if role.lower() not in ["system", "user", "assistant"]:
#             if role.lower() in ["human"]:
#                 role = "user"
#             elif role.lower() in ["ai", "assistant"]:
#                 role = "assistant"
#             else:
#                 # Default unexpected roles to user
#                 role = "user"
                
#         return {"role": role, "content": content}
    
#     def _messages_to_prompt(self, messages: List[Dict[str, str]]) -> str:
#         """Convert messages to a single prompt string.
        
#         This method is kept for backward compatibility but is not used
#         in the API-based implementation.
        
#         Args:
#             messages: List of message dictionaries
            
#         Returns:
#             Formatted prompt string
#         """
#         prompt_parts = []
        
#         for message in messages:
#             role = message["role"]
#             content = message["content"]
            
#             if role == "system":
#                 prompt_parts.append(f"System: {content}")
#             elif role == "user":
#                 prompt_parts.append(f"Human: {content}")
#             elif role == "assistant":
#                 prompt_parts.append(f"Assistant: {content}")
        
#         prompt_parts.append("Assistant: ")  # Add final prompt for generation
#         return "\n".join(prompt_parts) 