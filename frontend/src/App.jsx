import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  MarkerType,
} from '@xyflow/react';

import ServiceNode from './components/ServiceNode';
import CausalEdge from './components/CausalEdge';
import AgentTerminal from './components/AgentTerminal';
import DiagnosisDrawer from './components/DiagnosisDrawer';
import ChaosChipBar from './components/ChaosChipBar';
import InspectPanel from './components/InspectPanel';
import Header from './components/Header';

const nodeTypes = { serviceNode: ServiceNode };
const edgeTypes = { causal: CausalEdge };

const DEFAULT_COMPOSE = `version: "3.8"
services:
  gateway:
    ports: ["8080:8080"]
    depends_on: [orders]
  orders:
    ports: ["8081:8081"]
    depends_on: [inventory, payment]
  inventory:
    ports: ["8082:8082"]
    depends_on: [db-service]
  payment:
    ports: ["8083:8083"]
    depends_on: [db-service]
  db-service:
    ports: ["8084:8084"]
`;

const SERVICE_PORTS = {
  gateway: 8080,
  orders: 8081,
  inventory: 8082,
  payment: 8083,
  'db-service': 8084,
};

const POSITIONS = {
  gateway: { x: 380, y: 50 },
  orders: { x: 380, y: 230 },
  inventory: { x: 160, y: 420 },
  payment: { x: 600, y: 420 },
  'db-service': { x: 380, y: 600 },
};

