import { useState } from 'react'
import { useDashboardStore } from '../hooks/useDashboardStore'
import { EVENT_CATEGORIES, getAgentColor } from '../types'

export function EventFeed() {
  const events = useDashboardStore((s) => s.events)
  const eventFilter = useDashboardStore((s) => s.eventFilter)
  const setEventFilter = useDashboardStore((s) => s.setEventFilter)

  const filtered = eventFilter
    ? events.filter((e) => {
        const q = eventFilter.toLowerCase()
        return (
          e.event_type.toLowerCase().includes(q) ||
          (e.agent_id ?? '').toLowerCase().includes(q) ||
          (e.chain_id ?? '').toLowerCase().includes(q) ||
          JSON.stringify(e.payload).toLowerCase().includes(q)
        )
      })
    : events

  return (
    <div className="panel">
      <div className="panel-header">
        <span style={{ color: 'var(--green)' }}>●</span>
        <div className="panel-title">Event Feed</div>
        <div className="panel-badge" style={{ background: 'var(--bg-3)', color: 'var(--text-2)' }}>
          {filtered.length} events
        </div>
      </div>
      <div className="filter-bar" style={{ padding: '8px 16px' }}>
        <input
          className="filter-input"
          placeholder="Filter events..."
          value={eventFilter}
          onChange={(e) => setEventFilter(e.target.value)}
        />
      </div>
      <div className="panel-body">
        {filtered.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">📡</div>
            <div>Waiting for events...</div>
          </div>
        ) : (
          filtered.map((event, i) => {
            const cat = EVENT_CATEGORIES[event.event_type] ?? {
              icon: '•',
              color: 'var(--text-2)',
              label: event.event_type,
            }
            return (
              <div className="event-item" key={event.id ?? i}>
                <div className="event-time">{formatTime(event.timestamp)}</div>
                <div className="event-icon" style={{ background: `${cat.color}20`, color: cat.color }}>
                  {cat.icon}
                </div>
                <div className="event-body">
                  <div className="event-title">
                    {event.agent_id && (
                      <span className="agent-ref" style={{ color: getAgentColor(event.agent_id).fg }}>
                        {event.agent_id}
                      </span>
                    )}{' '}
                    {cat.label}
                    {event.chain_id && (
                      <>
                        {' '}
                        in <span className="chain-ref">#{event.chain_id}</span>
                      </>
                    )}
                  </div>
                  <div className="event-detail">{formatPayload(event)}</div>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return '--:--:--'
  }
}

function formatPayload(event: { event_type: string; payload: Record<string, unknown> }): string {
  const p = event.payload
  switch (event.event_type) {
    case 'tool.called':
      return `${p.tool_name}(${p.args ? JSON.stringify(p.args).slice(0, 60) : ''})`
    case 'tool.result':
      return `${p.tool_name} → ${p.success ? 'ok' : 'error'}${p.duration_ms ? ` (${Math.round(p.duration_ms as number)}ms)` : ''}`
    case 'agent.started':
      return `model: ${p.model ?? 'default'}`
    case 'agent.completed':
      return `${p.tokens ?? ''} tokens · ${p.elapsed ?? ''}ms`
    case 'chain.delegated':
      return `${p.from_agent ?? ''} → ${p.to_agent ?? ''}`
    case 'chain.awaiting_approval':
      return `pending agents: ${p.pending_agents ?? ''}`
    case 'usage.tracked':
      return `${p.model}: ${p.input_tokens ?? 0} in + ${p.output_tokens ?? 0} out`
    case 'heartbeat.checked':
      return `action: ${p.action}${p.had_content ? '' : ' (no HEARTBEAT.md)'}`
    case 'cron.triggered':
      return `${p.job_name}: ${p.status}${p.error ? ` — ${p.error}` : ''}`
    case 'memory.written':
      return `${p.source ?? 'unknown'}: ${p.file ?? p.content_preview ?? ''}`
    default:
      return JSON.stringify(p).slice(0, 100)
  }
}
