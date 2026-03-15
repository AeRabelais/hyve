import { useState, useEffect, useCallback } from 'react'

type Cfg = Record<string, any>

// Known provider names in order
const PROVIDER_NAMES = [
  'anthropic', 'openai', 'openrouter', 'deepseek', 'groq', 'gemini',
  'moonshot', 'minimax', 'zhipu', 'dashscope', 'vllm', 'siliconflow',
  'volcengine', 'aihubmix', 'openai_codex', 'github_copilot', 'custom',
]

const REASONING_OPTIONS = ['', 'low', 'medium', 'high']

export function ConfigEditor() {
  const [config, setConfig] = useState<Cfg | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set(['defaults', 'providers']))

  useEffect(() => {
    fetch('/api/config/full')
      .then((r) => r.json())
      .then((data) => {
        if (data.error) throw new Error(data.error)
        setConfig(data)
        setLoading(false)
      })
      .catch((e) => {
        setToast({ type: 'error', text: `Failed to load: ${e.message}` })
        setLoading(false)
      })
  }, [])

  const toggle = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  const update = useCallback((path: string[], value: any) => {
    setConfig((prev) => {
      if (!prev) return prev
      const next = structuredClone(prev)
      let obj: any = next
      for (let i = 0; i < path.length - 1; i++) {
        if (obj[path[i]] === undefined) obj[path[i]] = {}
        obj = obj[path[i]]
      }
      obj[path[path.length - 1]] = value
      return next
    })
  }, [])

  const save = async () => {
    if (!config) return
    setSaving(true)
    setToast(null)
    try {
      const res = await fetch('/api/config/full', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      const data = await res.json()
      if (data.ok) {
        setToast({ type: 'success', text: 'Configuration saved. Restart to apply changes.' })
      } else {
        setToast({ type: 'error', text: data.error || 'Save failed' })
      }
    } catch {
      setToast({ type: 'error', text: 'Network error' })
    }
    setSaving(false)
  }

  if (loading) {
    return (
      <div className="config-editor">
        <div className="empty-state">
          <div className="empty-state-icon">⚙</div>
          <div>Loading configuration…</div>
        </div>
      </div>
    )
  }

  if (!config) {
    return (
      <div className="config-editor">
        <div className="empty-state">
          <div className="empty-state-icon">⚠</div>
          <div>No configuration loaded</div>
        </div>
      </div>
    )
  }

  const defaults = config.agents?.defaults || {}
  const providers = config.providers || {}
  const mcpServers = config.tools?.mcpServers || {}
  const gateway = config.gateway || {}
  const heartbeat = gateway.heartbeat || {}
  const memory = config.memory || {}
  const tools = config.tools || {}
  const agents = config.agents?.agents || {}
  const teams = config.agents?.teams || {}

  return (
    <div className="config-editor">
      {toast && (
        <div className={`config-toast ${toast.type}`}>
          {toast.type === 'success' ? '✓' : '⚠'} {toast.text}
          <button className="toast-close" onClick={() => setToast(null)}>×</button>
        </div>
      )}

      <div className="config-header">
        <h2>Configuration</h2>
        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save Configuration'}
        </button>
      </div>

      {/* ── Agent Defaults ── */}
      <Section title="Agent Defaults" id="defaults" expanded={expanded} toggle={toggle}>
        <Field label="Default Model" hint="e.g. anthropic/claude-sonnet-4-20250514">
          <input
            className="cfg-input"
            value={defaults.model || ''}
            onChange={(e) => update(['agents', 'defaults', 'model'], e.target.value)}
          />
        </Field>
        <Field label="Workspace" hint="Default workspace path">
          <input
            className="cfg-input"
            value={defaults.workspace || ''}
            onChange={(e) => update(['agents', 'defaults', 'workspace'], e.target.value)}
          />
        </Field>
        <Field label="Provider" hint="auto, anthropic, openrouter, etc.">
          <input
            className="cfg-input"
            value={defaults.provider || 'auto'}
            onChange={(e) => update(['agents', 'defaults', 'provider'], e.target.value)}
          />
        </Field>
        <div className="field-row">
          <Field label="Max Tokens">
            <input
              className="cfg-input cfg-input-sm"
              type="number"
              value={defaults.maxTokens ?? 8192}
              onChange={(e) => update(['agents', 'defaults', 'maxTokens'], Number(e.target.value))}
            />
          </Field>
          <Field label="Temperature">
            <input
              className="cfg-input cfg-input-sm"
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={defaults.temperature ?? 0.1}
              onChange={(e) => update(['agents', 'defaults', 'temperature'], Number(e.target.value))}
            />
          </Field>
          <Field label="Max Tool Iterations">
            <input
              className="cfg-input cfg-input-sm"
              type="number"
              value={defaults.maxToolIterations ?? 40}
              onChange={(e) => update(['agents', 'defaults', 'maxToolIterations'], Number(e.target.value))}
            />
          </Field>
        </div>
        <div className="field-row">
          <Field label="Memory Window">
            <input
              className="cfg-input cfg-input-sm"
              type="number"
              value={defaults.memoryWindow ?? 100}
              onChange={(e) => update(['agents', 'defaults', 'memoryWindow'], Number(e.target.value))}
            />
          </Field>
          <Field label="Reasoning Effort">
            <select
              className="cfg-select"
              value={defaults.reasoningEffort || ''}
              onChange={(e) => update(['agents', 'defaults', 'reasoningEffort'], e.target.value || null)}
            >
              {REASONING_OPTIONS.map((v) => (
                <option key={v} value={v}>{v || '(none)'}</option>
              ))}
            </select>
          </Field>
        </div>
      </Section>

      {/* ── Providers ── */}
      <Section title="Providers" id="providers" expanded={expanded} toggle={toggle}>
        <div className="providers-grid">
          {PROVIDER_NAMES.map((name) => {
            const p = providers[name] || {}
            const hasKey = !!(p.apiKey)
            return (
              <div key={name} className={`provider-row ${hasKey ? 'configured' : ''}`}>
                <div className="provider-name">
                  <span className={`provider-dot ${hasKey ? 'on' : ''}`} />
                  {name}
                </div>
                <PasswordField
                  value={p.apiKey || ''}
                  placeholder="API Key"
                  onChange={(v) => update(['providers', name, 'apiKey'], v)}
                />
                <input
                  className="cfg-input cfg-input-sm"
                  value={p.apiBase || ''}
                  placeholder="API Base (optional)"
                  onChange={(e) => update(['providers', name, 'apiBase'], e.target.value || null)}
                />
              </div>
            )
          })}
        </div>
      </Section>

      {/* ── MCP Servers ── */}
      <Section title="MCP Servers" id="mcp" expanded={expanded} toggle={toggle}>
        <MCPServersEditor
          servers={mcpServers}
          onChange={(servers) => update(['tools', 'mcpServers'], servers)}
        />
      </Section>

      {/* ── Gateway ── */}
      <Section title="Gateway" id="gateway" expanded={expanded} toggle={toggle}>
        <div className="field-row">
          <Field label="Host">
            <input
              className="cfg-input cfg-input-sm"
              value={gateway.host || '0.0.0.0'}
              onChange={(e) => update(['gateway', 'host'], e.target.value)}
            />
          </Field>
          <Field label="Port">
            <input
              className="cfg-input cfg-input-sm"
              type="number"
              value={gateway.port ?? 18790}
              onChange={(e) => update(['gateway', 'port'], Number(e.target.value))}
            />
          </Field>
        </div>
        <div className="field-row">
          <Field label="Heartbeat Enabled">
            <Toggle
              value={heartbeat.enabled !== false}
              onChange={(v) => update(['gateway', 'heartbeat', 'enabled'], v)}
            />
          </Field>
          <Field label="Heartbeat Interval (s)">
            <input
              className="cfg-input cfg-input-sm"
              type="number"
              value={heartbeat.intervalS ?? 1800}
              onChange={(e) => update(['gateway', 'heartbeat', 'intervalS'], Number(e.target.value))}
            />
          </Field>
        </div>
      </Section>

      {/* ── Memory ── */}
      <Section title="Memory" id="memory" expanded={expanded} toggle={toggle}>
        <Field label="Enabled">
          <Toggle
            value={memory.enabled === true}
            onChange={(v) => update(['memory', 'enabled'], v)}
          />
        </Field>
        <Field label="Vault Path" hint="Obsidian vault root (defaults to workspace)">
          <input
            className="cfg-input"
            value={memory.vaultPath || ''}
            placeholder="(defaults to workspace)"
            onChange={(e) => update(['memory', 'vaultPath'], e.target.value || null)}
          />
        </Field>
        <div className="field-row">
          <Field label="Distillation Model">
            <input
              className="cfg-input cfg-input-sm"
              value={memory.distillationModel || ''}
              placeholder="(uses agent model)"
              onChange={(e) => update(['memory', 'distillationModel'], e.target.value || null)}
            />
          </Field>
          <Field label="Classification Model">
            <input
              className="cfg-input cfg-input-sm"
              value={memory.classificationModel || ''}
              placeholder="(uses agent model)"
              onChange={(e) => update(['memory', 'classificationModel'], e.target.value || null)}
            />
          </Field>
        </div>
        {memory.decay && (
          <>
            <div className="subsection-label">Decay TTLs</div>
            <div className="field-row">
              <Field label="Stable (days)">
                <input className="cfg-input cfg-input-xs" type="number" value={memory.decay?.stableTtlDays ?? 90}
                  onChange={(e) => update(['memory', 'decay', 'stableTtlDays'], Number(e.target.value))} />
              </Field>
              <Field label="Active (days)">
                <input className="cfg-input cfg-input-xs" type="number" value={memory.decay?.activeTtlDays ?? 14}
                  onChange={(e) => update(['memory', 'decay', 'activeTtlDays'], Number(e.target.value))} />
              </Field>
              <Field label="Session (hrs)">
                <input className="cfg-input cfg-input-xs" type="number" value={memory.decay?.sessionTtlHours ?? 24}
                  onChange={(e) => update(['memory', 'decay', 'sessionTtlHours'], Number(e.target.value))} />
              </Field>
              <Field label="Checkpoint (hrs)">
                <input className="cfg-input cfg-input-xs" type="number" value={memory.decay?.checkpointTtlHours ?? 4}
                  onChange={(e) => update(['memory', 'decay', 'checkpointTtlHours'], Number(e.target.value))} />
              </Field>
            </div>
          </>
        )}
        {memory.index && (
          <>
            <div className="subsection-label">Index</div>
            <div className="field-row">
              <Field label="Max Tokens">
                <input className="cfg-input cfg-input-xs" type="number" value={memory.index?.maxTokens ?? 3000}
                  onChange={(e) => update(['memory', 'index', 'maxTokens'], Number(e.target.value))} />
              </Field>
              <Field label="Context Slots">
                <input className="cfg-input cfg-input-xs" type="number" value={memory.index?.activeContextSlots ?? 3}
                  onChange={(e) => update(['memory', 'index', 'activeContextSlots'], Number(e.target.value))} />
              </Field>
            </div>
          </>
        )}
      </Section>

      {/* ── Tools ── */}
      <Section title="Tools" id="tools" expanded={expanded} toggle={toggle}>
        <Field label="Restrict to Workspace">
          <Toggle
            value={tools.restrictToWorkspace === true}
            onChange={(v) => update(['tools', 'restrictToWorkspace'], v)}
          />
        </Field>
        <div className="subsection-label">Web Search</div>
        <div className="field-row">
          <Field label="Brave API Key">
            <PasswordField
              value={tools.web?.search?.apiKey || ''}
              placeholder="Brave Search API Key"
              onChange={(v) => update(['tools', 'web', 'search', 'apiKey'], v)}
            />
          </Field>
          <Field label="Max Results">
            <input className="cfg-input cfg-input-xs" type="number"
              value={tools.web?.search?.maxResults ?? 5}
              onChange={(e) => update(['tools', 'web', 'search', 'maxResults'], Number(e.target.value))} />
          </Field>
        </div>
        <Field label="Web Proxy" hint="HTTP/SOCKS5 proxy URL">
          <input className="cfg-input" value={tools.web?.proxy || ''} placeholder="(none)"
            onChange={(e) => update(['tools', 'web', 'proxy'], e.target.value || null)} />
        </Field>
        <div className="subsection-label">Shell Exec</div>
        <div className="field-row">
          <Field label="Timeout (s)">
            <input className="cfg-input cfg-input-xs" type="number"
              value={tools.exec?.timeout ?? 60}
              onChange={(e) => update(['tools', 'exec', 'timeout'], Number(e.target.value))} />
          </Field>
          <Field label="PATH Append">
            <input className="cfg-input cfg-input-sm" value={tools.exec?.pathAppend || ''}
              onChange={(e) => update(['tools', 'exec', 'pathAppend'], e.target.value)} />
          </Field>
        </div>
      </Section>

      {/* ── Channels (summary) ── */}
      <Section title="Channels" id="channels" expanded={expanded} toggle={toggle}>
        <ChannelsEditor config={config} update={update} />
      </Section>

      {/* ── Configured Agents (read-only summary) ── */}
      {Object.keys(agents).length > 0 && (
        <Section title={`Configured Agents (${Object.keys(agents).length})`} id="agents-list" expanded={expanded} toggle={toggle}>
          <div className="config-list">
            {Object.entries(agents).map(([id, a]: [string, any]) => (
              <div key={id} className="config-list-item">
                <span className="config-list-name">{id}</span>
                <span className="config-list-detail">{a.model || '(default model)'}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ── Configured Teams (read-only summary) ── */}
      {Object.keys(teams).length > 0 && (
        <Section title={`Configured Teams (${Object.keys(teams).length})`} id="teams-list" expanded={expanded} toggle={toggle}>
          <div className="config-list">
            {Object.entries(teams).map(([name, t]: [string, any]) => (
              <div key={name} className="config-list-item">
                <span className="config-list-name">{name}</span>
                <span className="config-list-detail">
                  leader: {t.leader} · {(t.agents || []).length} members · {t.approvalMode || 'auto'}
                </span>
              </div>
            ))}
          </div>
        </Section>
      )}

      <div className="config-footer">
        <button className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save Configuration'}
        </button>
        <span className="config-hint">Changes require a restart to take effect.</span>
      </div>
    </div>
  )
}

// ── Helper components ─────────────────────────────────────

function Section({ title, id, expanded, toggle, children }: {
  title: string; id: string
  expanded: Set<string>; toggle: (id: string) => void
  children: React.ReactNode
}) {
  const isOpen = expanded.has(id)
  return (
    <div className="config-section">
      <button className="config-section-header" onClick={() => toggle(id)}>
        <span className="config-chevron">{isOpen ? '▾' : '▸'}</span>
        <span className="config-section-title">{title}</span>
      </button>
      {isOpen && <div className="config-section-body">{children}</div>}
    </div>
  )
}

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode
}) {
  return (
    <div className="cfg-field">
      <label className="cfg-label">
        {label}
        {hint && <span className="cfg-hint">{hint}</span>}
      </label>
      {children}
    </div>
  )
}

function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      className={`cfg-toggle ${value ? 'on' : ''}`}
      onClick={() => onChange(!value)}
      type="button"
    >
      <span className="cfg-toggle-knob" />
    </button>
  )
}

