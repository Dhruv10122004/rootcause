import React from 'react';
import { BaseEdge, getSmoothStepPath, EdgeLabelRenderer } from '@xyflow/react';

export default function CausalEdge({
  id, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition,
  data = {}, style = {},
}) {
  const { isCausal = false, isStressed = false, isRootCause = false } = data;

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition,
    borderRadius: 12,
  });

  let strokeColor = 'var(--border-medium)';
  let strokeWidth = 1.2;
  let className = '';

  if (isCausal) {
    strokeColor = 'var(--causal-indigo)';
    strokeWidth = 2.5;
    className = 'causal-edge-path';
  } else if (isRootCause) {
    strokeColor = 'var(--health-crit)';
    strokeWidth = 2;
  } else if (isStressed) {
    strokeColor = 'var(--health-warn)';
    strokeWidth = 1.8;
    className = 'stressed-edge-path';
  }

  return (
    <>
      {/* Glow under causal path */}
      {isCausal && (
        <path d={edgePath} fill="none" stroke={strokeColor} strokeWidth={8} strokeOpacity={0.1} />
      )}

      <BaseEdge
        id={id}
        path={edgePath}
        style={{ ...style, stroke: strokeColor, strokeWidth }}
        className={className}
      />

      {/* Small label on causal edges */}
      {isCausal && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'none',
              fontFamily: 'var(--font-data)',
              fontSize: '9px',
              fontWeight: 500,
              color: 'var(--causal-indigo)',
              background: 'var(--bg-root)',
              padding: '1px 6px',
              borderRadius: '3px',
              border: '1px solid var(--causal-indigo)',
              opacity: 0.85,
            }}
          >
            causal
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
