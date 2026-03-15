import { useDashboardStore } from '../hooks/useDashboardStore'
import { EVENT_CATEGORIES, getAgentColor } from '../types'

export function WorkLog() {
  const events = useDashboardStore((s) => s.events)
  const selectedAgent = useDashboardStore((s) => s.selectedAgent)
  const agents = useDashboardStore((s) => s.agents)

  // Pick agent: selected or first running, or first available
  const agentId =
    selectedAgent ??
    Object.values(agents).find((a) => a.status === 'running')?.agent_id ??
    Object.values(agents)[0]?.agent_id

  const agent = agentId ? agents[agentId] : null
  const agentEvents = agentId
    ? events.filter((e) => e.agent_id === agentId).slice(0, 30)
    : []

  const color = agentId ? getAgentColor(agentId) : { bg: 'var(--bg-3)', fg: 'var(--text-2)' }

  return (
    <div className="panel">
      <div className="panel-header">
        <span style={{ color: 'var(--purple)' }}>◆</span>
        <div className="panel-title">
          Work Log{agentId ? `: ${agentId}` : ''}
        </div>
        {agent?.chain_id && (
          <div className="panel-badge" style={{ background: 'var(--purple-dim)', color: 'var(--purple)' }}>
            #{agent.chain_id}
          </div>
        )}
      </div>
      <div className="panel-body">
        {!agentId ? (
          <div className="empty-state">
            <div className="empty-state-icon">📋</div>
            <div>Select an agent to see its work log</div>
          </div>
        ) : (
          <>
            {agent && (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  padding: '10px 14px',
                  background: 'var(--bg-2)',
                  borderRadius: 8,
                  marginBottom: 12,
                }}
              >
                <div className="agent-avatar" style={{ background: color.bg, color: color.fg, width: 32, height: 32, fontSize: 15 }}>
                  {agentId[0].toUpperCase()}
                </div>
                <div>
                  <div style={{ fontFamily: 'var(--mono)', fontSize: 13, fontWeight: 600, color: 'var(--text-0)' }}>
                    {agentId}
                  </div>
                  <div style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--text-3)' }}>
                    {agent.status} · iter {agent.iteration} · {agent.total_tokens.toLocaleString()} tokens
                  </div>
                </div>
              </div>
            )}

            {agentEvents.length === 0 ? (
              <div className="empty-state" style={{ height: 'auto', paddingTop: 32 }}>
                <div>No events for this agent yet</div>
              </div>
            ) : (
              agentEvents.map((ev, i) => {
                const cat = EVENT_CATEGORIES[ev.event_type] ?? { icon: '•', color: 'var(--text-2)', label: ev.event_type }
                const lineColor = ev.event_type.startsWith('tool.') ? 'var(--cyan)' :
                  ev.event_type.startsWith('chain.') ? 'var(--purple)' :
                  ev.event_type.startsWith('memory.') ? 'var(--rose)' :
                  'var(--border)'

                return (
                  <div className="worklog-entry" key={ev.id ?? i}>
                    <div className="worklog-time">{formatTime(ev.timestamp)}</div>
                    <div className="worklog-line" style={{ background: lineColor }} />
                    <div className="worklog-content">
                      {ev.event_type === 'tool.called' && (
                        <>
                          <span className="tool-call">
                            {String(ev.payload.tool_name ?? '')}({formatArgs(ev.payload.args)})
                          </span>
                        </>
                      )}
                      {ev.event_type === 'tool.result' && (
                        <>
                          {String(ev.payload.tool_name ?? '')} →{' '}
                          {ev.payload.success ? (
                            <span style={{ color: 'var(--green)' }}>ok</span>
                          ) : (
                            <span style={{ color: 'var(--red)' }}>error: {String(ev.payload.error ?? '')}</span>
                          )}
                          {ev.payload.duration_ms && ` (${Math.round(ev.payload.duration_ms as number)}ms)`}
                        </>
                      )}
                      {ev.event_type === 'chain.delegated' && (
                        <span style={{ color: 'var(--purple)' }}>
                          ← delegated from {String(ev.payload.from_agent ?? '?')}
                        </span>
                      )}
                      {ev.event_type === 'agent.started' && 'Agent started'}
                      {ev.event_type === 'agent.completed' && (
                        <span style={{ color: 'var(--green)' }}>Agent completed</span>
                      )}
                      {ev.event_type === 'agent.iteration' && `Iteration ${ev.payload.iteration ?? ''}`}
                      {!['tool.called', 'tool.result', 'chain.delegated', 'agent.started', 'agent.completed', 'agent.iteration'].includes(ev.event_type) && (
                        <>{cat.label}: {JSON.stringify(ev.payload).slice(0, 80)}</>
                      )}
                    </div>
                  </div>
                )
              })
            )}
          </>
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

function formatArgs(args: unknown): string {
  if (!args) return ''
  const s = JSON.stringify(args)
  return s.length > 50 ? s.slice(0, 50) + '…' : s
}
