import { useEffect, useRef, useCallback } from 'react'
import { useDashboardStore } from './useDashboardStore'
import type { WSMessage } from '../types'

const WS_URL = import.meta.env.DEV
  ? `ws://${window.location.hostname}:${window.location.port}/ws`
  : `ws://${window.location.host}/ws`

const RECONNECT_DELAY = 2000

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<number | null>(null)
  const { setConnected, setSnapshot, addEvent } = useDashboardStore()

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      console.log('[Dashboard] WebSocket connected')
    }

    ws.onmessage = (ev) => {
      try {
        const msg: WSMessage = JSON.parse(ev.data)
        if (msg.type === 'snapshot') {
          setSnapshot(msg.data)
        } else if (msg.type === 'event') {
          addEvent(msg.data)
        }
      } catch (err) {
        console.error('[Dashboard] Failed to parse message:', err)
      }
    }

    ws.onclose = () => {
      setConnected(false)
      console.log('[Dashboard] WebSocket disconnected, reconnecting...')
      reconnectTimer.current = window.setTimeout(connect, RECONNECT_DELAY)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [setConnected, setSnapshot, addEvent])

  const sendCommand = useCallback((text: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'command', text }))
    }
  }, [])

  useEffect(() => {
    connect()
    // Also fetch config
    fetch('/api/config')
      .then((r) => r.json())
      .then((data) => useDashboardStore.getState().setConfig(data))
      .catch(() => {})

    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { sendCommand }
}
