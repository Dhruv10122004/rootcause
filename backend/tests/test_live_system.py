import os
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

backend_dir = str(Path(__file__).resolve().parent.parent)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    import pytest
except ImportError:
    pytest = None

from app.services.topology_parser import DockerComposeParser
from app.models.orchestrator import ExperimentPlan, StressPattern
from app.services.orchestrator import ExperimentOrchestrator


mark_anyio = pytest.mark.anyio if pytest else lambda f: f
mark_skip = pytest.mark.skip(reason="Manual integration test - run directly with python test_live_system.py") if pytest else lambda f: f

@mark_skip
@mark_anyio
async def test_live_microservices_stress():
    compose_path = Path(__file__).resolve().parent.parent.parent / "target-services" / "docker-compose.yml"
    parser = DockerComposeParser()
    topology = parser.parse_file(compose_path)

    print("=" * 65)
    print(" 1. TOPOLOGY AUTO-DISCOVERED FROM DOCKER COMPOSE")
    print("=" * 65)
    print(f"Services Found: {[n.id for n in topology.nodes]}")
    print(f"Ingress Entrypoint: {topology.entrypoint_ids}")
    print(f"Shared Bottleneck Candidates: {topology.shared_bottleneck_ids}")
    print(f"Dependency Edges: {[(e.source, e.target) for e in topology.edges]}")

    # Check if target services are running before executing live stress
    import httpx
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get("http://localhost:8080/_metrics")
            if resp.status_code != 200:
                if pytest:
                    pytest.skip("Target services returned non-200. Start with npm start --prefix target-services")
    except Exception:
        if pytest:
            pytest.skip("Target services not running on localhost:8080. Start with npm start --prefix target-services")

    print("\n" + "=" * 65)
    print(" 2. INITIATING TARGETED STRESS EXPERIMENT")
    print("=" * 65)
    print("Firing 35 concurrent requests at http://localhost:8080/order for 8s...")

    plan = ExperimentPlan(
        name="Real-World Target Services Stress Test",
        target_url="http://localhost:8080",
        http_endpoint="/order",
        concurrency_users=35,
        duration_seconds=8,
        stress_pattern=StressPattern.SPIKE
    )

    orchestrator = ExperimentOrchestrator()
    print("\nStreaming live telemetry from all 5 microservices...\n")

    async for event in orchestrator.run_experiment_stream(topology, plan):
        t = event.event_type.value
        if t == "STATUS_UPDATE":
            msg = event.data.get("message", "")
            print(f"[*] {msg}")
        elif t == "METRICS_TICK":
            sec = event.data.get("second", 0)
            snaps = event.data.get("snapshots", {})
            gw = snaps.get("gateway", {})
            orders = snaps.get("orders", {})
            db = snaps.get("db-service", {})
            gw_rps = gw.get("throughput_rps", 0.0)
            gw_p99 = gw.get("latency_p99_ms", 0.0)
            orders_retries = orders.get("retries_per_sec", 0.0)
            db_active = db.get("pool_active", 0)
            db_max = db.get("pool_max", 0)
            print(f"    [t={sec:02d}s] Gateway: {gw_rps} RPS (p99={gw_p99}ms) | DB Pool: {db_active}/{db_max} | Orders Retries: {orders_retries}/s")
        elif t == "REASONING_CHUNK":
            token = event.data.get("token", "")
            print(token, end="", flush=True)
        elif t == "DIAGNOSIS_REPORT":
            print("\n\n" + "=" * 65)
            print(" 3. ROOT CAUSE ARCHITECTURAL REPORT (PRODUCED BY AI)")
            print("=" * 65)
            print(f"FAILURE MODE:       {event.data.get('failure_mode')}")
            print(f"ROOT CAUSE SERVICE: {event.data.get('root_cause_service')}")
            print(f"BLAST RADIUS:       {event.data.get('blast_radius')}")
            print(f"CONFIDENCE:         {event.data.get('confidence_score')}")
            print(f"SUMMARY:            {event.data.get('summary')}")
            print("\nEVIDENCE:")
            for ev in event.data.get("evidence", []):
                print(f"  - {ev}")
            print(f"\nRECOMMENDED REMEDIATION:")
            fix = event.data.get("suggested_fix", {})
            print(f"  Action: {fix.get('action')}")
            print(f"  Target: {fix.get('target_service')}")
            print(f"  Fix:    {fix.get('recommendation')}")
            print("=" * 65)


if __name__ == "__main__":
    asyncio.run(test_live_microservices_stress())
