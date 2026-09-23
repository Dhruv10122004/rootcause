from __future__ import annotations
import os
import json
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("rootcause.llm")


class BaseLLMProvider(ABC):
    """Abstract base class for RootCause LLM providers."""

    @abstractmethod
    async def stream_reasoning(self, system_prompt: str, user_prompt: str) -> AsyncGenerator[str, None]:
        """Streams reasoning tokens one by one as they are generated."""
        pass

    @abstractmethod
    async def generate_response(self, system_prompt: str, user_prompt: str) -> str:
        """Returns the full completion text."""
        pass


class GroqProvider(BaseLLMProvider):
    """Groq Cloud implementation using ultra-fast LLM models."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY environment variable is required for GroqProvider")
        self.model = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        
        from groq import AsyncGroq
        self.client = AsyncGroq(api_key=self.api_key)

    async def stream_reasoning(self, system_prompt: str, user_prompt: str) -> AsyncGenerator[str, None]:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            stream=True,
            temperature=0.1,
            max_tokens=3000,
        )
        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content

    async def generate_response(self, system_prompt: str, user_prompt: str) -> str:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            max_tokens=3000,
        )
        return response.choices[0].message.content or ""


class BedrockProvider(BaseLLMProvider):

    def __init__(
        self,
        region_name: Optional[str] = None,
        model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0"
    ):
        self.region_name = region_name or os.getenv("AWS_REGION", "us-east-1")
        self.model_id = os.getenv("BEDROCK_MODEL_ID", model_id)
        import boto3
        self.client = boto3.client("bedrock-runtime", region_name=self.region_name)

    async def stream_reasoning(self, system_prompt: str, user_prompt: str) -> AsyncGenerator[str, None]:
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self.client.converse_stream(
                modelId=self.model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"temperature": 0.1, "maxTokens": 4096},
            )
        )
        stream = response.get("stream")
        if stream:
            for event in stream:
                if "contentBlockDelta" in event:
                    text = event["contentBlockDelta"]["delta"].get("text", "")
                    if text:
                        yield text

    async def generate_response(self, system_prompt: str, user_prompt: str) -> str:
        collected = []
        async for chunk in self.stream_reasoning(system_prompt, user_prompt):
            collected.append(chunk)
        return "".join(collected)


class MockLLMProvider(BaseLLMProvider):
    """
    High-fidelity local simulated provider for offline unit testing.
    Outputs realistic reasoning and valid structured diagnosis JSON.
    """

    async def stream_reasoning(self, system_prompt: str, user_prompt: str) -> AsyncGenerator[str, None]:
        # Determine scenario from diagnostic alerts and context in prompt
        is_pool = "POOL_SATURATION" in user_prompt or "100%" in user_prompt
        is_retry = ("RETRY_SPIKE" in user_prompt or "RETRY_STORM" in user_prompt) and not is_pool
        is_cascade = (
            is_pool or
            "CASCADING_FAILURE" in user_prompt or
            "HIGH_LATENCY" in user_prompt or
            "ELEVATED_ERRORS" in user_prompt or
            ("- db-service" in user_prompt) or
            ("- db " in user_prompt)
        ) and not is_retry

        if is_cascade:
            # Check for db-service in topology section
            if "- db-service" in user_prompt or "- db " in user_prompt:
                rc_svc = "db-service"
                rc_fix_svc = "db-service"
                blast = ["inventory", "payment", "orders", "gateway"]
                summary_text = "The database service experienced severe latency under concurrency load, exhausting caller connection pools and triggering cascading timeouts up to the ingress gateway."
                evidence_items = ['"db-service p99 latency exceeded SLA thresholds"', '"Connection pool saturation and upstream wait queues"']
            else:
                rc_svc = "inventory-service" if "inventory-service" in user_prompt else "inventory"
                rc_fix_svc = "orders-service" if "orders-service" in user_prompt else "orders"
                blast = ["orders-service", "api-gateway"] if "orders-service" in user_prompt else ["orders", "gateway"]
                summary_text = "Degraded latency in inventory-service filled caller connection pools, triggering cascading timeouts at the gateway."
                evidence_items = ['"orders-service connection pool at 100%"', '"api-gateway p99 jumped to 1450ms"']

            evidence_json = ", ".join(evidence_items)
            blast_json = json.dumps(blast)

            chunks = [
                "Scanning service vitals across the dependency tree...\n",
                f"Detected critical latency spike and connection pressure on {rc_svc}.\n",
                f"Trace analysis reveals upstream callers queued while waiting for {rc_svc} responses.\n",
                "Conclusion: Latency propagation exhausted caller connection pools, blocking ingress requests.\n\n",
                '```json\n{\n'
                '  "failure_mode": "CASCADING_FAILURE",\n'
                f'  "root_cause_service": "{rc_svc}",\n'
                f'  "blast_radius": {blast_json},\n'
                '  "confidence_score": 0.96,\n'
                f'  "summary": "{summary_text}",\n'
                f'  "evidence": [{evidence_json}],\n'
                '  "suggested_fix": {\n'
                '    "action": "Add Circuit Breaker & Connection Pool Expansion",\n'
                f'    "target_service": "{rc_fix_svc}",\n'
                '    "recommendation": "Configure circuit breaker thresholds with fast-fail fallback and expand connection pool capacity."\n'
                '  }\n'
                '}\n```'
            ]
        elif is_retry:
            target_svc = "orders-service" if "orders-service" in user_prompt else "orders"
            inv_svc = "inventory-service" if "inventory-service" in user_prompt else "inventory"
            chunks = [
                "Analyzing distributed call graph...\n",
                f"Observed critical retry inflation: {target_svc} retrying {inv_svc}.\n",
                "Because downstream returned timeout, caller retried without backoff.\n",
                f"Diagnosing: RETRY_STORM amplified load 3x on {inv_svc}.\n",
                "Generating root cause report...\n\n",
                '```json\n{\n'
                '  "failure_mode": "RETRY_STORM",\n'
                f'  "root_cause_service": "{inv_svc}",\n'
                f'  "blast_radius": ["{target_svc}", "gateway"],\n'
                '  "confidence_score": 0.94,\n'
                '  "summary": "Downstream timeouts induced un-throttled retry loops, creating a self-reinforcing retry storm.",\n'
                '  "evidence": ["Retries spiked under failure", "Downstream latency escalated exponentially"],\n'
                '  "suggested_fix": {\n'
                '    "action": "Implement Exponential Backoff with Jitter",\n'
                f'    "target_service": "{target_svc}",\n'
                f'    "recommendation": "Configure circuit breaker with exponential backoff and decorrelated jitter on calls from {target_svc} to {inv_svc}."\n'
                '  }\n'
                '}\n```'
            ]
        else:
            chunks = [
                "Telemetry within healthy bounds across all nodes.\n\n",
                '```json\n{\n'
                '  "failure_mode": "HEALTHY",\n'
                '  "root_cause_service": "none",\n'
                '  "blast_radius": [],\n'
                '  "confidence_score": 0.99,\n'
                '  "summary": "All services operating within normal latency and pool parameters.",\n'
                '  "evidence": ["p99 latency < 50ms", "pool saturation < 30%"],\n'
                '  "suggested_fix": {\n'
                '    "action": "Maintain Current Architecture",\n'
                '    "target_service": "all",\n'
                '    "recommendation": "No remediation required."\n'
                '  }\n'
                '}\n```'
            ]

        for chunk in chunks:
            yield chunk
            await asyncio.sleep(0.01)

    async def generate_response(self, system_prompt: str, user_prompt: str) -> str:
        collected = []
        async for chunk in self.stream_reasoning(system_prompt, user_prompt):
            collected.append(chunk)
        return "".join(collected)


def get_llm_provider(force_provider: Optional[str] = None) -> BaseLLMProvider:
    """
    Factory function resolving LLM provider according to environment settings.
    - If LLM_PROVIDER=bedrock: uses AWS Bedrock
    - If GROQ_API_KEY is available: uses Groq Cloud
    - Fallback: MockLLMProvider for offline reliability
    """
    provider_name = (force_provider or os.getenv("LLM_PROVIDER", "")).lower()

    if provider_name == "bedrock":
        logger.info("Initializing AWS Bedrock Provider")
        return BedrockProvider()
    elif provider_name == "mock":
        return MockLLMProvider()
    
    # Check if Groq key exists
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        logger.info("Initializing Groq Provider")
        return GroqProvider(api_key=groq_key)

    logger.warning("No GROQ_API_KEY or AWS Bedrock configured; falling back to MockLLMProvider")
    return MockLLMProvider()