export default function App() {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [topology, setTopology] = useState(null);

  const [mode, setMode] = useState('live');
  const [concurrency, setConcurrency] = useState(35);
  const [duration, setDuration] = useState(8);

  const [isRunning, setIsRunning] = useState(false);
  const [thoughts, setThoughts] = useState('');
  const [report, setReport] = useState(null);
  const [showDrawer, setShowDrawer] = useState(false);
  const [currentMetrics, setCurrentMetrics] = useState({});
  const [metricsHistory, setMetricsHistory] = useState({});

  const [panelWidth, setPanelWidth] = useState(380);
  const [runHistory, setRunHistory] = useState([]);

  const [chaosInjections, setChaosInjections] = useState({});
  const [selectedNodeId, setSelectedNodeId] = useState(null);

  const [causalPath, setCausalPath] = useState([]);
  const [rootCauseId, setRootCauseId] = useState(null);
  const [blastRadiusIds, setBlastRadiusIds] = useState([]);

  const socketRef = useRef(null);
  const metricsRef = useRef({});

  // ── Load topology ──
  const loadTopology = useCallback(async () => {
    try {
      const res = await fetch('/api/topology/parse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yaml_content: DEFAULT_COMPOSE, target_host: 'localhost' }),
      });
      if (res.ok) {
        const data = await res.json();
        setTopology(data);
      }
    } catch (e) {
      console.error('Topology load failed:', e);
    }
  }, []);

  useEffect(() => { loadTopology(); }, [loadTopology]);

  // ── Build graph ──
  const buildGraph = useCallback(() => {
    if (!topology?.nodes) return;

    const causalPairs = [];
    for (let i = 0; i < causalPath.length - 1; i++) {
      causalPairs.push([causalPath[i], causalPath[i + 1]]);
    }

    setNodes((prevNodes) => {
      const prevMap = new Map((prevNodes || []).map((n) => [n.id, n]));
      return topology.nodes.map((n, i) => {
        const prevNode = prevMap.get(n.id);
        const pos = prevNode?.position || POSITIONS[n.id] || { x: 100 + i * 180, y: 100 };
        const m = currentMetrics[n.id] || metricsRef.current[n.id] || {};
        return {
          id: n.id,
          type: 'serviceNode',
          position: pos,
          data: {
            id: n.id,
            display_name: n.display_name || n.id,
            role: n.role,
            host_port: n.host_port,
            metrics: m,
            isRootCause: rootCauseId === n.id,
            isBlastRadius: blastRadiusIds.includes(n.id),
            isCausalPath: causalPairs.some(([s, t]) => s === n.id || t === n.id),
            isSelected: selectedNodeId === n.id,
            chaosActive: chaosInjections[n.id] || null,
            onClick: (nodeId) => setSelectedNodeId((prev) => (prev === nodeId ? null : nodeId)),
          },
        };
      });
    });

    const flowEdges = topology.edges.map((e, idx) => {
      const tm = currentMetrics[e.target] || metricsRef.current[e.target] || {};
      const isStressed = (tm.latency_p99_ms ?? 0) > 400 || (tm.pool_active ?? 0) >= (tm.pool_max ?? 999);
      const isCausal = causalPairs.some(([s, t]) => s === e.source && t === e.target);
      const isRoot = rootCauseId === e.target;

      let strokeColor = '#2a2a2a';
      if (isCausal) strokeColor = '#7c6ef0';
      else if (isRoot) strokeColor = '#d9534f';
      else if (isStressed) strokeColor = '#e5a63e';

      return {
        id: `e-${e.source}-${e.target}-${idx}`,
        source: e.source,
        target: e.target,
        type: 'causal',
        animated: isCausal || isStressed,
        data: { isCausal, isStressed, isRootCause: isRoot },
        style: { stroke: strokeColor, strokeWidth: isCausal ? 2.5 : isStressed ? 1.8 : 1.2 },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: strokeColor,
          width: 12,
          height: 12,
        },
      };
    });

    setEdges(flowEdges);
  }, [topology, currentMetrics, selectedNodeId, chaosInjections, rootCauseId, blastRadiusIds, causalPath]);

  useEffect(() => { buildGraph(); }, [buildGraph]);


  const handleInject = useCallback(async (serviceId, injectType) => {
    const port = SERVICE_PORTS[serviceId];
    if (!port) return;

    setChaosInjections((prev) => {
      const currentChaos = prev[serviceId];
      const isActive = currentChaos?.latency_ms === injectType.chaos.latency_ms &&
                       currentChaos?.error_rate === injectType.chaos.error_rate;

      if (isActive) {
        // Toggle OFF
        fetch(`/api/chaos/${port}`, { method: 'DELETE' }).catch((e) => console.error(e));
        const next = { ...prev };
        delete next[serviceId];
        return next;
      } else {
        // Toggle ON
        fetch(`/api/chaos/${port}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(injectType.chaos),
        }).catch((e) => console.error(e));
        return { ...prev, [serviceId]: injectType.chaos };
      }
    });
  }, []);

  const clearAllChaos = useCallback(async () => {
    const promises = Object.keys(chaosInjections).map(async (svcId) => {
      const port = SERVICE_PORTS[svcId];
      if (port) {
        try { await fetch(`/api/chaos/${port}`, { method: 'DELETE' }); } catch (e) { /* ignore */ }
      }
    });
    await Promise.all(promises);
    setChaosInjections({});
  }, [chaosInjections]);

  // ── Metrics history ──
  useEffect(() => {
    if (Object.keys(currentMetrics).length > 0) {
      setMetricsHistory((prev) => {
        const next = { ...prev };
        for (const [svcId, snap] of Object.entries(currentMetrics)) {
          next[svcId] = [...(next[svcId] || []).slice(-14), snap];
        }
        return next;
      });
    }
  }, [currentMetrics]);

  // ── WebSocket experiment ──
  const startExperiment = () => {
    if (!topology) return;

    setIsRunning(true);
    setThoughts('');
    setReport(null);
    setShowDrawer(false);
    setRootCauseId(null);
    setBlastRadiusIds([]);
    setCausalPath([]);
    metricsRef.current = {};
    setMetricsHistory({});

    const wsHost = window.location.port === '5173' ? `${window.location.hostname}:8000` : window.location.host;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${wsHost}/api/ws/experiment`);
    socketRef.current = ws;

    ws.onopen = () => {
      let simScenario = null;
      if (mode === 'sim_cascade') simScenario = 'CASCADING_FAILURE';
      if (mode === 'sim_retry') simScenario = 'RETRY_STORM';

      ws.send(JSON.stringify({
        topology,
        plan: {
          name: 'Stress Test',
          target_url: 'http://localhost:8080',
          http_endpoint: '/order',
          concurrency_users: concurrency,
          duration_seconds: duration,
          stress_pattern: 'SPIKE',
        },
        simulation_scenario: simScenario,
      }));
    };

    let currentReport = null;

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        const { event_type, data } = msg;

        if (event_type === 'METRICS_TICK') {
          const snaps = data.snapshots || {};
          metricsRef.current = snaps;
          setCurrentMetrics({ ...snaps });
          setNodes((prevNodes) =>
            (prevNodes || []).map((node) => {
              const m = snaps[node.id];
              if (!m) return node;
              return {
                ...node,
                data: {
                  ...node.data,
                  metrics: m,
                },
              };
            })
          );
          setEdges((prevEdges) =>
            (prevEdges || []).map((edge) => {
              const tm = snaps[edge.target] || {};
              const isStressed = (tm.latency_p99_ms ?? 0) > 400 || (tm.pool_active ?? 0) >= (tm.pool_max ?? 999);
              return {
                ...edge,
                animated: edge.data?.isCausal || isStressed,
                data: {
                  ...edge.data,
                  isStressed,
                },
              };
            })
          );
        } else if (event_type === 'REASONING_CHUNK') {
          setThoughts((prev) => prev + (data.token || ''));
        } else if (event_type === 'DIAGNOSIS_REPORT') {
          const root = data.root_cause_service;
          const blast = data.blast_radius || [];
          setReport(data);
          currentReport = data;
          setRootCauseId(root);
          setBlastRadiusIds(blast);
          setShowDrawer(true);
          computeCausalPath(root, blast);
        } else if (event_type === 'ERROR') {
          console.error('Experiment error:', data.error);
          setThoughts((prev) => prev + `\n[ERROR] ${data.error}\n`);
        } else if (event_type === 'COMPLETED') {
          setIsRunning(false);
          ws.close();
          if (currentReport) {
            setRunHistory(prev => [...prev, {
              id: Date.now(),
              timestamp: new Date().toISOString(),
              mode: mode,
              root_cause: currentReport.root_cause_service,
              confidence: currentReport.confidence_score,
            }]);
          }
        }
      } catch (err) {
        console.error('WS error:', err);
      }
    };

    ws.onerror = () => setIsRunning(false);
    ws.onclose = () => setIsRunning(false);
  };

  const stopExperiment = () => {
    socketRef.current?.close();
    setIsRunning(false);
  };

  const computeCausalPath = (rootId, blastIds) => {
    if (!topology || !rootId) return;
    const reverseAdj = {};
    for (const edge of topology.edges) {
      if (!reverseAdj[edge.target]) reverseAdj[edge.target] = [];
      reverseAdj[edge.target].push(edge.source);
    }
    const path = [rootId];
    const visited = new Set([rootId]);
    let current = rootId;
    while (reverseAdj[current]) {
      const ups = reverseAdj[current].filter((u) => !visited.has(u));
      if (!ups.length) break;
      const next = ups.find((u) => blastIds.includes(u)) || ups[0];
      path.push(next);
      visited.add(next);
      current = next;
    }
    setCausalPath(path.reverse());
  };

  const handleApplyFix = useCallback(async (serviceId) => {
    const port = SERVICE_PORTS[serviceId];
    if (!port) return;
    try {
      await fetch(`/api/fix/${port}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: true }),
      });
      await fetch(`/api/chaos/${port}`, { method: 'DELETE' }).catch(() => {});
      setChaosInjections(prev => {
        const next = { ...prev };
        delete next[serviceId];
        return next;
      });
    } catch (e) {
      console.error(`Fix apply failed on ${serviceId}:`, e);
    }
  }, []);

  // ── Panel Resizing ──
  const startDrag = useCallback((e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = panelWidth;

    const onMouseMove = (moveEvent) => {
      const delta = startX - moveEvent.clientX;
      const newWidth = Math.max(250, Math.min(800, startWidth + delta));
      setPanelWidth(newWidth);
    };

    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }, [panelWidth]);

  // ── Selected node for inspect panel ──
  const selectedNodeData = selectedNodeId && topology
    ? (() => {
        const n = topology.nodes.find((nd) => nd.id === selectedNodeId);
        if (!n) return null;
        return {
          id: n.id,
          display_name: n.display_name || n.id,
          role: n.role,
          host_port: n.host_port,
          metrics: currentMetrics[n.id] || {},
        };
      })()
    : null;

  return (
    <div className="w-screen h-screen flex flex-col overflow-hidden select-none"
      style={{ background: 'var(--bg-root)', color: 'var(--text-primary)' }}>

      <Header
        isRunning={isRunning}
        onStart={startExperiment}
        onStop={stopExperiment}
        mode={mode} setMode={setMode}
        concurrency={concurrency} setConcurrency={setConcurrency}
        duration={duration} setDuration={setDuration}
        runHistory={runHistory}
      >
        <ChaosChipBar
          selectedNode={selectedNodeId}
          onInject={handleInject}
          onClearAll={clearAllChaos}
          activeChaos={chaosInjections}
        />
      </Header>

      <div className="flex-1 flex overflow-hidden relative">
        {/* Graph canvas */}
        <div className="flex-1 relative">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.3 }}
            minZoom={0.4}
            maxZoom={1.5}
            onPaneClick={() => setSelectedNodeId(null)}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#222222" gap={28} size={1} variant="dots" />
            <Controls position="top-left" showInteractive={false} />
          </ReactFlow>

          {/* Top-right status */}
          <div className="absolute top-4 right-4 flex items-center gap-2 z-10 pointer-events-none"
            style={{ fontFamily: 'var(--font-data)', fontSize: '11px' }}>
            {(() => {
              const gw = currentMetrics.gateway || {};
              const db = currentMetrics['db-service'] || {};
              return (
                <>
                  <span className="px-2 py-0.5 rounded" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}>
                    <span style={{ color: 'var(--text-muted)' }}>degraded </span>
                    <span style={{ fontWeight: 600, color: blastRadiusIds.length > 0 ? 'var(--health-warn)' : 'var(--text-primary)' }}>
                      {blastRadiusIds.length}/5
                    </span>
                  </span>
                  <span className="px-2 py-0.5 rounded" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}>
                    <span style={{ color: 'var(--text-muted)' }}>rps </span>
                    <span style={{ fontWeight: 600 }}>{(gw.throughput_rps || 0).toFixed(0)}</span>
                  </span>
                  <span className="px-2 py-0.5 rounded" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}>
                    <span style={{ color: 'var(--text-muted)' }}>pool </span>
                    <span style={{ fontWeight: 600, color: (db.pool_active ?? 0) >= 5 ? 'var(--health-crit)' : 'var(--text-primary)' }}>
                      {db.pool_active ?? 0}/{db.pool_max ?? 5}
                    </span>
                  </span>
                  <span className="px-2 py-0.5 rounded" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}>
                    <span style={{ color: 'var(--text-muted)' }}>p99 </span>
                    <span style={{ fontWeight: 600, color: (gw.latency_p99_ms ?? 0) > 800 ? 'var(--health-crit)' : 'var(--text-primary)' }}>
                      {Math.round(gw.latency_p99_ms || 0)}ms
                    </span>
                  </span>
                </>
              );
            })()}
          </div>
        </div>

        {/* Right panel: inspect or terminal */}
        <div style={{ width: panelWidth, borderLeft: '1px solid var(--border-subtle)', background: 'var(--bg-surface)' }}
          className="h-full flex flex-col relative transition-none">
          
          {/* Resize handle */}
          <div
            onMouseDown={startDrag}
            className="absolute top-0 bottom-0 -left-1 w-2 cursor-col-resize z-50 group"
          >
            <div className="w-0.5 h-full mx-auto bg-transparent group-hover:bg-[#5e5a56] transition-colors" />
          </div>

          {selectedNodeId && selectedNodeData && (
            <InspectPanel
              node={selectedNodeData}
              metricsHistory={metricsHistory}
              chaosState={chaosInjections[selectedNodeId] || null}
              onClose={() => setSelectedNodeId(null)}
            />
          )}

          <div className={`flex-1 p-3 overflow-hidden ${selectedNodeId && selectedNodeData ? 'hidden' : 'flex flex-col'}`}>
            <AgentTerminal thoughts={thoughts} isRunning={isRunning} completed={Boolean(report)} />
          </div>
        </div>
      </div>

      {/* Diagnosis drawer */}
      {showDrawer && report && (
        <DiagnosisDrawer
          report={report}
          onHighlightService={(svcId) => setSelectedNodeId(svcId)}
          onDismiss={() => setShowDrawer(false)}
          onApplyFix={handleApplyFix}
          onRerun={startExperiment}
        />
      )}
    </div>
  );
}
