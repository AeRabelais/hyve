import { useDashboardStore } from '../hooks/useDashboardStore'
import { getAgentColor } from '../types'

export function ChainVisualizer() {
  const chains = useDashboardStore((s) => s.chains)
  const selectedChain = useDashboardStore((s) => s.selectedChain)
  const events = useDashboardStore((s) => s.events)
  const agents = useDashboardStore((s) => s.agents)

  // Pick chain to display: selected or first active
  const chainId =
    selectedChain ??
    Object.values(chains).find((c) => c.status === 'active')?.chain_id ??
    Object.values(chains)[0]?.chain_id

  const chain = chainId ? chains[chainId] : null

  // Build node sequence from chain events
  const chainEvents = chainId
    ? events.filter((e) => e.chain_id === chainId).reverse()
    : []

  const nodeAgents: string[] = []
  const nodeStatuses: Record<string, 'completed' | 'running' | 'awaiting' | 'pending'> = {}

  for (const ev of chainEvents) {
    if (ev.agent_id && !nodeAgents.includes(ev.agent_id)) {
      nodeAgents.push(ev.agent_id)
    }
    if (ev.event_type === 'agent.completed' && ev.agent_id) {
      nodeStatuses[ev.agent_id] = 'completed'
    }
    if (ev.event_type === 'agent.started' && ev.agent_id && !nodeStatuses[ev.agent_id]) {
      nodeStatuses[ev.agent_id] = 'running'
    }
    if (ev.event_type === 'chain.awaiting_approval' && ev.agent_id) {
      nodeStatuses[ev.agent_id] = 'awaiting'
    }
  }

  const elapsed = chain?.started_at ? timeSince(chain.started_at) : '—'
  const statusLabel = chain ? formatChainStatus(chain.status) : '—'

  return (
    <div className="panel">
      <div className="panel-header">
        <span style={{ color: 'var(--orange)' }}>◆</span>
        <div className="panel-title">
          {chainId ? `Chain: #${chainId}` : 'Chain Visualizer'}
        </div>
        {chain && (
          <div
            className="panel-badge"
            style={{
              background: chain.status === 'active' ? 'var(--cyan-dim)' : chain.status === 'awaiting_approval' ? 'var(--yellow-dim)' : 'var(--bg-3)',
              color: chain.status === 'active' ? 'var(--cyan)' : chain.status === 'awaiting_approval' ? 'var(--yellow)' : 'var(--text-2)',
            }}
          >
            {statusLabel} · {elapsed}
          </div>
        )}
      </div>
      <div className="panel-body">
        {!chain ? (
          <div className="empty-state">
            <div className="empty-state-icon">🔗</div>
            <div>No chains active</div>
            <div style={{ fontSize: 10 }}>Chains appear here when agents delegate work</div>
          </div>
        ) : (
          <>
            <div className="chain-flow">
              {nodeAgents.map((agentId, i) => {
                const status = nodeStatuses[agentId] ?? 'pending'
                const color = getAgentColor(agentId)
                const agent = agents[agentId]
                return (
                  <div key={agentId} style={{ display: 'flex', alignItems: 'flex-start' }}>
                    {i > 0 && <div className="chain-arrow">→</div>}
                    <div className="chain-node">
                      <div className={`chain-node-box ${status}`}>
                        <div className="chain-node-icon" style={{ color: color.fg }}>
                          {agentId[0].toUpperCase()}
                        </div>
                        <div className="chain-node-name">{agentId}</div>
                        <div className="chain-node-status">
                          {status === 'completed' && '✓ done'}
                          {status === 'running' && `● iter ${agent?.iteration ?? 0}`}
                          {status === 'awaiting' && '⏸ approval'}
                          {status === 'pending' && 'queued'}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
            <div className="chain-meta">
              <strong>Chain</strong> <span style={{ color: 'var(--orange)' }}>#{chainId}</span>
              {' · '}status <strong>{statusLabel}</strong>
              {' · '}elapsed <strong>{elapsed}</strong>
              <br />
              Agents: {nodeAgents.join(' → ') || 'none yet'}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function formatChainStatus(status: string): string {
  return status.replace(/_/g, ' ')
}

function timeSince(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}
