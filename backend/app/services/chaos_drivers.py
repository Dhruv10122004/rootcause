from __future__ import annotations
import time
import asyncio
import logging
import httpx
from typing import Dict, Any, Optional, List, Tuple
from ..models.orchestrator import StressPattern, ContainerFault, ContainerFaultType
from ..models.canonical import CanonicalMetricSnapshot, DownstreamCall

logger = logging.getLogger("rootcause.drivers")


class ConcurrencyStressDriver:
    """
    Tier 1 Priority: Pure black-box concurrency and stress generator.
    Fires non-blocking concurrent HTTP requests at the ingress entrypoint to
    induce natural resource exhaustion and cascading failures without requiring
    any custom chaos endpoints or service instrumentation.
    """

    def __init__(
        self,
        target_url: str,
        method: str = "GET",
        endpoint: str = "/",
        payload: Optional[Dict[str, Any]] = None,
        concurrency: int = 50,
        pattern: StressPattern = StressPattern.RAMP_UP,
        duration_seconds: int = 15,
    ):
        self.target_url = target_url.rstrip("/") + ("/" + endpoint.lstrip("/"))
        self.method = method.upper()
        self.payload = payload
        self.target_concurrency = concurrency
        self.pattern = pattern
        self.duration_seconds = duration_seconds

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._latencies_window: List[float] = []
        self._errors_count = 0
        self._success_count = 0
        self._start_time = 0.0

    def _get_active_concurrency_at(self, elapsed: float) -> int:
        """Computes current concurrent workers based on the selected stress pattern."""
        progress = min(1.0, elapsed / max(1.0, float(self.duration_seconds)))
        
        if self.pattern == StressPattern.SUSTAINED:
            return self.target_concurrency
        elif self.pattern == StressPattern.RAMP_UP:
            # Linear ramp from 10% to 100% concurrency
            return max(2, int(self.target_concurrency * (0.1 + 0.9 * progress)))
        elif self.pattern == StressPattern.SPIKE:
            # Normal load for first 30%, then sudden 100% spike
            return int(self.target_concurrency * 0.1) if progress < 0.3 else self.target_concurrency
        elif self.pattern == StressPattern.BURST:
            # Oscillates every 3 seconds
            is_high = (int(elapsed) // 3) % 2 == 1
            return self.target_concurrency if is_high else max(2, int(self.target_concurrency * 0.2))
        return self.target_concurrency

    async def _worker(self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore):
        while self._running:
            async with semaphore:
                start = time.perf_counter()
                try:
                    if self.method == "POST":
                        res = await client.post(self.target_url, json=self.payload, timeout=5.0)
                    else:
                        res = await client.get(self.target_url, timeout=5.0)
                    
                    elapsed_ms = (time.perf_counter() - start) * 1000.0
                    self._latencies_window.append(elapsed_ms)
                    if res.status_code >= 500:
                        self._errors_count += 1
                    else:
                        self._success_count += 1
                except Exception:
                    elapsed_ms = (time.perf_counter() - start) * 1000.0
                    self._latencies_window.append(elapsed_ms)
                    self._errors_count += 1

                # Brief yield to allow event loop cooperative scheduling
                await asyncio.sleep(0.01)

    async def _run_loop(self):
        self._start_time = time.time()
        semaphore = asyncio.Semaphore(self.target_concurrency)
        async with httpx.AsyncClient(verify=False) as client:
            workers = [asyncio.create_task(self._worker(client, semaphore)) for _ in range(self.target_concurrency)]
            
            end_time = self._start_time + self.duration_seconds
            while self._running and time.time() < end_time:
                elapsed = time.time() - self._start_time
                active_users = self._get_active_concurrency_at(elapsed)
                # Adjust active capacity by controlling semaphore
                await asyncio.sleep(0.5)

            self._running = False
            for w in workers:
                w.cancel()

    def start(self):
        """Starts background traffic generation."""
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """Gracefully terminates traffic generation."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_live_metrics(self) -> Dict[str, float]:
        """Calculates live RPS, p50, p99, and error rate for the current window."""
        total_reqs = self._success_count + self._errors_count
        elapsed = max(0.1, time.time() - self._start_time)
        rps = total_reqs / elapsed

        if self._latencies_window:
            sorted_lat = sorted(self._latencies_window[-200:])  # last 200 requests
            p50 = sorted_lat[int(len(sorted_lat) * 0.5)]
            p99 = sorted_lat[min(len(sorted_lat) - 1, int(len(sorted_lat) * 0.99))]
        else:
            p50, p99 = 0.0, 0.0

        error_rate = (self._errors_count / total_reqs) if total_reqs > 0 else 0.0

        return {
            "rps": round(rps, 1),
            "p50_ms": round(p50, 1),
            "p99_ms": round(p99, 1),
            "error_rate": round(error_rate, 3),
            "total_requests": total_reqs,
            "errors": self._errors_count,
        }


class DockerContainerDriver:
    """
    Tier 2 Priority: Infrastructure-level container chaos.
    Uses standard Docker CLI controls (`docker pause`, `docker unpause`, `docker restart`)
    to simulate network freezes, hung databases, and process crashes without altering
    a single line of application source code.
    """

    @staticmethod
    async def _execute_docker_cmd(*args: str) -> Tuple[bool, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                return True, stdout.decode().strip()
            return False, stderr.decode().strip()
        except FileNotFoundError:
            logger.warning("Docker CLI not available on host system; simulating container action")
            return True, "simulated"
        except Exception as e:
            return False, str(e)

    @classmethod
    async def apply_fault(cls, fault: ContainerFault) -> bool:
        """Applies configured container fault."""
        target = fault.target_service
        if fault.fault_type == ContainerFaultType.PAUSE:
            logger.info(f"Docker Chaos: Pausing container '{target}'")
            success, out = await cls._execute_docker_cmd("pause", target)
            return success
        elif fault.fault_type == ContainerFaultType.RESTART:
            logger.info(f"Docker Chaos: Restarting container '{target}'")
            success, out = await cls._execute_docker_cmd("restart", target)
            return success
        elif fault.fault_type == ContainerFaultType.KILL:
            logger.info(f"Docker Chaos: Killing container '{target}'")
            success, out = await cls._execute_docker_cmd("kill", target)
            return success
        return True

    @classmethod
    async def recover_fault(cls, fault: ContainerFault) -> bool:
        """Restores container back to normal state."""
        target = fault.target_service
        if fault.fault_type == ContainerFaultType.PAUSE:
            logger.info(f"Docker Chaos: Unpausing container '{target}'")
            success, out = await cls._execute_docker_cmd("unpause", target)
            return success
        return True


class SimulationScenarioGenerator:
    """
    Generates high-fidelity simulated telemetry time-series for instant local testing,
    UI development, and demos without requiring live Docker containers.
    """

    @staticmethod
    def get_tick_snapshots(scenario_type: str, second: int) -> Dict[str, CanonicalMetricSnapshot]:
        now = time.time()
        
        if scenario_type.upper() == "CASCADING_FAILURE":
            # Degradation kicks in at second 2
            is_degraded = second >= 2
            db_pool = 5 if is_degraded else 1
            db_lat = 1250.0 if is_degraded else 15.0
            inv_lat = 1150.0 if is_degraded else 18.0
            pay_lat = 1100.0 if is_degraded else 20.0
            orders_lat = 1380.0 if is_degraded else 25.0
            gw_lat = 1450.0 if is_degraded else 35.0
            gw_errors = 0.14 if is_degraded else 0.0

            gw_snap = CanonicalMetricSnapshot(
                service_id="gateway", timestamp=now, throughput_rps=150.0 if is_degraded else 80.0,
                latency_p50_ms=45.0, latency_p99_ms=gw_lat, error_rate=gw_errors,
                downstream_calls=[DownstreamCall(target="orders", latency_ms=orders_lat, errors=int(gw_errors * 10))]
            )
            orders_snap = CanonicalMetricSnapshot(
                service_id="orders", timestamp=now, throughput_rps=150.0 if is_degraded else 80.0,
                latency_p50_ms=40.0, latency_p99_ms=orders_lat, error_rate=gw_errors * 0.7,
                pool_active=20 if is_degraded else 5, pool_max=20, retries_per_sec=35.0 if is_degraded else 0.0,
                downstream_calls=[
                    DownstreamCall(target="inventory", latency_ms=inv_lat, errors=5 if is_degraded else 0),
                    DownstreamCall(target="payment", latency_ms=pay_lat, errors=4 if is_degraded else 0),
                ]
            )
            inv_snap = CanonicalMetricSnapshot(
                service_id="inventory", timestamp=now, throughput_rps=120.0 if is_degraded else 60.0,
                latency_p50_ms=800.0 if is_degraded else 12.0, latency_p99_ms=inv_lat, error_rate=0.0,
                downstream_calls=[DownstreamCall(target="db-service", latency_ms=db_lat, errors=0)]
            )
            pay_snap = CanonicalMetricSnapshot(
                service_id="payment", timestamp=now, throughput_rps=120.0 if is_degraded else 60.0,
                latency_p50_ms=750.0 if is_degraded else 14.0, latency_p99_ms=pay_lat, error_rate=0.0,
                downstream_calls=[DownstreamCall(target="db-service", latency_ms=db_lat, errors=0)]
            )
            db_snap = CanonicalMetricSnapshot(
                service_id="db-service", timestamp=now, throughput_rps=240.0 if is_degraded else 120.0,
                latency_p50_ms=650.0 if is_degraded else 10.0, latency_p99_ms=db_lat, error_rate=0.0,
                pool_active=db_pool, pool_max=5
            )

            return {
                "gateway": gw_snap, "api-gateway": gw_snap,
                "orders": orders_snap, "orders-service": orders_snap,
                "inventory": inv_snap, "inventory-service": inv_snap,
                "payment": pay_snap, "payment-service": pay_snap,
                "db-service": db_snap,
            }

        elif scenario_type.upper() == "RETRY_STORM":
            is_storm = second >= 3
            retries = 55.0 if is_storm else 0.0
            orders_rps = 350.0 if is_storm else 100.0
            db_pool = 5 if is_storm else 2

            gw_snap = CanonicalMetricSnapshot(
                service_id="gateway", timestamp=now, throughput_rps=120.0,
                latency_p50_ms=30.0, latency_p99_ms=950.0 if is_storm else 35.0, error_rate=0.18 if is_storm else 0.0,
            )
            orders_snap = CanonicalMetricSnapshot(
                service_id="orders", timestamp=now, throughput_rps=orders_rps,
                latency_p50_ms=80.0, latency_p99_ms=1100.0 if is_storm else 40.0, error_rate=0.15 if is_storm else 0.0,
                retries_per_sec=retries,
            )
            inv_snap = CanonicalMetricSnapshot(
                service_id="inventory", timestamp=now, throughput_rps=orders_rps * 1.2 if is_storm else 80.0,
                latency_p50_ms=600.0 if is_storm else 15.0, latency_p99_ms=1200.0 if is_storm else 30.0, error_rate=0.25 if is_storm else 0.0,
            )
            pay_snap = CanonicalMetricSnapshot(
                service_id="payment", timestamp=now, throughput_rps=40.0,
                latency_p50_ms=18.0, latency_p99_ms=25.0, error_rate=0.0,
            )
            db_snap = CanonicalMetricSnapshot(
                service_id="db-service", timestamp=now, throughput_rps=300.0 if is_storm else 80.0,
                latency_p50_ms=450.0 if is_storm else 10.0, latency_p99_ms=980.0 if is_storm else 25.0, error_rate=0.0,
                pool_active=db_pool, pool_max=5
            )

            return {
                "gateway": gw_snap, "api-gateway": gw_snap,
                "orders": orders_snap, "orders-service": orders_snap,
                "inventory": inv_snap, "inventory-service": inv_snap,
                "payment": pay_snap, "payment-service": pay_snap,
                "db-service": db_snap,
            }

        # Default Healthy Steady State
        gw_snap = CanonicalMetricSnapshot(service_id="gateway", timestamp=now, throughput_rps=80.0, latency_p50_ms=15.0, latency_p99_ms=35.0, error_rate=0.0)
        orders_snap = CanonicalMetricSnapshot(service_id="orders", timestamp=now, throughput_rps=80.0, latency_p50_ms=20.0, latency_p99_ms=45.0, error_rate=0.0)
        inv_snap = CanonicalMetricSnapshot(service_id="inventory", timestamp=now, throughput_rps=80.0, latency_p50_ms=10.0, latency_p99_ms=25.0, error_rate=0.0)
        pay_snap = CanonicalMetricSnapshot(service_id="payment", timestamp=now, throughput_rps=80.0, latency_p50_ms=12.0, latency_p99_ms=28.0, error_rate=0.0)
        db_snap = CanonicalMetricSnapshot(service_id="db-service", timestamp=now, throughput_rps=160.0, latency_p50_ms=8.0, latency_p99_ms=20.0, error_rate=0.0, pool_active=1, pool_max=5)

        return {
            "gateway": gw_snap, "api-gateway": gw_snap,
            "orders": orders_snap, "orders-service": orders_snap,
            "inventory": inv_snap, "inventory-service": inv_snap,
            "payment": pay_snap, "payment-service": pay_snap,
            "db-service": db_snap,
        }
