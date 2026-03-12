import time
from typing import Dict, List, Optional
from collections import deque
import threading
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("RateLimiter")

class RateLimiter:
    """
    Rate limiter for LLM API calls.
    Tracks requests, input tokens, and output tokens per minute.
    Also tracks cost based on token pricing.
    """
    
    def __init__(
        self,
        provider: str,
        requests_per_minute: int,
        input_tokens_per_minute: int,
        output_tokens_per_minute: int,
        input_token_price_per_million: float,
        output_token_price_per_million: float,
        buffer_percentage: float = 0.1  # Buffer to avoid hitting exact limits
    ):
        """
        Initialize the rate limiter.
        
        Args:
            provider: LLM provider name ("openai" or "claude")
            requests_per_minute: Maximum requests per minute
            input_tokens_per_minute: Maximum input tokens per minute
            output_tokens_per_minute: Maximum output tokens per minute
            input_token_price_per_million: Price per million input tokens
            output_token_price_per_million: Price per million output tokens
            buffer_percentage: Percentage buffer to avoid hitting exact limits
        """
        self.provider = provider
        self.requests_per_minute = requests_per_minute * (1 - buffer_percentage)
        self.input_tokens_per_minute = input_tokens_per_minute * (1 - buffer_percentage)
        self.output_tokens_per_minute = output_tokens_per_minute * (1 - buffer_percentage)
        
        # Pricing
        self.input_token_price = input_token_price_per_million / 1_000_000
        self.output_token_price = output_token_price_per_million / 1_000_000
        
        # Track usage within a sliding window (1 minute)
        self.request_timestamps = deque()
        self.input_token_usage = deque()  # Tuples of (timestamp, token_count)
        self.output_token_usage = deque()  # Tuples of (timestamp, token_count)
        
        # Total usage stats
        self.total_requests = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        
        # Thread lock for thread safety
        self.lock = threading.Lock()
    
    def _clean_old_entries(self, usage_queue: deque, current_time: float):
        """Remove entries older than 1 minute from the queue."""
        one_minute_ago = current_time - 60
        
        # Handle different queue formats (timestamps vs. (timestamp, value) tuples)
        if usage_queue and isinstance(usage_queue[0], tuple):
            # For token usage queues that store (timestamp, count) tuples
            while usage_queue and usage_queue[0][0] < one_minute_ago:
                usage_queue.popleft()
        else:
            # For request_timestamps queue that stores timestamp floats directly
            while usage_queue and usage_queue[0] < one_minute_ago:
                usage_queue.popleft()
    
    def _get_usage_count(self, usage_queue: deque):
        """Get the total count from a usage queue."""
        return sum(count for _, count in usage_queue)
    
    def wait_if_needed(self, input_tokens: int, estimated_output_tokens: Optional[int] = None):
        """
        Check if we're about to exceed rate limits and wait if necessary.
        This improved version uses a while loop instead of recursion to
        avoid potential infinite waiting scenarios.
        
        Args:
            input_tokens: Number of input tokens for the upcoming request
            estimated_output_tokens: Estimated number of output tokens
        """
        with self.lock:
            if estimated_output_tokens is None:
                estimated_output_tokens = input_tokens // 2  # Rough fallback estimate
            
            # If this single request is bigger than the entire capacity, warn or handle
            if input_tokens > self.input_tokens_per_minute or estimated_output_tokens > self.output_tokens_per_minute:
                logger.warning(
                    f"Request uses more tokens ({input_tokens} in / {estimated_output_tokens} out) "
                    f"than the configured per-minute capacity. This request may never succeed."
                )
            
            while True:
                current_time = time.time()
                
                # Clean up old entries
                self._clean_old_entries(self.request_timestamps, current_time)
                self._clean_old_entries(self.input_token_usage, current_time)
                self._clean_old_entries(self.output_token_usage, current_time)
                
                # Calculate current usage
                current_requests = len(self.request_timestamps)
                current_input_tokens = self._get_usage_count(self.input_token_usage)
                current_output_tokens = self._get_usage_count(self.output_token_usage)
                
                # Check if adding this request would exceed limits
                if ((current_requests + 1) <= self.requests_per_minute and
                    (current_input_tokens + input_tokens) <= self.input_tokens_per_minute and
                    (current_output_tokens + estimated_output_tokens) <= self.output_tokens_per_minute):
                    # We can proceed now
                    break
                
                # Otherwise, compute how long to wait
                wait_time = 0
                if self.request_timestamps:
                    wait_time = max(wait_time, 60 - (current_time - self.request_timestamps[0]))
                if self.input_token_usage:
                    wait_time = max(wait_time, 60 - (current_time - self.input_token_usage[0][0]))
                if self.output_token_usage:
                    wait_time = max(wait_time, 60 - (current_time - self.output_token_usage[0][0]))
                
                # If wait_time is still <= 0, we won't fix usage by waiting
                if wait_time <= 0:
                    logger.warning(
                        "Waiting cannot reduce usage enough to allow this request; "
                        "request exceeds per-minute capacity or usage remains too high."
                    )
                    break
                
                logger.info(f"Rate limit approaching for {self.provider}. Waiting {wait_time:.2f} seconds...")
                time.sleep(wait_time)
    
    def record_request(self, input_tokens: int, output_tokens: int):
        """
        Record an API request and its token usage.
        
        Args:
            input_tokens: Number of input tokens used
            output_tokens: Number of output tokens generated
        """
        with self.lock:
            current_time = time.time()
            
            # Record request and token usage
            self.request_timestamps.append(current_time)
            self.input_token_usage.append((current_time, input_tokens))
            self.output_token_usage.append((current_time, output_tokens))
            
            # Update total stats
            self.total_requests += 1
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            
            # Calculate cost
            input_cost = input_tokens * self.input_token_price
            output_cost = output_tokens * self.output_token_price
            total_cost = input_cost + output_cost
            self.total_cost += total_cost
            
            # Log usage and cost
            logger.info(
                f"{self.provider} Request: {self.total_requests} | "
                f"Tokens: {input_tokens}in/{output_tokens}out | "
                f"Cost: ${total_cost:.6f} | "
                f"Total Cost: ${self.total_cost:.6f}"
            )
    
    def print_usage_stats(self):
        """Print current usage statistics."""
        with self.lock:
            logger.info(f"{self.provider} Usage Statistics:")
            logger.info(f"  Total Requests: {self.total_requests}")
            logger.info(f"  Total Input Tokens: {self.total_input_tokens}")
            logger.info(f"  Total Output Tokens: {self.total_output_tokens}")
            logger.info(f"  Total Cost: ${self.total_cost:.6f}")