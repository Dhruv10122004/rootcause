import React, { useState, useEffect, useRef } from 'react';
import { Terminal, FastForward } from 'lucide-react';

function renderInline(text) {
  if (!text) return null;

  // Split by markdown inline tokens:
  // 1. Code blocks / spans: `...`
  // 2. Bold + Italic: ***...***
  // 3. Bold: **...** (or unclosed **... at streaming end)
  // 4. Italic: *...* or _..._
  const tokenRegex = /(`[^`]+`?|\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*?|\*[^*]+\*|_[^_]+_)/g;
  const parts = text.split(tokenRegex);

  return parts.map((part, i) => {
    if (!part) return null;

    // Code span
    if (part.startsWith('`')) {
      const codeText = part.replace(/^`|`$/g, '');
      return (
        <code
          key={i}
          style={{
            fontFamily: 'var(--font-data)',
            fontSize: '11px',
            background: 'var(--bg-raised)',
            padding: '1px 4px',
            borderRadius: 3,
            border: '1px solid var(--border-subtle)',
            color: 'var(--agent-blue)',
          }}
        >
          {codeText}
        </code>
      );
    }

    // Bold + Italic: ***text***
    if (part.startsWith('***')) {
      const biText = part.replace(/^\*\*\*|\*\*\*$/g, '');
      return (
        <strong key={i} className="italic" style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
          {biText}
        </strong>
      );
    }

    // Bold: **text** or partial **text while streaming
    if (part.startsWith('**')) {
      const boldText = part.replace(/^\*\*|\*\*?$/g, '');
      return (
        <strong key={i} style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
          {boldText}
        </strong>
      );
    }

    // Italic: *text* or _text_
    if ((part.startsWith('*') && part.endsWith('*')) || (part.startsWith('_') && part.endsWith('_'))) {
      const italicText = part.slice(1, -1);
      return (
        <span key={i} style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}>
          {italicText}
        </span>
      );
    }

    // Plain text fallback - strip any lone dangling asterisks
    const cleanText = part.replace(/\*\*/g, '');
    return <span key={i}>{cleanText}</span>;
  });
}

