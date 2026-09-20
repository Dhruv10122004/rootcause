import React, { useMemo } from 'react';
import { Handle, Position } from '@xyflow/react';
import { Server, Database, Shield, Zap } from 'lucide-react';

function Waveform({ value = 0, healthy = true, width = 52, height = 16 }) {
  const points = useMemo(() => {
    const pts = [];
    const n = 10;
    for (let i = 0; i <= n; i++) {
      const x = (i / n) * width;
      const amp = healthy ? height * 0.2 : height * 0.45;
      const freq = healthy ? 0.7 : 2.5;
      const noise = healthy ? 0 : (Math.sin(i * 3.1 + value * 0.01) * 0.3);
      const y = height / 2 + Math.sin(i * freq + Date.now() * 0.0008) * amp + noise * height * 0.2;
      pts.push(`${x},${Math.max(1, Math.min(height - 1, y))}`);
    }
    return pts.join(' ');
  }, [value, healthy, width, height]);

  const color = !healthy ? (value > 800 ? 'var(--health-crit)' : 'var(--health-warn)') : 'var(--health-ok)';

  return (
    <svg width={width} height={height} style={{ opacity: 0.7 }}>
      <polyline points={points} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" />
    </svg>
  );
}

export default function ServiceNode({ data }) {
  const {
    id,
    display_name,
    role,
    host_port,
    metrics = {},
    isRootCause = false,
    isBlastRadius = false,
    isCausalPath = false,
    isSelected = false,
    chaosActive = null,
    onClick,
  } = data;

  const p99 = metrics.latency_p99_ms ?? 0;
  const rps = metrics.throughput_rps ?? 0;
  const errorRate = metrics.error_rate ?? 0;
  const poolActive = metrics.pool_active ?? null;
  const poolMax = metrics.pool_max ?? null;
  const retries = metrics.retries_per_sec ?? 0;

  const isPoolSaturated = poolActive !== null && poolMax !== null && poolActive >= poolMax;
  const isPoolStressed = poolActive !== null && poolMax !== null && poolActive >= (poolMax - 1) && !isPoolSaturated;

  const isHealthy = p99 < 300 && errorRate < 0.05 && !isPoolSaturated && !isPoolStressed;
  const isStressed = (p99 >= 300 && p99 <= 800) || (errorRate >= 0.05 && errorRate <= 0.15) || isPoolStressed;
  const isCritical = p99 > 800 || errorRate > 0.15 || isPoolSaturated;

  const isDb = id.toLowerCase().includes('db');
  const isGateway = role === 'ingress';

  // Border color: functional meaning only
  let borderColor = 'var(--border-medium)';
  let cardBg = 'var(--bg-surface)';

  if (isRootCause) {
    borderColor = 'var(--health-crit)';
    cardBg = 'rgba(217, 83, 79, 0.08)';
  } else if (isCausalPath) {
    borderColor = 'var(--causal-indigo)';
    cardBg = 'rgba(124, 110, 240, 0.08)';
  } else if (isBlastRadius || isCritical) {
    borderColor = 'var(--health-crit)';
    cardBg = 'rgba(217, 83, 79, 0.06)';
  } else if (isStressed) {
    borderColor = 'var(--health-warn)';
    cardBg = 'rgba(229, 166, 62, 0.06)';
  } else if (isSelected) {
    borderColor = 'var(--text-secondary)';
  }

  const sickClass = (chaosActive || isCritical) ? 'node-sick' : '';

  return (
    <div
      className={`relative rounded-lg cursor-pointer transition-all duration-300 ${sickClass} ${chaosActive ? 'inject-active' : ''}`}
      style={{
        width: 220,
        background: cardBg,
        border: `1.5px solid ${borderColor}`,
        padding: '10px 12px',
      }}
      onClick={(e) => { e.stopPropagation(); onClick?.(id); }}
    >
      <Handle type="target" position={Position.Top} style={{ background: 'var(--text-muted)', width: 6, height: 6, border: '2px solid var(--bg-root)' }} />

      {/* Root cause tag */}
      {isRootCause && (
        <div className="absolute -top-2.5 left-1/2 -translate-x-1/2 px-2 py-0.5 rounded text-[9px] font-semibold z-10"
          style={{ background: 'var(--health-crit)', color: 'white', fontFamily: 'var(--font-data)' }}>
          root cause
        </div>
      )}

      {/* Chaos indicator */}
      {chaosActive && !isRootCause && (
        <div className="absolute -top-2.5 right-2 flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold z-10"
          style={{ background: 'var(--health-warn)', color: 'var(--bg-root)', fontFamily: 'var(--font-data)' }}>
          <Zap className="w-2.5 h-2.5" /> injected
        </div>
      )}

      {/* Top row: icon + name + port */}
      <div className="flex items-center gap-2 mb-2">
        <div className="p-1 rounded" style={{ background: 'var(--bg-raised)' }}>
          {isDb ? <Database className="w-3.5 h-3.5" style={{ color: 'var(--text-secondary)' }} /> :
           isGateway ? <Shield className="w-3.5 h-3.5" style={{ color: 'var(--text-secondary)' }} /> :
           <Server className="w-3.5 h-3.5" style={{ color: 'var(--text-secondary)' }} />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-xs font-semibold truncate" style={{ fontFamily: 'var(--font-ui)', color: 'var(--text-primary)' }}>
            {display_name || id}
          </div>
          <div className="text-[10px]" style={{ fontFamily: 'var(--font-data)', color: 'var(--text-muted)' }}>
            :{host_port}
          </div>
        </div>
        <Waveform value={p99} healthy={isHealthy} />
      </div>

      {/* Vitals row */}
      <div className="flex gap-1.5" style={{ fontFamily: 'var(--font-data)', fontSize: '10px' }}>
        <div className="flex-1 rounded px-1.5 py-1" style={{ background: 'var(--bg-raised)' }}>
          <div style={{ color: 'var(--text-muted)', fontSize: '8px' }}>rps</div>
          <div style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{rps.toFixed(1)}</div>
        </div>
        <div className="flex-1 rounded px-1.5 py-1"
          style={{ background: isCritical ? 'rgba(217,83,79,0.1)' : 'var(--bg-raised)' }}>
          <div style={{ color: 'var(--text-muted)', fontSize: '8px' }}>p99</div>
          <div style={{ color: isCritical ? 'var(--health-crit)' : p99 > 300 ? 'var(--health-warn)' : 'var(--text-primary)', fontWeight: 500 }}>
            {Math.round(p99)}ms
          </div>
        </div>
        {errorRate > 0 && (
          <div className="flex-1 rounded px-1.5 py-1" style={{ background: 'rgba(217,83,79,0.1)' }}>
            <div style={{ color: 'var(--text-muted)', fontSize: '8px' }}>err</div>
            <div style={{ color: 'var(--health-crit)', fontWeight: 500 }}>{(errorRate * 100).toFixed(0)}%</div>
          </div>
        )}
      </div>

      {/* Pool bar */}
      {poolMax != null && poolMax > 0 && (
        <div className="mt-1.5">
          <div className="flex justify-between" style={{ fontFamily: 'var(--font-data)', fontSize: '9px', color: 'var(--text-muted)' }}>
            <span>pool</span>
            <span style={{ color: poolActive >= poolMax ? 'var(--health-crit)' : 'var(--text-secondary)' }}>
              {poolActive}/{poolMax}
            </span>
          </div>
          <div className="w-full h-1 rounded-full mt-0.5" style={{ background: 'var(--bg-raised)' }}>
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{
                width: `${Math.min(100, (poolActive / poolMax) * 100)}%`,
                background: poolActive >= poolMax ? 'var(--health-crit)' : (poolActive / poolMax) > 0.7 ? 'var(--health-warn)' : 'var(--health-ok)',
              }}
            />
          </div>
        </div>
      )}

      <Handle type="source" position={Position.Bottom} style={{ background: 'var(--text-muted)', width: 6, height: 6, border: '2px solid var(--bg-root)' }} />
    </div>
  );
}
