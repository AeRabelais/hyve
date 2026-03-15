import { useState, useEffect } from 'react'
import { useDashboardStore } from '../hooks/useDashboardStore'

const REASONING_OPTIONS = ['', 'low', 'medium', 'high']

const KNOWN_SKILLS = [
  'clawhub', 'cron', 'github', 'memory', 'skill-creator', 'summarize', 'tmux', 'weather',
]

interface AgentFormData {
  agentId: string
  model: string
  workspace: string
  systemPrompt: string
  tools: string
  skills: string[]
  maxIterations: string
  temperature: string
  maxTokens: string
  memoryWindow: string
  reasoningEffort: string
}

const EMPTY_FORM: AgentFormData = {
  agentId: '',
  model: '',
  workspace: '',
  systemPrompt: '',
  tools: '',
  skills: [],
  maxIterations: '',
  temperature: '',
  maxTokens: '',
  memoryWindow: '',
  reasoningEffort: '',
}

export function AgentForm() {
  const [form, setForm] = useState<AgentFormData>({ ...EMPTY_FORM })
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [existingAgents, setExistingAgents] = useState<string[]>([])
  const [editMode, setEditMode] = useState(false)
  const setActiveTab = useDashboardStore((s) => s.setActiveTab)

  // Load existing agents for the dropdown
  useEffect(() => {
    fetch('/api/config/full')
      .then((r) => r.json())
      .then((data) => {
        const agents = data.agents?.agents || {}
        setExistingAgents(Object.keys(agents))
      })
      .catch(() => {})
  }, [])

  const loadAgent = (agentId: string) => {
    if (!agentId) {
      setForm({ ...EMPTY_FORM })
      setEditMode(false)
      return
    }
    fetch('/api/config/full')
      .then((r) => r.json())
      .then((data) => {
        const agent = data.agents?.agents?.[agentId]
        if (agent) {
          setForm({
            agentId,
            model: agent.model || '',
            workspace: agent.workspace || '',
            systemPrompt: agent.systemPrompt || '',
            tools: (agent.tools || []).join(', '),
            skills: agent.skills || [],
            maxIterations: agent.maxIterations != null ? String(agent.maxIterations) : '',
            temperature: agent.temperature != null ? String(agent.temperature) : '',
            maxTokens: agent.maxTokens != null ? String(agent.maxTokens) : '',
            memoryWindow: agent.memoryWindow != null ? String(agent.memoryWindow) : '',
            reasoningEffort: agent.reasoningEffort || '',
          })
          setEditMode(true)
        }
      })
      .catch(() => {})
  }

  const set = (field: keyof AgentFormData, value: any) => {
    setForm((prev) => ({ ...prev, [field]: value }))
  }

  const handleSubmit = async () => {
    if (!form.agentId.trim()) {
      setToast({ type: 'error', text: 'Agent ID is required' })
      return
    }

    setSaving(true)
    setToast(null)

    // Build the payload
    const payload: Record<string, any> = { agentId: form.agentId.trim() }
    if (form.model) payload.model = form.model
    if (form.workspace) payload.workspace = form.workspace
    if (form.systemPrompt) payload.systemPrompt = form.systemPrompt
    if (form.tools.trim()) {
      payload.tools = form.tools.split(',').map((s) => s.trim()).filter(Boolean)
    }
    if (form.skills.length > 0) payload.skills = form.skills
    if (form.maxIterations) payload.maxIterations = Number(form.maxIterations)
    if (form.temperature) payload.temperature = Number(form.temperature)
    if (form.maxTokens) payload.maxTokens = Number(form.maxTokens)
    if (form.memoryWindow) payload.memoryWindow = Number(form.memoryWindow)
    if (form.reasoningEffort) payload.reasoningEffort = form.reasoningEffort

    try {
      const res = await fetch('/api/config/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await res.json()
      if (data.ok) {
        setToast({ type: 'success', text: `Agent "${form.agentId}" ${editMode ? 'updated' : 'created'}. Restart to apply.` })
        if (!editMode) {
          setExistingAgents((prev) => [...prev, form.agentId])
        }
      } else {
        setToast({ type: 'error', text: data.error || 'Failed to save agent' })
      }
    } catch {
      setToast({ type: 'error', text: 'Network error' })
    }
    setSaving(false)
  }

  const handleDelete = async () => {
    if (!form.agentId || !editMode) return
    if (!confirm(`Remove agent "${form.agentId}" from config?`)) return

    try {
      const res = await fetch(`/api/config/agents/${encodeURIComponent(form.agentId)}`, {
        method: 'DELETE',
      })
      const data = await res.json()
      if (data.ok) {
        setToast({ type: 'success', text: `Agent "${form.agentId}" removed.` })
        setExistingAgents((prev) => prev.filter((a) => a !== form.agentId))
        setForm({ ...EMPTY_FORM })
        setEditMode(false)
      } else {
        setToast({ type: 'error', text: data.error || 'Delete failed' })
      }
    } catch {
      setToast({ type: 'error', text: 'Network error' })
    }
  }

  const toggleSkill = (skill: string) => {
    setForm((prev) => ({
      ...prev,
      skills: prev.skills.includes(skill)
        ? prev.skills.filter((s) => s !== skill)
        : [...prev.skills, skill],
    }))
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
        <h2>{editMode ? 'Edit Agent' : 'Add Agent'}</h2>
      </div>

      {/* Edit existing agent selector */}
      {existingAgents.length > 0 && (
        <div className="form-banner">
          <span>Edit existing:</span>
          <select
            className="cfg-select"
            value={editMode ? form.agentId : ''}
            onChange={(e) => loadAgent(e.target.value)}
          >
            <option value="">New agent…</option>
            {existingAgents.map((id) => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
        </div>
      )}

      <div className="form-body">
        <div className="cfg-field">
          <label className="cfg-label">
            Agent ID <span className="cfg-required">*</span>
            <span className="cfg-hint">Unique identifier (e.g. coder, researcher, writer)</span>
          </label>
          <input
            className="cfg-input"
            value={form.agentId}
            onChange={(e) => set('agentId', e.target.value.replace(/[^a-zA-Z0-9_-]/g, ''))}
            placeholder="my-agent"
            disabled={editMode}
          />
        </div>

        <div className="cfg-field">
          <label className="cfg-label">
            Model
            <span className="cfg-hint">Leave empty to use default model</span>
          </label>
          <input
            className="cfg-input"
            value={form.model}
            onChange={(e) => set('model', e.target.value)}
            placeholder="(uses default)"
          />
        </div>

        <div className="cfg-field">
          <label className="cfg-label">
            Workspace
            <span className="cfg-hint">Custom workspace path (defaults to ~/.nanobot/workspace/&lt;agent_id&gt;)</span>
          </label>
          <input
            className="cfg-input"
            value={form.workspace}
            onChange={(e) => set('workspace', e.target.value)}
            placeholder="(auto-created)"
          />
        </div>

        <div className="cfg-field">
          <label className="cfg-label">
            System Prompt
            <span className="cfg-hint">Custom system prompt override</span>
          </label>
          <textarea
            className="cfg-textarea"
            rows={4}
            value={form.systemPrompt}
            onChange={(e) => set('systemPrompt', e.target.value)}
            placeholder="You are a helpful assistant that..."
          />
        </div>

        <div className="cfg-field">
          <label className="cfg-label">
            Tools
            <span className="cfg-hint">Comma-separated allowlist (empty = all tools)</span>
          </label>
          <input
            className="cfg-input"
            value={form.tools}
            onChange={(e) => set('tools', e.target.value)}
            placeholder="message, shell, read_file, write_file"
          />
        </div>

        <div className="cfg-field">
          <label className="cfg-label">Skills</label>
          <div className="skill-tags">
            {KNOWN_SKILLS.map((skill) => (
              <button
                key={skill}
                className={`skill-tag ${form.skills.includes(skill) ? 'active' : ''}`}
                onClick={() => toggleSkill(skill)}
                type="button"
              >
                {skill}
              </button>
            ))}
          </div>
        </div>

        <div className="field-row">
          <div className="cfg-field">
            <label className="cfg-label">Max Iterations</label>
            <input
              className="cfg-input cfg-input-sm"
              type="number"
              value={form.maxIterations}
              onChange={(e) => set('maxIterations', e.target.value)}
              placeholder="(default)"
            />
          </div>
          <div className="cfg-field">
            <label className="cfg-label">Temperature</label>
            <input
              className="cfg-input cfg-input-sm"
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={form.temperature}
              onChange={(e) => set('temperature', e.target.value)}
              placeholder="(default)"
            />
          </div>
          <div className="cfg-field">
            <label className="cfg-label">Max Tokens</label>
            <input
              className="cfg-input cfg-input-sm"
              type="number"
              value={form.maxTokens}
              onChange={(e) => set('maxTokens', e.target.value)}
              placeholder="(default)"
            />
          </div>
        </div>

        <div className="field-row">
          <div className="cfg-field">
            <label className="cfg-label">Memory Window</label>
            <input
              className="cfg-input cfg-input-sm"
              type="number"
              value={form.memoryWindow}
              onChange={(e) => set('memoryWindow', e.target.value)}
              placeholder="(default)"
            />
          </div>
          <div className="cfg-field">
            <label className="cfg-label">Reasoning Effort</label>
            <select
              className="cfg-select"
              value={form.reasoningEffort}
              onChange={(e) => set('reasoningEffort', e.target.value)}
            >
              {REASONING_OPTIONS.map((v) => (
                <option key={v} value={v}>{v || '(none)'}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="config-footer">
        <button className="btn btn-primary" onClick={handleSubmit} disabled={saving || !form.agentId.trim()}>
          {saving ? 'Saving…' : editMode ? 'Update Agent' : 'Create Agent'}
        </button>
        {editMode && (
          <button className="btn btn-danger" onClick={handleDelete}>
            Remove Agent
          </button>
        )}
        <span className="config-hint">Changes require a restart to take effect.</span>
      </div>
    </div>
  )
}
