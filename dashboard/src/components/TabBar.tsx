import { useDashboardStore } from '../hooks/useDashboardStore'
import type { ActiveTab } from '../types'

const TABS: { id: ActiveTab; label: string; icon: string }[] = [
  { id: 'monitor', label: 'Monitor', icon: '◉' },
  { id: 'config', label: 'Edit Config', icon: '⚙' },
  { id: 'agent', label: 'Add Agent', icon: '+' },
  { id: 'team', label: 'Add Team', icon: '⊞' },
]

export function TabBar() {
  const activeTab = useDashboardStore((s) => s.activeTab)
  const setActiveTab = useDashboardStore((s) => s.setActiveTab)

  return (
    <div className="tab-bar">
      {TABS.map((tab) => (
        <button
          key={tab.id}
          className={`tab-item ${activeTab === tab.id ? 'active' : ''}`}
          onClick={() => setActiveTab(tab.id)}
        >
          <span className="tab-icon">{tab.icon}</span>
          {tab.label}
        </button>
      ))}
    </div>
  )
}
