from __future__ import annotations
import json
import re
import logging
from typing import Dict, Any, Optional, AsyncGenerator, Tuple

from ..models.topology import TopologyGraph
from ..models.canonical import CanonicalMetricSnapshot
from ..models.diagnosis import DiagnosisReport, FailureMode, SuggestedRemediation
from .llm_provider import BaseLLMProvider, get_llm_provider

logger = logging.getLogger("rootcause.diagnosis")

SYSTEM_PROMPT = """You are RootCause AI, an autonomous Principal Site Reliability Engineer (SRE) specializing in distributed systems failure modes under load.

Your job is to analyze real-time microservices telemetry alongside a dependency topology graph to diagnose:
1. The EXACT failure mode:
   - CASCADING_FAILURE: Upstream services degrade because a downstream dependency is slow, causing connection/thread pool exhaustion.
   - RETRY_STORM: A failing service triggers un-throttled retries from callers, multiplying load and creating a death spiral.
   - CONNECTION_POOL_EXHAUSTION: Thread or connection pool reaches capacity, causing queuing latency while CPU remains low.
   - TIMEOUT_MISCONFIGURATION: Caller timeout is shorter than callee latency, abandoning requests while callee is still working.
   - HEALTHY: No anomalous degradation detected.

2. The ROOT CAUSE SERVICE (the original point of failure).
3. The BLAST RADIUS (services impacted as a consequence).
4. Concrete ARCHITECTURAL REMEDIATION (e.g. Circuit breaker thresholds, timeout alignment, pool sizing, exponential backoff).

RESPONSE FORMAT:
First, stream your step-by-step reasoning tracing the failure through the graph.
Then, conclude your response with a JSON code block matching this EXACT schema:

```json
{
  "failure_mode": "CASCADING_FAILURE",
  "root_cause_service": "string",
  "blast_radius": ["service1", "service2"],
  "confidence_score": 0.95,
  "summary": "Plain-language summary of the incident",
  "evidence": ["Data point 1", "Data point 2"],
  "suggested_fix": {
    "action": "Short title of action",
    "target_service": "service_needing_fix",
    "recommendation": "Precise technical instruction"
  }
}
```
"""


