// ── Event system types ────────────────────────────────────

export interface NanobotEvent {
  id: number | null
  timestamp: string
  event_type: string
  agent_id: string | null
  chain_id: string | null
  payload: Record<string, unknown>
}

// ── Derived state types ───────────────────────────────────

export interface AgentState {
  agent_id: string
  status: 'running' | 'idle' | 'pending'
  started_at: string | null
  completed_at: string | null
  chain_id: string | null
  iteration: number
  total_tokens: number
  total_cost_usd: number
}

export interface ChainState {
  chain_id: string
  status: 'active' | 'awaiting_approval' | 'completed'
  started_at: string | null
  completed_at: string | null
}

export interface HeartbeatState {
  action: string
  checked_at: string | null
  had_content: boolean
  error: string | null
}

export interface CronJobState {
  job_id: string
  job_name: string
  last_triggered_at: string | null
  last_status: string
  last_error: string | null
}

export interface TaskBoardItem {
  task_id: string
  title: string
  agent_id: string | null
  chain_id: string | null
  status: 'pending' | 'active' | 'done'
  started_at: string | null
  completed_at: string | null
}

export interface ConfigInfo {
  default_model: string
  agents: Record<string, { model: string | null; system_prompt: string }>
  teams: Record<string, { agents: string[]; mode: string; approval: string }>
}

// ── WebSocket message types ───────────────────────────────

export interface SnapshotMessage {
  type: 'snapshot'
  data: {
    agents: Record<string, AgentState>
    chains: Record<string, ChainState>
    heartbeat: HeartbeatState | null
    cron_jobs: Record<string, CronJobState>
    task_board: Record<string, TaskBoardItem>
    recent_events: NanobotEvent[]
  }
}

export interface EventMessage {
  type: 'event'
  data: NanobotEvent
}

export type WSMessage = SnapshotMessage | EventMessage

// ── UI helpers ────────────────────────────────────────────

export const EVENT_CATEGORIES: Record<string, { icon: string; color: string; label: string }> = {
  'agent.started':           { icon: '▶', color: 'var(--cyan)',   label: 'Agent Started' },
  'agent.iteration':         { icon: '↻', color: 'var(--cyan)',   label: 'Iteration' },
  'agent.completed':         { icon: '✓', color: 'var(--green)',  label: 'Agent Completed' },
  'tool.called':             { icon: '🔧', color: 'var(--cyan)',   label: 'Tool Called' },
  'tool.result':             { icon: '✓', color: 'var(--green)',  label: 'Tool Result' },
  'message.routed':          { icon: '→', color: 'var(--purple)', label: 'Message Routed' },
  'chain.delegated':         { icon: '→', color: 'var(--purple)', label: 'Chain Delegated' },
  'chain.awaiting_approval': { icon: '⏸', color: 'var(--yellow)', label: 'Awaiting Approval' },
  'chain.approved':          { icon: '✓', color: 'var(--green)',  label: 'Chain Approved' },
  'chain.completed':         { icon: '✓', color: 'var(--green)',  label: 'Chain Completed' },
  'chain.checkpoint':        { icon: '📌', color: 'var(--orange)', label: 'Checkpoint' },
  'memory.written':          { icon: '🧠', color: 'var(--rose)',   label: 'Memory Written' },
  'heartbeat.checked':       { icon: '💓', color: 'var(--text-2)', label: 'Heartbeat' },
  'cron.triggered':          { icon: '⏰', color: 'var(--orange)', label: 'Cron Triggered' },
  'usage.tracked':           { icon: '📊', color: 'var(--text-2)', label: 'Usage Tracked' },
}

export const AGENT_COLORS = [
  { bg: 'var(--cyan-dim)', fg: 'var(--cyan)' },
  { bg: 'var(--purple-dim)', fg: 'var(--purple)' },
  { bg: 'var(--green-dim)', fg: 'var(--green)' },
  { bg: 'var(--rose-dim)', fg: 'var(--rose)' },
  { bg: 'var(--orange-dim)', fg: 'var(--orange)' },
  { bg: 'var(--yellow-dim)', fg: 'var(--yellow)' },
]

export function getAgentColor(agentId: string): { bg: string; fg: string } {
  let hash = 0
  for (let i = 0; i < agentId.length; i++) {
    hash = ((hash << 5) - hash + agentId.charCodeAt(i)) | 0
  }
  return AGENT_COLORS[Math.abs(hash) % AGENT_COLORS.length]
}
