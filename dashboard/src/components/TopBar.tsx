import { useDashboardStore } from '../hooks/useDashboardStore'

export function TopBar() {
  const connected = useDashboardStore((s) => s.connected)
  const agents = useDashboardStore((s) => s.agents)
  const chains = useDashboardStore((s) => s.chains)

  const agentCount = Object.keys(agents).length
  const activeChains = Object.values(chains).filter((c) => c.status !== 'completed').length
  const totalTokens = Object.values(agents).reduce((sum, a) => sum + a.total_tokens, 0)
  const tokensK = totalTokens > 0 ? `${(totalTokens / 1000).toFixed(1)}k` : '0'

  return (
    <div className="topbar">
      <div className="topbar-brand">
        🐈 <span>nanobot</span>
      </div>
      <div className="topbar-status">
        <span>
          <span className={`status-dot ${connected ? 'live' : 'off'}`} />
          {connected ? 'Connected' : 'Disconnected'}
        </span>
        <span>{agentCount} agent{agentCount !== 1 ? 's' : ''} · {activeChains} chain{activeChains !== 1 ? 's' : ''}</span>
        <span>↑ {tokensK} tokens</span>
      </div>
    </div>
  )
}