export default function AgentTerminal({ thoughts, rawThoughts, isRunning, completed }) {
  const incoming = thoughts || rawThoughts || '';
  const cleanInitial = (completed || !isRunning) && incoming ? incoming.replace(/```(?:json)?[\s\S]*$/i, '').trimEnd() : '';
  const [displayedText, setDisplayedText] = useState(cleanInitial);
  const queueRef = useRef(cleanInitial);
  const terminalRef = useRef(null);

  useEffect(() => {
    const cleanRaw = (incoming || '').replace(/```(?:json)?[\s\S]*$/i, '').trimEnd();
    if (cleanRaw.length > queueRef.current.length) queueRef.current = cleanRaw;
    if (!incoming && !isRunning) { queueRef.current = ''; setDisplayedText(''); }
  }, [incoming, isRunning]);

  useEffect(() => {
    const timer = setInterval(() => {
      const target = queueRef.current.length;
      const current = displayedText.length;
      if (current < target) {
        const step = target - current > 60 ? 3 : target - current > 20 ? 2 : 1;
        setDisplayedText((prev) => queueRef.current.slice(0, prev.length + step));
      }
    }, 25);
    return () => clearInterval(timer);
  }, [displayedText]);

  useEffect(() => {
    if (terminalRef.current) terminalRef.current.scrollTop = terminalRef.current.scrollHeight;
  }, [displayedText]);

  const isTyping = displayedText.length < queueRef.current.length || isRunning;
  const hasBacklog = queueRef.current.length - displayedText.length > 20;

  const renderLine = (line, idx) => {
    const t = line.trim();
    if (!t) return <div key={idx} className="h-1.5" />;

    // Horizontal Rule: ---, ***, ___
    if (/^[-*_]{3,}$/.test(t)) {
      return (
        <div
          key={idx}
          className="my-2"
          style={{ borderBottom: '1px solid var(--border-subtle)' }}
        />
      );
    }

    // Markdown Headers: #, ##, ###, ####
    if (/^#{1,4}\s+/.test(t)) {
      const headerText = t.replace(/^#{1,4}\s+/, '');
      return (
        <div key={idx} className="mt-3 mb-1 pb-1" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
          <span style={{ fontFamily: 'var(--font-ui)', fontSize: '12px', fontWeight: 600, color: 'var(--agent-blue)' }}>
            {renderInline(headerText)}
          </span>
        </div>
      );
    }

    // Step or Section Bold Heading: e.g. **Step 1: ...** or **1. ...**
    if (/^\*\*Step/i.test(t) || /^\*\*\d+\./i.test(t)) {
      return (
        <div key={idx} className="mt-3 mb-1 pb-1" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
          <span style={{ fontFamily: 'var(--font-ui)', fontSize: '12px', fontWeight: 600, color: 'var(--agent-blue)' }}>
            {renderInline(t)}
          </span>
        </div>
      );
    }

    // Numbered List: e.g. "1. ..." or "2) ..."
    const numMatch = t.match(/^(\d+)[.)]\s+(.*)$/);
    if (numMatch) {
      const num = numMatch[1];
      const body = numMatch[2];
      return (
        <div key={idx} className="flex items-start gap-1.5 py-0.5 pl-2" style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
          <span style={{ color: 'var(--agent-blue)', fontWeight: 600, minWidth: '16px' }}>{num}.</span>
          <span className="flex-1 leading-relaxed">{renderInline(body)}</span>
        </div>
      );
    }

    // Bullet List: e.g. "- ...", "* ...", "• ..."
    const bulletMatch = t.match(/^[-*•]\s+(.*)$/);
    if (bulletMatch) {
      return (
        <div key={idx} className="flex items-start gap-1.5 py-0.5 pl-2" style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
          <span style={{ color: 'var(--agent-blue)', fontWeight: 600 }}>›</span>
          <span className="flex-1 leading-relaxed">{renderInline(bulletMatch[1])}</span>
        </div>
      );
    }

    // Callout / Root Cause highlights
    if (t.toLowerCase().includes('root cause:') || t.toLowerCase().includes('conclusion:')) {
      return (
        <div key={idx} className="my-1.5 p-2 rounded" style={{
          background: 'rgba(217,83,79,0.06)',
          border: '1px solid rgba(217,83,79,0.2)',
          fontSize: '11px', color: 'var(--health-crit)',
        }}>
          {renderInline(t)}
        </div>
      );
    }

    // Default regular line
    return (
      <div key={idx} className="py-0.5 leading-relaxed" style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
        {renderInline(t)}
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full rounded-lg overflow-hidden"
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}>

      {/* Title bar */}
      <div className="flex items-center justify-between px-3 py-2" style={{ borderBottom: '1px solid var(--border-subtle)', background: 'var(--bg-raised)' }}>
        <div className="flex items-center gap-2">
          <Terminal className="w-3.5 h-3.5" style={{ color: 'var(--agent-blue)' }} />
          <span style={{ fontFamily: 'var(--font-data)', fontSize: '11px', fontWeight: 500, color: 'var(--text-secondary)' }}>
            agent reasoning
          </span>
        </div>

        <div className="flex items-center gap-2">
          {hasBacklog && (
            <button onClick={() => setDisplayedText(queueRef.current)}
              className="flex items-center gap-1 px-2 py-0.5 rounded cursor-pointer transition"
              style={{ fontFamily: 'var(--font-data)', fontSize: '10px', background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', color: 'var(--text-muted)' }}>
              <FastForward className="w-3 h-3" /> skip
            </button>
          )}
          {isTyping ? (
            <span className="flex items-center gap-1" style={{ fontFamily: 'var(--font-data)', fontSize: '10px', color: 'var(--agent-blue)' }}>
              <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--agent-blue)' }} />
              reasoning
            </span>
          ) : completed ? (
            <span style={{ fontFamily: 'var(--font-data)', fontSize: '10px', color: 'var(--health-ok)' }}>done</span>
          ) : (
            <span style={{ fontFamily: 'var(--font-data)', fontSize: '10px', color: 'var(--text-muted)' }}>idle</span>
          )}
        </div>
      </div>

      {/* Content */}
      <div ref={terminalRef} className="flex-1 p-3 overflow-y-auto select-text" style={{ fontFamily: 'var(--font-data)' }}>
        {!displayedText && !isRunning ? (
          <div className="h-full flex flex-col items-center justify-center text-center py-8" style={{ color: 'var(--text-muted)' }}>
            <p style={{ fontFamily: 'var(--font-ui)', fontSize: '13px', fontWeight: 500, marginBottom: 4 }}>
              Agent idle
            </p>
            <p style={{ fontSize: '11px', maxWidth: 220 }}>
              Click "Run experiment" to start stress testing and watch the agent analyze failure propagation.
            </p>
          </div>
        ) : (
          <div>
            {displayedText.split('\n').map((line, idx) => renderLine(line, idx))}
            {isTyping && <span className="inline-block w-1.5 h-3 ml-0.5 animate-pulse" style={{ background: 'var(--agent-blue)' }} />}
          </div>
        )}
      </div>
    </div>
  );
}
