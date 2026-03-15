import { useDashboardStore } from '../hooks/useDashboardStore'
import { getAgentColor } from '../types'
import type { TaskBoardItem } from '../types'

export function TaskBoard() {
  const taskBoard = useDashboardStore((s) => s.taskBoard)

  const tasks = Object.values(taskBoard)
  const pending = tasks.filter((t) => t.status === 'pending')
  const active = tasks.filter((t) => t.status === 'active')
  const done = tasks.filter((t) => t.status === 'done').slice(0, 8) // Cap done list

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
            <TaskCard key={t.task_id} task={t} />
          ))}
        </div>
        <div className="task-col active">
          <div className="task-col-header">
            Active <span className="count">{active.length}</span>
          </div>
          {active.map((t) => (
            <TaskCard key={t.task_id} task={t} />
          ))}
        </div>
        <div className="task-col done">
          <div className="task-col-header">
            Done <span className="count">{done.length}</span>
          </div>
          {done.map((t) => (
            <TaskCard key={t.task_id} task={t} opacity={0.6} />
          ))}
        </div>
      </div>
    </div>
  )
}

function TaskCard({ task, opacity = 1 }: { task: TaskBoardItem; opacity?: number }) {
  const agentId = task.agent_id ?? 'system'
  const color = getAgentColor(agentId)
  return (
    <div className="task-card" style={{ opacity }}>
      <div className="task-card-title">{task.title}</div>
      <div className="task-card-meta">
        <span className="task-agent-badge" style={{ background: color.bg, color: color.fg }}>
          {agentId}
        </span>
        {task.chain_id && <span style={{ color: 'var(--orange)' }}>#{task.chain_id}</span>}
        {task.started_at && <span>{timeSince(task.started_at)}</span>}
      </div>
    </div>
  )
}

function timeSince(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime()
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  return `${Math.floor(m / 60)}h`
}