class DiagnosisEngine:
    """
    Core reasoning pipeline that translates topology and live telemetry into
    real-time thought streams and structured architectural diagnoses.
    """

    def __init__(self, llm_provider: Optional[BaseLLMProvider] = None):
        self.llm = llm_provider or get_llm_provider()

    def _build_diagnostic_brief(
        self,
        topology: TopologyGraph,
        snapshots: Dict[str, CanonicalMetricSnapshot],
        chaos_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Constructs an information-dense diagnostic brief for the LLM."""
        brief_parts = []

        # 1. Topology section
        brief_parts.append("=== TOPOLOGY DEPENDENCY GRAPH ===")
        for node in topology.nodes:
            downstreams = topology.get_downstream_ids(node.id)
            upstreams = topology.get_upstream_ids(node.id)
            role_tag = f"[{node.role.value.upper()}]"
            brief_parts.append(
                f"- {node.id} {role_tag}:"
                f"\n    Calls Downstream: {downstreams if downstreams else 'None (Leaf)'}"
                f"\n    Called By Upstream: {upstreams if upstreams else 'None (Ingress)'}"
            )

        # 2. Chaos experiment context if active
        if chaos_context:
            brief_parts.append("\n=== ACTIVE CHAOS EXPERIMENT ===")
            brief_parts.append(json.dumps(chaos_context, indent=2))

        # 3. Telemetry observations
        brief_parts.append("\n=== CURRENT TELEMETRY SNAPSHOTS ===")
        for svc_id, snapshot in snapshots.items():
            pool_info = (
                f"{snapshot.pool_active}/{snapshot.pool_max} ({int(snapshot.pool_saturation * 100)}%)"
                if snapshot.pool_max
                else "Uninstrumented"
            )
            
            alerts = []
            if snapshot.latency_p99_ms > 500:
                alerts.append(f"HIGH_LATENCY ({snapshot.latency_p99_ms:.1f}ms)")
            if snapshot.error_rate > 0.05:
                alerts.append(f"ELEVATED_ERRORS ({int(snapshot.error_rate * 100)}%)")
            if snapshot.pool_saturation > 0.8:
                alerts.append(f"POOL_SATURATION ({int(snapshot.pool_saturation * 100)}%)")
            if snapshot.retries_per_sec > 10:
                alerts.append(f"RETRY_SPIKE ({snapshot.retries_per_sec:.1f}/sec)")

            alert_str = f" ALERTS: {', '.join(alerts)}" if alerts else "  [Normal Range]"

            ds_calls_str = ""
            if snapshot.downstream_calls:
                ds_lines = [
                    f"      -> {call.target}: latency={call.latency_ms:.1f}ms, errors={call.errors}"
                    for call in snapshot.downstream_calls
                ]
                ds_calls_str = "\n    Downstream Timing:\n" + "\n".join(ds_lines)

            brief_parts.append(
                f"Service '{svc_id}':\n"
                f"    Throughput: {snapshot.throughput_rps:.1f} RPS | Latency p50: {snapshot.latency_p50_ms:.1f}ms | Latency p99: {snapshot.latency_p99_ms:.1f}ms\n"
                f"    Error Rate: {snapshot.error_rate * 100:.1f}% | Retries: {snapshot.retries_per_sec:.1f}/sec\n"
                f"    Connection/Thread Pool: {pool_info}\n"
                f"{alert_str}"
                f"{ds_calls_str}"
            )

        brief_parts.append("\nAnalyze the call chains, pinpoint the root cause service and failure mode, and output the final JSON.")
        return "\n".join(brief_parts)

    @staticmethod
    def _extract_diagnosis_json(full_text: str) -> Optional[DiagnosisReport]:
        """Finds and parses the embedded ```json ... ``` block or raw JSON in the LLM response."""
        # Find json markdown block first
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", full_text)
        candidate = match.group(1).strip() if match else full_text

        # Find outermost { ... }
        start_idx = candidate.find("{")
        end_idx = candidate.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            raw_json = candidate[start_idx:end_idx + 1]
        else:
            return None

        try:
            data = json.loads(raw_json)
            # Normalize failure mode enum
            raw_mode = str(data.get("failure_mode", "UNKNOWN")).upper()
            try:
                data["failure_mode"] = FailureMode(raw_mode)
            except ValueError:
                data["failure_mode"] = FailureMode.UNKNOWN

            fix = data.get("suggested_fix", {})
            if isinstance(fix, dict):
                data["suggested_fix"] = SuggestedRemediation(
                    action=fix.get("action", "Architectural Review"),
                    target_service=fix.get("target_service", data.get("root_cause_service", "system")),
                    recommendation=fix.get("recommendation", "Review service resilience.")
                )
            
            report = DiagnosisReport(**data)
            report.raw_thought_stream = full_text
            return report
        except Exception as e:
            logger.error(f"Failed to parse LLM diagnosis JSON: {e}")
            return None

    async def stream_diagnosis(
        self,
        topology: TopologyGraph,
        snapshots: Dict[str, CanonicalMetricSnapshot],
        chaos_context: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[str, None]:
        """Streams reasoning tokens in real-time as the agent analyzes the data."""
        brief = self._build_diagnostic_brief(topology, snapshots, chaos_context)
        async for chunk in self.llm.stream_reasoning(SYSTEM_PROMPT, brief):
            yield chunk

    async def diagnose(
        self,
        topology: TopologyGraph,
        snapshots: Dict[str, CanonicalMetricSnapshot],
        chaos_context: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Optional[DiagnosisReport]]:
        """
        Executes a complete diagnosis step.
        Returns: (full_reasoning_text, parsed_structured_report)
        """
        brief = self._build_diagnostic_brief(topology, snapshots, chaos_context)
        full_text = await self.llm.generate_response(SYSTEM_PROMPT, brief)
        report = self._extract_diagnosis_json(full_text)
        return full_text, report
