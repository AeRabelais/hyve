import { useWebSocket } from './hooks/useWebSocket'
import { TopBar } from './components/TopBar'
import { Sidebar } from './components/Sidebar'
import { ChainVisualizer } from './components/ChainVisualizer'
import { EventFeed } from './components/EventFeed'
import { TaskBoard } from './components/TaskBoard'
import { WorkLog } from './components/WorkLog'
import { CommandBar } from './components/CommandBar'

export function App() {
  const { sendCommand } = useWebSocket()

  return (
    <>
      <TopBar />
      <div className="main-layout">
        <Sidebar />
        <div className="content-area">
          <div className="panel-grid">
            <ChainVisualizer />
            <EventFeed />
            <TaskBoard />
            <WorkLog />
          </div>
        </div>
      </div>
      <CommandBar onSend={sendCommand} />
    </>
  )
}
