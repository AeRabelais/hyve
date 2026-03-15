import { useDashboardStore } from '../hooks/useDashboardStore'
import { getAgentColor } from '../types'

export function Sidebar() {
  const agents = useDashboardStore((s) => s.agents)
  const chains = useDashboardStore((s) => s.chains)
  const config = useDashboardStore((s) => s.config)
  const selectedAgent = useDashboardStore((s) => s.selectedAgent)
  const selectedChain = useDashboardStore((s) => s.selectedChain)
  const setSelectedAgent = useDashboardStore((s) => s.setSelectedAgent)
  const setSelectedChain = useDashboardStore((s) => s.setSelectedChain)

  const agentList = Object.values(agents)
  const chainList = Object.values(chains)
  const teams = config?.teams ?? {}

  const statusIndicatorColor = (status: string) => {
    if (status === 'running') return 'var(--cyan)'
    if (status === 'pending') return 'var(--yellow)'
    return 'var(--text-3)'
  }

  const statusText = (a: typeof agentList[0]) => {
    if (a.status === 'running' && a.chain_id) return `processing #${a.chain_id}`
    if (a.status === 'running') return `running · iter ${a.iteration}`
    if (a.completed_at) {
      const ago = timeSince(a.completed_at)
      return `idle · ${ago}`
    }
    return a.status
  }

  return (
    <div className="sidebar">
      <div className="sidebar-section">
        <div className="sidebar-heading">Agents</div>
        {agentList.length === 0 && (
          <div style={{ color: 'var(--text-3)', fontSize: 11, fontFamily: 'var(--mono)' }}>
            No agents active
          </div>
        )}
        {agentList.map((agent) => {
          const color = getAgentColor(agent.agent_id)
          return (
            <div
              key={agent.agent_id}
              className={`agent-item ${selectedAgent === agent.agent_id ? 'selected' : ''}`}
              onClick={() => setSelectedAgent(selectedAgent === agent.agent_id ? null : agent.agent_id)}
            >
              <div className="agent-avatar" style={{ background: color.bg, color: color.fg }}>
                {agent.agent_id[0].toUpperCase()}
              </div>
              <div className="agent-info">
                <div className="agent-name">{agent.agent_id}</div>
                <div className="agent-status-text">{statusText(agent)}</div>
              </div>
              <div
                className="agent-indicator"
                style={{ background: statusIndicatorColor(agent.status) }}
              />
            </div>
          )
        })}
      </div>

      <div className="sidebar-section">
        <div className="sidebar-heading">Active Chains</div>
        {chainList.length === 0 && (
          <div style={{ color: 'var(--text-3)', fontSize: 11, fontFamily: 'var(--mono)' }}>
            No chains
          </div>
        )}
        {chainList.map((chain) => (
          <div
            key={chain.chain_id}
            className={`chain-item ${selectedChain === chain.chain_id ? 'selected' : ''}`}
            onClick={() => setSelectedChain(selectedChain === chain.chain_id ? null : chain.chain_id)}
          >
            <div className="chain-name">
              <span style={{ color: 'var(--orange)' }}>#{chain.chain_id}</span>
              <span className={`chain-tag ${chain.status}`}>{formatStatus(chain.status)}</span>
            </div>
            <div className="chain-desc">
              {chain.started_at ? `started ${timeSince(chain.started_at)} ago` : ''}
            </div>
          </div>
        ))}
      </div>

      {Object.keys(teams).length > 0 && (
        <div className="sidebar-section">
          <div className="sidebar-heading">Teams</div>
          {Object.entries(teams).map(([name, team]) => (
            <div key={name} className="agent-item">
              <div
                className="agent-avatar"
                style={{ background: 'var(--orange-dim)', color: 'var(--orange)', fontSize: 10 }}
              >
                {name.slice(0, 3).toUpperCase()}
              </div>
              <div className="agent-info">
                <div className="agent-name">{name}</div>
                <div className="agent-status-text">{team.agents.join(', ')}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function formatStatus(status: string): string {
  return status.replace(/_/g, ' ')
}

function timeSince(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h`
}
