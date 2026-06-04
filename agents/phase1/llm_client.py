"""
LLM Client for Phase 1 agents — uses Claude via AWS Bedrock.
Drop-in replacement for Gemini SDK.
"""

import os
import json
import boto3
from botocore.config import Config


class ClaudeClient:
    """Claude client matching Gemini SDK interface for Phase 1 agents."""

    def __init__(self):
        self.bedrock = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_BEDROCK_REGION", "us-east-1"),
            config=Config(read_timeout=60, connect_timeout=10, retries={"max_attempts": 2}),
        )
        self.model_id = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

    def generate_content(self, prompt: str, system_instruction: str = None) -> str:
        """
        Generate content using Claude.

        Args:
            prompt: User prompt
            system_instruction: System prompt

        Returns:
            Generated text
        """
        messages = [{"role": "user", "content": [{"text": prompt}]}]

        kwargs = {
            "modelId": self.model_id,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": 1024,
                "temperature": 0.7,
            },
        }

        if system_instruction:
            kwargs["system"] = [{"text": system_instruction}]

        try:
            response = self.bedrock.converse(**kwargs)
            return response["output"]["message"]["content"][0]["text"]
        except Exception as e:
            raise Exception(f"Claude API error: {str(e)}")


# Global client instance
_client = None


def get_client():
    """Get or create global Claude client."""
    global _client
    if _client is None:
        _client = ClaudeClient()
    return _client
