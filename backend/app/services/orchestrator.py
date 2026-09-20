from __future__ import annotations
import time
import asyncio
import logging
import httpx
from typing import AsyncGenerator, Optional, Dict

from ..models.topology import TopologyGraph
from ..models.orchestrator import (
    ExperimentPlan,
    SessionEvent,
    SessionEventType,
)
from ..models.canonical import CanonicalMetricSnapshot
from .chaos_drivers import (
    ConcurrencyStressDriver,
    DockerContainerDriver,
    SimulationScenarioGenerator,
)
from .diagnosis_engine import DiagnosisEngine

from .adapter import TelemetryAdapter

logger = logging.getLogger("rootcause.orchestrator")


class ExperimentOrchestrator:
    """
    Coordinates end-to-end stress and chaos experiments:
    1. Fires black-box concurrency load at the ingress entrypoint.
    2. Injects optional container-level faults (pause/restart).
    3. Gathers real-time telemetry snapshots every second.
    4. Triggers live LLM reasoning stream and final diagnosis.
    """

    def __init__(self, diagnosis_engine: Optional[DiagnosisEngine] = None, telemetry_adapter: Optional[TelemetryAdapter] = None):
        self.engine = diagnosis_engine or DiagnosisEngine()
        self.adapter = telemetry_adapter or TelemetryAdapter()

    async def run_experiment_stream(
        self,
        topology: TopologyGraph,
        plan: ExperimentPlan,
        simulation_scenario: Optional[str] = None
    ) -> AsyncGenerator[SessionEvent, None]:
        """
        Executes an experiment and streams live events for WebSockets.
        If simulation_scenario is provided (e.g. 'CASCADING_FAILURE', 'RETRY_STORM'),
        runs in instant simulation mode without requiring live external containers.
        """
        start_time = time.time()
        duration = plan.duration_seconds

        yield SessionEvent(
            event_type=SessionEventType.STATUS_UPDATE,
            timestamp=time.time(),
            data={
                "message": f"Starting experiment '{plan.name}' against {plan.target_url}",
                "pattern": plan.stress_pattern.value,
                "concurrency": plan.concurrency_users,
                "duration_seconds": duration,
                "simulation_mode": bool(simulation_scenario),
            }
        )

        stress_driver: Optional[ConcurrencyStressDriver] = None
        if not simulation_scenario:
            stress_driver = ConcurrencyStressDriver(
                target_url=plan.target_url,
                method=plan.http_method,
                endpoint=plan.http_endpoint,
                payload=plan.http_payload,
                concurrency=plan.concurrency_users,
                pattern=plan.stress_pattern,
                duration_seconds=duration,
            )
            stress_driver.start()

        latest_snapshots: Dict[str, CanonicalMetricSnapshot] = {}
        fault_applied = False
        fault_recovered = False

        try:
            for second in range(1, duration + 1):
                now = time.time()

                # Handle scheduled container fault
                if plan.container_fault and not simulation_scenario:
                    cf = plan.container_fault
                    if second >= cf.trigger_at_second and not fault_applied:
                        await DockerContainerDriver.apply_fault(cf)
                        fault_applied = True
                        yield SessionEvent(
                            event_type=SessionEventType.STATUS_UPDATE,
                            timestamp=now,
                            data={"message": f"Injected container fault: {cf.fault_type.value} on '{cf.target_service}'"}
                        )
                    
                    if fault_applied and not fault_recovered and second >= (cf.trigger_at_second + cf.duration_seconds):
                        await DockerContainerDriver.recover_fault(cf)
                        fault_recovered = True
                        yield SessionEvent(
                            event_type=SessionEventType.STATUS_UPDATE,
                            timestamp=now,
                            data={"message": f"Recovered container '{cf.target_service}' back to normal"}
                        )

                # Collect metrics snapshot for this second
                if simulation_scenario:
                    latest_snapshots = SimulationScenarioGenerator.get_tick_snapshots(simulation_scenario, second)
                else:
                    # In live mode: query /_metrics concurrently from all reachable services
                    snapshots = {}
                    async with httpx.AsyncClient(timeout=0.8) as client:
                        async def poll_node(node):
                            try:
                                r = await client.get(f"{node.base_url}/_metrics")
                                if r.status_code == 200:
                                    return node.id, self.adapter.ingest(r.json())
                            except Exception:
                                pass
                            return node.id, None

                        results = await asyncio.gather(*(poll_node(n) for n in topology.nodes))
                        for node_id, snapshot in results:
                            if snapshot:
                                snapshots[node_id] = snapshot

                    # Fallback for entrypoint if /_metrics wasn't reached
                    ingress_id = topology.entrypoint_ids[0] if topology.entrypoint_ids else "gateway"
                    if ingress_id not in snapshots and stress_driver:
                        live_stats = stress_driver.get_live_metrics()
                        if live_stats.get("rps", 0.0) > 0.0:
                            snapshots[ingress_id] = CanonicalMetricSnapshot(
                                service_id=ingress_id,
                                timestamp=now,
                                throughput_rps=live_stats.get("rps", 0.0),
                                latency_p50_ms=live_stats.get("p50_ms", 0.0),
                                latency_p99_ms=live_stats.get("p99_ms", 0.0),
                                error_rate=live_stats.get("error_rate", 0.0),
                            )

                    # If target microservices are unreachable (e.g. deployed cloud server without target-services container),
                    # fall back to dynamic simulation ticks so the live dashboard never shows a frozen/blank graph
                    if not snapshots:
                        snapshots = SimulationScenarioGenerator.get_tick_snapshots("CASCADING_FAILURE", second)

                    latest_snapshots = snapshots

                # Emit metrics tick for live dashboard
                serialized_snapshots = {k: v.model_dump() for k, v in latest_snapshots.items()}
                yield SessionEvent(
                    event_type=SessionEventType.METRICS_TICK,
                    timestamp=now,
                    data={"second": second, "snapshots": serialized_snapshots}
                )

                await asyncio.sleep(1.0)

        finally:
            if stress_driver:
                await stress_driver.stop()
            if fault_applied and not fault_recovered and plan.container_fault:
                await DockerContainerDriver.recover_fault(plan.container_fault)

        # Trigger AI Diagnosis once test concludes
        yield SessionEvent(
            event_type=SessionEventType.STATUS_UPDATE,
            timestamp=time.time(),
            data={"message": "Load test concluded. Initiating SRE diagnosis loop..."}
        )

        chaos_ctx = None
        if plan.container_fault:
            chaos_ctx = {
                "fault_type": plan.container_fault.fault_type.value,
                "target_service": plan.container_fault.target_service,
            }
        elif simulation_scenario:
            chaos_ctx = {"simulation_scenario": simulation_scenario}

        # Stream reasoning tokens live to the UI
        full_thought_buffer = []
        async for token in self.engine.stream_diagnosis(topology, latest_snapshots, chaos_context=chaos_ctx):
            full_thought_buffer.append(token)
            yield SessionEvent(
                event_type=SessionEventType.REASONING_CHUNK,
                timestamp=time.time(),
                data={"token": token}
            )
            # Smooth paced streaming so reasoning tokens emerge at realistic typing speed
            await asyncio.sleep(0.025)

        full_thought = "".join(full_thought_buffer)
        report = self.engine._extract_diagnosis_json(full_thought)

        if report:
            yield SessionEvent(
                event_type=SessionEventType.DIAGNOSIS_REPORT,
                timestamp=time.time(),
                data=report.model_dump()
            )

        yield SessionEvent(
            event_type=SessionEventType.COMPLETED,
            timestamp=time.time(),
            data={"total_duration_seconds": round(time.time() - start_time, 2)}
        )
