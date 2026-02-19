import { useState, useEffect, useRef } from 'react'
import './App.css'

function App() {
  const [session, setSession] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [repoPath, setRepoPath] = useState(".")
  const [isConnected, setIsConnected] = useState(false)
  const messagesEndRef = useRef(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const connectToRepo = async () => {
    try {
      setIsLoading(true)
      const res = await fetch('/api/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_path: repoPath })
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setSession(data)
      setIsConnected(true)
      setMessages([{
        role: 'assistant',
        content: `Connected to ${data.repo_path}. I'm ready to help!`,
        timestamp: new Date().toISOString()
      }])
    } catch (err) {
      alert(`Connection failed: ${err.message}`)
    } finally {
      setIsLoading(false)
    }
  }

  const sendMessage = async (e) => {
    e.preventDefault()
    if (!input.trim() || isLoading) return

    const userMsg = { role: 'user', content: input, timestamp: new Date().toISOString() }
    setMessages(prev => [...prev, userMsg])
    setInput("")
    setIsLoading(true)

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: session.session_id, message: userMsg.content })
      })
      if (!res.ok) throw new Error(await res.text())

      const data = await res.json()
      const assistantMsg = {
        role: 'assistant',
        content: data.message,
        timestamp: new Date().toISOString(),
        action: data.action,
        execution_result: data.execution_result
      }
      setMessages(prev => [...prev, assistantMsg])

    } catch (err) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `Error: ${err.message}`,
        timestamp: new Date().toISOString(),
        isError: true
      }])
    } finally {
      setIsLoading(false)
    }
  }

  const toggleMode = async () => {
    if (!session) return
    const newMode = session.interactive_mode ? "auto" : "interactive"

    // Optimistic update
    setSession(prev => ({ ...prev, interactive_mode: !prev.interactive_mode }))

    try {
      await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: session.session_id, mode: newMode })
      })
    } catch (err) {
      console.error("Failed to toggle mode", err)
      // Revert on failure
      setSession(prev => ({ ...prev, interactive_mode: !prev.interactive_mode }))
    }
  }

  if (!isConnected) {
    return (
      <div className="connect-screen">
        <div className="card">
          <h1>🤖 God Mode Agent</h1>
          <p>Enter the absolute path to your repository to begin.</p>
          <input
            type="text"
            value={repoPath}
            onChange={e => setRepoPath(e.target.value)}
            placeholder="/path/to/your/repo"
          />
          <button onClick={connectToRepo} disabled={isLoading}>
            {isLoading ? "Connecting..." : "Connect"}
          </button>
        </div>
      </div>
    )
  }

  const isInteractive = session?.interactive_mode

  return (
    <div className={`app-container ${isInteractive ? 'interactive' : 'god-mode'}`}>
      <header className="app-header">
        <div className="logo">🤖 God Mode</div>
        <div className="status-badge">
          {isInteractive ? "🤝 Co-Pilot Active" : "✨ God Mode Active"}
        </div>
      </header>

      <div className="chat-area">
        {messages.map((msg, idx) => (
          <div key={idx} className={`message ${msg.role} ${msg.isError ? 'error' : ''}`}>
            <div className="message-content">
              {msg.content.split('\n').map((line, i) => <div key={i}>{line}</div>)}
            </div>
            {msg.action && (
              <div className="action-block">
                <div className="action-header">⚡ Executing: {msg.action.type}</div>
                <pre className="action-task">{msg.action.task}</pre>
                {msg.execution_result && (
                  <div className="execution-result">
                    <div className="result-label">Result:</div>
                    <pre>{msg.execution_result}</pre>
                  </div>
                )}
              </div>
            )}
            <div className="timestamp">{new Date(msg.timestamp).toLocaleTimeString()}</div>
          </div>
        ))}
        <div ref={messagesEndRef} />
        {isLoading && <div className="typing-indicator">Thinking...</div>}
      </div>

      <div className="control-bar">
        <button
          className={`mode-toggle ${isInteractive ? 'copilot' : 'god'}`}
          onClick={toggleMode}
        >
          {isInteractive ? "🚀 Switch to God Mode" : "✋ Switch to Co-Pilot"}
        </button>

        <form onSubmit={sendMessage} className="input-form">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder={isInteractive ? "Command your Co-pilot..." : "Give God Mode a task..."}
            disabled={isLoading}
          />
          <button type="submit" disabled={isLoading || !input.trim()}>Send</button>
        </form>
      </div>
    </div>
  )
}

export default App
