import { useState, useEffect } from 'react'
import { useDashboardStore } from '../hooks/useDashboardStore'

const APPROVAL_MODES = ['auto', 'confirm', 'first_only']

interface TeamFormData {
  teamName: string
  leader: string
  agents: string[]
  approvalMode: string
}

const EMPTY_FORM: TeamFormData = {
  teamName: '',
  leader: '',
  agents: [],
  approvalMode: 'auto',
}

export function TeamForm() {
  const [form, setForm] = useState<TeamFormData>({ ...EMPTY_FORM })
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [availableAgents, setAvailableAgents] = useState<string[]>([])
  const [existingTeams, setExistingTeams] = useState<string[]>([])
  const [editMode, setEditMode] = useState(false)
  const setActiveTab = useDashboardStore((s) => s.setActiveTab)

  // Load existing agents and teams
  useEffect(() => {
    fetch('/api/config/full')
      .then((r) => r.json())
      .then((data) => {
        const agents = data.agents?.agents || {}
        setAvailableAgents(Object.keys(agents))
        const teams = data.agents?.teams || {}
        setExistingTeams(Object.keys(teams))
      })
      .catch(() => {})
  }, [])

  const loadTeam = (teamName: string) => {
    if (!teamName) {
      setForm({ ...EMPTY_FORM })
      setEditMode(false)
      return
    }
    fetch('/api/config/full')
      .then((r) => r.json())
      .then((data) => {
        const team = data.agents?.teams?.[teamName]
        if (team) {
          setForm({
            teamName,
            leader: team.leader || '',
            agents: team.agents || [],
            approvalMode: team.approvalMode || 'auto',
          })
          setEditMode(true)
        }
      })
      .catch(() => {})
  }

  const set = (field: keyof TeamFormData, value: any) => {
    setForm((prev) => ({ ...prev, [field]: value }))
  }

  const toggleAgent = (agentId: string) => {
    setForm((prev) => ({
      ...prev,
      agents: prev.agents.includes(agentId)
        ? prev.agents.filter((a) => a !== agentId)
        : [...prev.agents, agentId],
    }))
  }

  const handleSubmit = async () => {
    if (!form.teamName.trim()) {
      setToast({ type: 'error', text: 'Team name is required' })
      return
    }
    if (!form.leader.trim()) {
      setToast({ type: 'error', text: 'Team leader is required' })
      return
    }

    setSaving(true)
    setToast(null)

    const payload: Record<string, any> = {
      teamName: form.teamName.trim(),
      leader: form.leader,
      agents: form.agents,
      approvalMode: form.approvalMode,
    }

    try {
      const res = await fetch('/api/config/teams', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await res.json()
      if (data.ok) {
        setToast({ type: 'success', text: `Team "${form.teamName}" ${editMode ? 'updated' : 'created'}. Restart to apply.` })
        if (!editMode) {
          setExistingTeams((prev) => [...prev, form.teamName])
        }
      } else {
        setToast({ type: 'error', text: data.error || 'Failed to save team' })
      }
    } catch {
      setToast({ type: 'error', text: 'Network error' })
    }
    setSaving(false)
  }

  const handleDelete = async () => {
    if (!form.teamName || !editMode) return
    if (!confirm(`Remove team "${form.teamName}" from config?`)) return

    try {
      const res = await fetch(`/api/config/teams/${encodeURIComponent(form.teamName)}`, {
        method: 'DELETE',
      })
      const data = await res.json()
      if (data.ok) {
        setToast({ type: 'success', text: `Team "${form.teamName}" removed.` })
        setExistingTeams((prev) => prev.filter((t) => t !== form.teamName))
        setForm({ ...EMPTY_FORM })
        setEditMode(false)
      } else {
        setToast({ type: 'error', text: data.error || 'Delete failed' })
      }
    } catch {
      setToast({ type: 'error', text: 'Network error' })
    }
  }

  return (
    <div className="config-editor">
      {toast && (
        <div className={`config-toast ${toast.type}`}>
          {toast.type === 'success' ? '✓' : '⚠'} {toast.text}
          <button className="toast-close" onClick={() => setToast(null)}>×</button>
        </div>
      )}

      <div className="config-header">
        <h2>{editMode ? 'Edit Team' : 'Add Team'}</h2>
      </div>

      {/* Edit existing team selector */}
      {existingTeams.length > 0 && (
        <div className="form-banner">
          <span>Edit existing:</span>
          <select
            className="cfg-select"
            value={editMode ? form.teamName : ''}
            onChange={(e) => loadTeam(e.target.value)}
          >
            <option value="">New team…</option>
            {existingTeams.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        </div>
      )}

      <div className="form-body">
        <div className="cfg-field">
          <label className="cfg-label">
            Team Name <span className="cfg-required">*</span>
            <span className="cfg-hint">Unique identifier (e.g. dev-team, research-squad)</span>
          </label>
          <input
            className="cfg-input"
            value={form.teamName}
            onChange={(e) => set('teamName', e.target.value.replace(/[^a-zA-Z0-9_-]/g, ''))}
            placeholder="my-team"
            disabled={editMode}
          />
        </div>

        <div className="cfg-field">
          <label className="cfg-label">
            Leader <span className="cfg-required">*</span>
            <span className="cfg-hint">Agent ID of the team leader (orchestrates the chain)</span>
          </label>
          {availableAgents.length > 0 ? (
            <select
              className="cfg-select"
              value={form.leader}
              onChange={(e) => set('leader', e.target.value)}
            >
              <option value="">Select leader…</option>
              {availableAgents.map((id) => (
                <option key={id} value={id}>{id}</option>
              ))}
            </select>
          ) : (
            <input
              className="cfg-input"
              value={form.leader}
              onChange={(e) => set('leader', e.target.value)}
              placeholder="leader-agent-id"
            />
          )}
        </div>

        <div className="cfg-field">
          <label className="cfg-label">
            Members
            <span className="cfg-hint">Select agents to include in this team</span>
          </label>
          {availableAgents.length > 0 ? (
            <div className="skill-tags">
              {availableAgents.map((id) => (
                <button
                  key={id}
                  className={`skill-tag ${form.agents.includes(id) ? 'active' : ''}`}
                  onClick={() => toggleAgent(id)}
                  type="button"
                >
                  {id}
                </button>
              ))}
            </div>
          ) : (
            <div className="cfg-hint" style={{ padding: '8px 0' }}>
              No agents configured yet. <button className="btn-link" onClick={() => setActiveTab('agent')}>Add an agent first</button>.
            </div>
          )}
          {form.agents.length > 0 && (
            <div className="team-members-list">
              {form.agents.map((id) => (
                <span key={id} className="team-member-tag">
                  {id}
                  <button className="tag-remove" onClick={() => toggleAgent(id)}>×</button>
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="cfg-field">
          <label className="cfg-label">
            Approval Mode
            <span className="cfg-hint">How delegation between agents is approved</span>
          </label>
          <select
            className="cfg-select"
            value={form.approvalMode}
            onChange={(e) => set('approvalMode', e.target.value)}
          >
            {APPROVAL_MODES.map((mode) => (
              <option key={mode} value={mode}>{mode}</option>
            ))}
          </select>
          <div className="approval-descriptions">
            {form.approvalMode === 'auto' && (
              <span className="cfg-hint">All delegations are auto-approved</span>
            )}
            {form.approvalMode === 'confirm' && (
              <span className="cfg-hint">Every delegation requires manual approval</span>
            )}
            {form.approvalMode === 'first_only' && (
              <span className="cfg-hint">Only the first delegation requires approval; subsequent are auto-approved</span>
            )}
          </div>
        </div>
      </div>

      <div className="config-footer">
        <button className="btn btn-primary" onClick={handleSubmit} disabled={saving || !form.teamName.trim() || !form.leader.trim()}>
          {saving ? 'Saving…' : editMode ? 'Update Team' : 'Create Team'}
        </button>
        {editMode && (
          <button className="btn btn-danger" onClick={handleDelete}>
            Remove Team
          </button>
        )}
        <span className="config-hint">Changes require a restart to take effect.</span>
      </div>
    </div>
  )
}
