import { useDashboardStore } from '../hooks/useDashboardStore'
import { getAgentColor } from '../types'
import type { NanobotEvent } from '../types'

interface Task {
  id: string
  title: string
  agentId: string
  chainId: string | null
  status: 'pending' | 'active' | 'done'
  startedAt: string | null
}

export function TaskBoard() {
  const events = useDashboardStore((s) => s.events)
  const agents = useDashboardStore((s) => s.agents)

  // Derive tasks from events
  const tasks = deriveTasks(events, agents)
  const pending = tasks.filter((t) => t.status === 'pending')
  const active = tasks.filter((t) => t.status === 'active')
  const done = tasks.filter((t) => t.status === 'done')

  return (
    <div className="panel">
      <div className="panel-header">
        <span style={{ color: 'var(--yellow)' }}>◆</span>
        <div className="panel-title">Tasks</div>
        <div className="panel-badge" style={{ background: 'var(--bg-3)', color: 'var(--text-2)' }}>
          {tasks.length} total
        </div>
      </div>
      <div className="task-columns">
        <div className="task-col pending">
          <div className="task-col-header">
            Pending <span className="count">{pending.length}</span>
          </div>
          {pending.map((t) => (
            <TaskCard key={t.id} task={t} />
          ))}
        </div>
        <div className="task-col active">
          <div className="task-col-header">
            Active <span className="count">{active.length}</span>
          </div>
          {active.map((t) => (
            <TaskCard key={t.id} task={t} />
          ))}
        </div>
        <div className="task-col done">
          <div className="task-col-header">
            Done <span className="count">{done.length}</span>
          </div>
          {done.map((t) => (
            <TaskCard key={t.id} task={t} opacity={0.6} />
          ))}
        </div>
      </div>
    </div>
  )
}

function TaskCard({ task, opacity = 1 }: { task: Task; opacity?: number }) {
  const color = getAgentColor(task.agentId)
  return (
    <div className="task-card" style={{ opacity }}>
      <div className="task-card-title">{task.title}</div>
      <div className="task-card-meta">
        <span className="task-agent-badge" style={{ background: color.bg, color: color.fg }}>
          {task.agentId}
        </span>
        {task.chainId && <span style={{ color: 'var(--orange)' }}>#{task.chainId}</span>}
        {task.startedAt && <span>{timeSince(task.startedAt)}</span>}
      </div>
    </div>
  )
}

function deriveTasks(events: NanobotEvent[], agents: Record<string, { agent_id: string; status: string; chain_id: string | null }>): Task[] {
  const tasks: Task[] = []
  const seen = new Set<string>()

  // Active agents become active tasks
  for (const agent of Object.values(agents)) {
    const key = `agent:${agent.agent_id}`
    if (agent.status === 'running') {
      seen.add(key)
      // Find the most recent tool call or delegation for context
      const recentEvent = events.find(
        (e) => e.agent_id === agent.agent_id && ['tool.called', 'chain.delegated', 'agent.started'].includes(e.event_type)
      )
      const title = recentEvent?.event_type === 'tool.called'
        ? `${recentEvent.payload.tool_name}()`
        : recentEvent?.event_type === 'chain.delegated'
        ? `Delegated from ${recentEvent.payload.from_agent ?? '?'}`
        : `Agent ${agent.agent_id} processing`
      tasks.push({
        id: key,
        title,
        agentId: agent.agent_id,
        chainId: agent.chain_id,
        status: 'active',
        startedAt: null,
      })
    }
  }

  // Recent completed events become done tasks
  for (const ev of events) {
    if (ev.event_type === 'agent.completed' && ev.agent_id) {
      const key = `done:${ev.agent_id}:${ev.timestamp}`
      if (!seen.has(key) && tasks.filter((t) => t.status === 'done').length < 5) {
        seen.add(key)
        tasks.push({
          id: key,
          title: `${ev.agent_id} completed`,
          agentId: ev.agent_id,
          chainId: ev.chain_id,
          status: 'done',
          startedAt: ev.timestamp,
        })
      }
    }
  }

  // Awaiting approval events become pending
  for (const ev of events) {
    if (ev.event_type === 'chain.awaiting_approval' && ev.chain_id) {
      const key = `pending:${ev.chain_id}`
      if (!seen.has(key)) {
        seen.add(key)
        tasks.push({
          id: key,
          title: `Awaiting approval: #${ev.chain_id}`,
          agentId: ev.agent_id ?? 'system',
          chainId: ev.chain_id,
          status: 'pending',
          startedAt: ev.timestamp,
        })
      }
    }
  }

  return tasks
}

function timeSince(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h`
}