function PasswordField({ value, placeholder, onChange }: {
  value: string; placeholder?: string; onChange: (v: string) => void
}) {
  const [visible, setVisible] = useState(false)
  return (
    <div className="password-field">
      <input
        className="cfg-input"
        type={visible ? 'text' : 'password'}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        className="password-toggle"
        onClick={() => setVisible(!visible)}
        type="button"
        tabIndex={-1}
      >
        {visible ? '◉' : '○'}
      </button>
    </div>
  )
}

// ── MCP Servers Editor ────────────────────────────────────

function MCPServersEditor({ servers, onChange }: {
  servers: Record<string, any>
  onChange: (servers: Record<string, any>) => void
}) {
  const [newName, setNewName] = useState('')
  const entries = Object.entries(servers)

  const addServer = () => {
    const name = newName.trim()
    if (!name || servers[name]) return
    onChange({
      ...servers,
      [name]: { type: 'stdio', command: '', args: [], env: {}, url: '', headers: {}, toolTimeout: 30 },
    })
    setNewName('')
  }

  const removeServer = (name: string) => {
    const next = { ...servers }
    delete next[name]
    onChange(next)
  }

  const updateServer = (name: string, field: string, value: any) => {
    onChange({
      ...servers,
      [name]: { ...servers[name], [field]: value },
    })
  }

  return (
    <div className="mcp-editor">
      {entries.length === 0 && (
        <div className="mcp-empty">No MCP servers configured</div>
      )}
      {entries.map(([name, srv]) => (
        <div key={name} className="mcp-server">
          <div className="mcp-server-header">
            <span className="mcp-server-name">{name}</span>
            <span className="mcp-server-type">{srv.type || 'stdio'}</span>
            <button className="btn btn-danger btn-xs" onClick={() => removeServer(name)}>Remove</button>
          </div>
          <div className="mcp-server-body">
            <div className="field-row">
              <Field label="Type">
                <select className="cfg-select" value={srv.type || 'stdio'}
                  onChange={(e) => updateServer(name, 'type', e.target.value)}>
                  <option value="stdio">stdio</option>
                  <option value="sse">sse</option>
                  <option value="streamableHttp">streamableHttp</option>
                </select>
              </Field>
              <Field label="Timeout (s)">
                <input className="cfg-input cfg-input-xs" type="number"
                  value={srv.toolTimeout ?? 30}
                  onChange={(e) => updateServer(name, 'toolTimeout', Number(e.target.value))} />
              </Field>
            </div>
            {(srv.type === 'stdio' || !srv.type) && (
              <>
                <Field label="Command">
                  <input className="cfg-input" value={srv.command || ''}
                    placeholder="e.g. npx, node, python"
                    onChange={(e) => updateServer(name, 'command', e.target.value)} />
                </Field>
                <Field label="Args" hint="Comma-separated">
                  <input className="cfg-input" value={(srv.args || []).join(', ')}
                    placeholder="e.g. -y, @modelcontextprotocol/server-filesystem, /tmp"
                    onChange={(e) => updateServer(name, 'args', e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean))} />
                </Field>
                <Field label="Environment" hint="KEY=VALUE, one per line">
                  <textarea className="cfg-textarea" rows={2}
                    value={Object.entries(srv.env || {}).map(([k, v]) => `${k}=${v}`).join('\n')}
                    placeholder="NODE_ENV=production"
                    onChange={(e) => {
                      const env: Record<string, string> = {}
                      e.target.value.split('\n').forEach((line) => {
                        const eq = line.indexOf('=')
                        if (eq > 0) env[line.slice(0, eq).trim()] = line.slice(eq + 1).trim()
                      })
                      updateServer(name, 'env', env)
                    }}
                  />
                </Field>
              </>
            )}
            {(srv.type === 'sse' || srv.type === 'streamableHttp') && (
              <>
                <Field label="URL">
                  <input className="cfg-input" value={srv.url || ''}
                    placeholder="https://..."
                    onChange={(e) => updateServer(name, 'url', e.target.value)} />
                </Field>
                <Field label="Headers" hint="KEY=VALUE, one per line">
                  <textarea className="cfg-textarea" rows={2}
                    value={Object.entries(srv.headers || {}).map(([k, v]) => `${k}=${v}`).join('\n')}
                    placeholder="Authorization=Bearer token"
                    onChange={(e) => {
                      const headers: Record<string, string> = {}
                      e.target.value.split('\n').forEach((line) => {
                        const eq = line.indexOf('=')
                        if (eq > 0) headers[line.slice(0, eq).trim()] = line.slice(eq + 1).trim()
                      })
                      updateServer(name, 'headers', headers)
                    }}
                  />
                </Field>
              </>
            )}
          </div>
        </div>
      ))}
      <div className="mcp-add">
        <input
          className="cfg-input"
          value={newName}
          placeholder="New server name…"
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') addServer() }}
        />
        <button className="btn btn-secondary" onClick={addServer} disabled={!newName.trim()}>
          Add Server
        </button>
      </div>
    </div>
  )
}

