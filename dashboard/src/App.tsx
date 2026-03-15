import { useWebSocket } from './hooks/useWebSocket'
import { useDashboardStore } from './hooks/useDashboardStore'
import { TopBar } from './components/TopBar'
import { Sidebar } from './components/Sidebar'
import { TabBar } from './components/TabBar'
import { ChainVisualizer } from './components/ChainVisualizer'
import { EventFeed } from './components/EventFeed'
import { TaskBoard } from './components/TaskBoard'
import { WorkLog } from './components/WorkLog'
import { CommandBar } from './components/CommandBar'
import { ConfigEditor } from './components/ConfigEditor'
import { AgentForm } from './components/AgentForm'
import { TeamForm } from './components/TeamForm'

export function App() {
  const { sendCommand } = useWebSocket()
  const activeTab = useDashboardStore((s) => s.activeTab)

  return (
    <>
      <TopBar />
      <div className="main-layout">
        <Sidebar />
        <div className="content-area">
          <TabBar />
          {activeTab === 'monitor' && (
            <div className="panel-grid">
              <ChainVisualizer />
              <EventFeed />
              <TaskBoard />
              <WorkLog />
            </div>
          )}
          {activeTab === 'config' && <ConfigEditor />}
          {activeTab === 'agent' && <AgentForm />}
          {activeTab === 'team' && <TeamForm />}
        </div>
      </div>
      <CommandBar onSend={sendCommand} />
    </>
  )
}
