import { create } from 'zustand'
import type { AgentState, ChainState, ConfigInfo, CronJobState, HeartbeatState, NanobotEvent } from '../types'

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
        }
      } else if (et === 'agent.iteration' && aid && agents[aid]) {
        agents[aid] = { ...agents[aid], iteration: agents[aid].iteration + 1 }
      } else if (et === 'agent.completed' && aid && agents[aid]) {
        agents[aid] = { ...agents[aid], status: 'idle', completed_at: event.timestamp }
      } else if (et === 'chain.delegated' && cid && !chains[cid]) {
        chains[cid] = { chain_id: cid, status: 'active', started_at: event.timestamp, completed_at: null }
      } else if (et === 'chain.awaiting_approval' && cid && chains[cid]) {
        chains[cid] = { ...chains[cid], status: 'awaiting_approval' }
      } else if (et === 'chain.completed' && cid && chains[cid]) {
        chains[cid] = { ...chains[cid], status: 'completed', completed_at: event.timestamp }
      } else if (et === 'usage.tracked' && aid && agents[aid]) {
        const p = event.payload as Record<string, number>
        agents[aid] = {
          ...agents[aid],
          total_tokens: agents[aid].total_tokens + (p.input_tokens ?? 0) + (p.output_tokens ?? 0),
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

      return { events, agents, chains, heartbeat, cronJobs }
    }),

  setConfig: (config) => set({ config }),
  setSelectedAgent: (id) => set({ selectedAgent: id }),
  setSelectedChain: (id) => set({ selectedChain: id }),
  setEventFilter: (filter) => set({ eventFilter: filter }),
}))