// ── Channels Editor ───────────────────────────────────────

const CHANNEL_NAMES = [
  'whatsapp', 'telegram', 'discord', 'slack', 'email',
  'matrix', 'feishu', 'dingtalk', 'mochat', 'qq',
]

function ChannelsEditor({ config, update }: { config: Cfg; update: (path: string[], value: any) => void }) {
  const channels = config.channels || {}
  const [expandedCh, setExpandedCh] = useState<string | null>(null)

  return (
    <div className="channels-editor">
      <div className="field-row">
        <Field label="Send Progress">
          <Toggle
            value={channels.sendProgress !== false}
            onChange={(v) => update(['channels', 'sendProgress'], v)}
          />
        </Field>
        <Field label="Send Tool Hints">
          <Toggle
            value={channels.sendToolHints === true}
            onChange={(v) => update(['channels', 'sendToolHints'], v)}
          />
        </Field>
      </div>
      {CHANNEL_NAMES.map((name) => {
        const ch = channels[name] || {}
        const isOpen = expandedCh === name
        return (
          <div key={name} className="channel-row">
            <button className="channel-header" onClick={() => setExpandedCh(isOpen ? null : name)}>
              <span className={`provider-dot ${ch.enabled ? 'on' : ''}`} />
              <span className="channel-name">{name}</span>
              <span className="config-chevron">{isOpen ? '▾' : '▸'}</span>
            </button>
            {isOpen && (
              <div className="channel-body">
                <Field label="Enabled">
                  <Toggle
                    value={ch.enabled === true}
                    onChange={(v) => update(['channels', name, 'enabled'], v)}
                  />
                </Field>
                {name === 'telegram' && (
                  <>
                    <Field label="Bot Token">
                      <PasswordField value={ch.token || ''} placeholder="Bot token from @BotFather"
                        onChange={(v) => update(['channels', name, 'token'], v)} />
                    </Field>
                    <Field label="Allow From" hint="Comma-separated user IDs">
                      <input className="cfg-input" value={(ch.allowFrom || []).join(', ')}
                        onChange={(e) => update(['channels', name, 'allowFrom'], e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean))} />
                    </Field>
                  </>
                )}
                {name === 'discord' && (
                  <>
                    <Field label="Bot Token">
                      <PasswordField value={ch.token || ''} placeholder="Discord bot token"
                        onChange={(v) => update(['channels', name, 'token'], v)} />
                    </Field>
                    <Field label="Allow From" hint="Comma-separated user IDs">
                      <input className="cfg-input" value={(ch.allowFrom || []).join(', ')}
                        onChange={(e) => update(['channels', name, 'allowFrom'], e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean))} />
                    </Field>
                  </>
                )}
                {name === 'slack' && (
                  <>
                    <Field label="Bot Token">
                      <PasswordField value={ch.botToken || ''} placeholder="xoxb-..."
                        onChange={(v) => update(['channels', name, 'botToken'], v)} />
                    </Field>
                    <Field label="App Token">
                      <PasswordField value={ch.appToken || ''} placeholder="xapp-..."
                        onChange={(v) => update(['channels', name, 'appToken'], v)} />
                    </Field>
                  </>
                )}
                {name === 'whatsapp' && (
                  <>
                    <Field label="Bridge URL">
                      <input className="cfg-input" value={ch.bridgeUrl || ''}
                        onChange={(e) => update(['channels', name, 'bridgeUrl'], e.target.value)} />
                    </Field>
                    <Field label="Bridge Token">
                      <PasswordField value={ch.bridgeToken || ''} placeholder="Shared auth token"
                        onChange={(v) => update(['channels', name, 'bridgeToken'], v)} />
                    </Field>
                  </>
                )}
                {name === 'email' && (
                  <>
                    <Field label="IMAP Host">
                      <input className="cfg-input" value={ch.imapHost || ''}
                        onChange={(e) => update(['channels', name, 'imapHost'], e.target.value)} />
                    </Field>
                    <Field label="IMAP Username">
                      <input className="cfg-input" value={ch.imapUsername || ''}
                        onChange={(e) => update(['channels', name, 'imapUsername'], e.target.value)} />
                    </Field>
                    <Field label="IMAP Password">
                      <PasswordField value={ch.imapPassword || ''}
                        onChange={(v) => update(['channels', name, 'imapPassword'], v)} />
                    </Field>
                    <Field label="SMTP Host">
                      <input className="cfg-input" value={ch.smtpHost || ''}
                        onChange={(e) => update(['channels', name, 'smtpHost'], e.target.value)} />
                    </Field>
                    <Field label="From Address">
                      <input className="cfg-input" value={ch.fromAddress || ''}
                        onChange={(e) => update(['channels', name, 'fromAddress'], e.target.value)} />
                    </Field>
                  </>
                )}
                {name === 'matrix' && (
                  <>
                    <Field label="Homeserver">
                      <input className="cfg-input" value={ch.homeserver || ''}
                        onChange={(e) => update(['channels', name, 'homeserver'], e.target.value)} />
                    </Field>
                    <Field label="Access Token">
                      <PasswordField value={ch.accessToken || ''}
                        onChange={(v) => update(['channels', name, 'accessToken'], v)} />
                    </Field>
                    <Field label="User ID" hint="e.g. @bot:matrix.org">
                      <input className="cfg-input" value={ch.userId || ''}
                        onChange={(e) => update(['channels', name, 'userId'], e.target.value)} />
                    </Field>
                  </>
                )}
                {name === 'feishu' && (
                  <>
                    <Field label="App ID">
                      <input className="cfg-input" value={ch.appId || ''}
                        onChange={(e) => update(['channels', name, 'appId'], e.target.value)} />
                    </Field>
                    <Field label="App Secret">
                      <PasswordField value={ch.appSecret || ''}
                        onChange={(v) => update(['channels', name, 'appSecret'], v)} />
                    </Field>
                  </>
                )}
                {name === 'dingtalk' && (
                  <>
                    <Field label="Client ID">
                      <input className="cfg-input" value={ch.clientId || ''}
                        onChange={(e) => update(['channels', name, 'clientId'], e.target.value)} />
                    </Field>
                    <Field label="Client Secret">
                      <PasswordField value={ch.clientSecret || ''}
                        onChange={(v) => update(['channels', name, 'clientSecret'], v)} />
                    </Field>
                  </>
                )}
                {(name === 'mochat' || name === 'qq') && (
                  <div className="cfg-hint" style={{ padding: '8px 0' }}>
                    Configure additional {name} settings in config.json directly.
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
