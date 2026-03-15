import { create } from 'zustand'
import type { AgentState, ChainState, ConfigInfo, CronJobState, HeartbeatState, NanobotEvent, TaskBoardItem } from '../types'

const MAX_EVENTS = 200

interface DashboardState {
  // Connection
  connected: boolean
  setConnected: (v: boolean) => void

  // Derived state
  agents: Record<string, AgentState>
  chains: Record<string, ChainState>
  heartbeat: HeartbeatState | null
  cronJobs: Record<string, CronJobState>
  taskBoard: Record<string, TaskBoardItem>
  config: ConfigInfo | null

  // Event feed
  events: NanobotEvent[]

  // UI state
  selectedAgent: string | null
  selectedChain: string | null
  eventFilter: string

  // Actions
  setSnapshot: (data: {
    agents: Record<string, AgentState>
    chains: Record<string, ChainState>
    heartbeat: HeartbeatState | null
    cron_jobs: Record<string, CronJobState>
    task_board: Record<string, TaskBoardItem>
    recent_events: NanobotEvent[]
  }) => void
  addEvent: (event: NanobotEvent) => void
  setConfig: (config: ConfigInfo) => void
  setSelectedAgent: (id: string | null) => void
  setSelectedChain: (id: string | null) => void
  setEventFilter: (filter: string) => void
}

export const useDashboardStore = create<DashboardState>((set, get) => ({
  connected: false,
  setConnected: (v) => set({ connected: v }),

  agents: {},
  chains: {},
  heartbeat: null,
  cronJobs: {},
  taskBoard: {},
  config: null,

  events: [],

  selectedAgent: null,
  selectedChain: null,
  eventFilter: '',

  setSnapshot: (data) =>
    set({
      agents: data.agents,
      chains: data.chains,
      heartbeat: data.heartbeat,
      cronJobs: data.cron_jobs,
      taskBoard: data.task_board ?? {},
      events: data.recent_events.slice(0, MAX_EVENTS),
    }),

  addEvent: (event) =>
    set((state) => {
      const events = [event, ...state.events].slice(0, MAX_EVENTS)

      // Update derived state from event
      const agents = { ...state.agents }
      const chains = { ...state.chains }
      let heartbeat = state.heartbeat
      const cronJobs = { ...state.cronJobs }
      const taskBoard = { ...state.taskBoard }

      const et = event.event_type
      const aid = event.agent_id
      const cid = event.chain_id

      if (et === 'agent.started' && aid) {
        agents[aid] = {
          agent_id: aid,
          status: 'running',
          started_at: event.timestamp,
          completed_at: null,
          chain_id: cid,
          iteration: 0,
          total_tokens: agents[aid]?.total_tokens ?? 0,
          total_cost_usd: agents[aid]?.total_cost_usd ?? 0,
        }
        const preview = (event.payload.preview as string) ?? `Agent ${aid} processing`
        taskBoard[`agent:${aid}`] = {
          task_id: `agent:${aid}`, title: preview.slice(0, 120),
          agent_id: aid, chain_id: cid, status: 'active',
          started_at: event.timestamp, completed_at: null,
        }
      } else if (et === 'agent.iteration' && aid && agents[aid]) {
        agents[aid] = { ...agents[aid], iteration: agents[aid].iteration + 1 }
      } else if (et === 'agent.completed' && aid && agents[aid]) {
        agents[aid] = { ...agents[aid], status: 'idle', completed_at: event.timestamp }
        const tk = `agent:${aid}`
        if (taskBoard[tk]) taskBoard[tk] = { ...taskBoard[tk], status: 'done', completed_at: event.timestamp }
      } else if (et === 'chain.delegated' && cid && !chains[cid]) {
        chains[cid] = { chain_id: cid, status: 'active', started_at: event.timestamp, completed_at: null }
        taskBoard[`chain:${cid}`] = {
          task_id: `chain:${cid}`, title: `Chain #${cid}`,
          agent_id: aid, chain_id: cid, status: 'active',
          started_at: event.timestamp, completed_at: null,
        }
      } else if (et === 'chain.awaiting_approval' && cid && chains[cid]) {
        chains[cid] = { ...chains[cid], status: 'awaiting_approval' }
        const tk = `chain:${cid}`
        if (taskBoard[tk]) taskBoard[tk] = { ...taskBoard[tk], status: 'pending', title: `Awaiting approval: #${cid}` }
      } else if (et === 'chain.completed' && cid && chains[cid]) {
        chains[cid] = { ...chains[cid], status: 'completed', completed_at: event.timestamp }
        const tk = `chain:${cid}`
        if (taskBoard[tk]) taskBoard[tk] = { ...taskBoard[tk], status: 'done', completed_at: event.timestamp }
      } else if (et === 'usage.tracked' && aid && agents[aid]) {
        const p = event.payload as Record<string, number>
        const costDelta = p.cost_usd ?? 0
        agents[aid] = {
          ...agents[aid],
          total_tokens: agents[aid].total_tokens + (p.input_tokens ?? 0) + (p.output_tokens ?? 0),
          total_cost_usd: agents[aid].total_cost_usd + costDelta,
        }
      } else if (et === 'heartbeat.checked') {
        heartbeat = {
          action: (event.payload.action as string) ?? 'skip',
          checked_at: event.timestamp,
          had_content: (event.payload.had_content as boolean) ?? true,
          error: (event.payload.error as string) ?? null,
        }
      } else if (et === 'cron.triggered') {
        const p = event.payload as Record<string, string>
        const jid = p.job_id ?? ''
        if (jid) {
          cronJobs[jid] = {
            job_id: jid,
            job_name: p.job_name ?? '',
            last_triggered_at: event.timestamp,
            last_status: p.status ?? 'ok',
            last_error: p.error ?? null,
          }
        }
      }

      return { events, agents, chains, heartbeat, cronJobs, taskBoard }
    }),

  setConfig: (config) => set({ config }),
  setSelectedAgent: (id) => set({ selectedAgent: id }),
  setSelectedChain: (id) => set({ selectedChain: id }),
  setEventFilter: (filter) => set({ eventFilter: filter }),
}))
