import { useState, useEffect, useRef } from 'react'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import './App.css'

function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark')
  const [session, setSession] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [repoPath, setRepoPath] = useState(".")
  const [isConnected, setIsConnected] = useState(false)
  const [status, setStatus] = useState(null)
  const [files, setFiles] = useState([])
  const [expandedFolders, setExpandedFolders] = useState(new Set())
  const [selectedFile, setSelectedFile] = useState(null)
  const [activeTab, setActiveTab] = useState('chat')
  const messagesEndRef = useRef(null)

  useEffect(() => {
    localStorage.setItem('theme', theme)
  }, [theme])

  const toggleTheme = () => {
    setTheme(prev => prev === 'dark' ? 'light' : 'dark')
  }

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  const handleDisconnect = () => {
    setIsConnected(false)
    setSession(null)
    setFiles([])
    setMessages([])
    setExpandedFolders(new Set())
    setSelectedFile(null)
  }

  useEffect(() => {
    if (!isConnected || !session) return

    const fetchStatus = async () => {
      try {
        const sRes = await fetch(`/api/status/${session.session_id}`)
        if (sRes.status === 404) return handleDisconnect()
        if (sRes.ok) setStatus(await sRes.json())

        const fRes = await fetch(`/api/files/${session.session_id}`)
        if (fRes.status === 404) return handleDisconnect()
        if (fRes.ok) {
          const data = await fRes.json()
          setFiles(data.files)
        }
      } catch (err) {
        console.error("Fetch failed", err)
      }
    }

    fetchStatus()
    const interval = setInterval(fetchStatus, 5000)
    return () => clearInterval(interval)
  }, [isConnected, session])

  const refreshFiles = async () => {
    if (!session) return
    try {
      const fRes = await fetch(`/api/files/${session.session_id}`)
      if (fRes.status === 404) return handleDisconnect()
      if (fRes.ok) {
        const data = await fRes.json()
        setFiles(data.files)
      }
    } catch (err) {
      console.error("Refresh failed", err)
    }
  }

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

    // Start SSE stream
    const eventSource = new EventSource(`/api/stream/${session.session_id}`);

    // Create a temporary message to hold real-time logs
    const tempMsgId = Date.now().toString();
    const tempMsg = {
      id: tempMsgId,
      role: 'assistant',
      content: 'Working on it...',
      isLoading: true,
      logs: [],
      currentStep: 'STARTING',
      phases: [], // Array of { name: string, status: 'running' | 'done' }
      timestamp: new Date().toISOString()
    };

    setMessages(prev => [...prev, tempMsg]);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.status) {
          setMessages(prev => prev.map(msg => {
            if (msg.id === tempMsgId) {
              const prevPhases = msg.phases || [];
              // If this is a new step, mark prev as done and add new
              const updatedPhases = prevPhases.map(p => ({ ...p, status: 'done' }));
              if (!updatedPhases.find(p => p.name === data.status)) {
                updatedPhases.push({ name: data.status, status: 'running' });
              }
              return { ...msg, currentStep: data.status, phases: updatedPhases };
            }
            return msg;
          }));
        } else if (data.log) {
          setMessages(prev => prev.map(msg => {
            if (msg.id === tempMsgId) {
              return { ...msg, logs: [...(msg.logs || []), data.log] };
            }
            return msg;
          }));
        }
      } catch (e) {
        console.error("SSE parse error", e);
      }
    };

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: session.session_id, message: userMsg.content })
      })

      if (res.status === 404) {
        eventSource.close()
        return handleDisconnect()
      }

      if (!res.ok) throw new Error(await res.text())

      const data = await res.json()
      eventSource.close()

      // Replace temp message with final, preserving logs
      setMessages(prev => prev.map(msg => {
        if (msg.id === tempMsgId) {
          return {
            role: 'assistant',
            content: data.message,
            timestamp: new Date().toISOString(),
            action: data.action,
            execution_result: data.execution_result,
            logs: msg.logs // Preserve the logs gathered via SSE
          };
        }
        return msg;
      }));

    } catch (err) {
      eventSource.close()
      setMessages(prev => prev.map(msg => msg.id === tempMsgId ? {
        role: 'assistant',
        content: `Error: ${err.message}`,
        timestamp: new Date().toISOString(),
        isError: true
      } : msg));
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

  const getLanguage = (filename) => {
    if (!filename) return 'text'
    const ext = filename.split('.').pop().toLowerCase()
    const map = {
      'js': 'javascript',
      'jsx': 'jsx',
      'ts': 'typescript',
      'tsx': 'tsx',
      'py': 'python',
      'css': 'css',
      'html': 'html',
      'json': 'json',
      'md': 'markdown',
      'sh': 'bash',
      'yml': 'yaml',
      'yaml': 'yaml',
      'sql': 'sql'
    }
    return map[ext] || 'text'
  }

  const buildFileTree = (paths) => {
    const tree = {}
    paths.forEach(path => {
      const parts = path.split('/')
      let current = tree
      parts.forEach((part, i) => {
        if (!current[part]) {
          current[part] = i === parts.length - 1 ? null : {}
        }
        current = current[part]
      })
    })
    return tree
  }

  const toggleFolder = (e, path) => {
    if (e) e.stopPropagation()
    setExpandedFolders(prev => {
      const next = new Set(prev)
      if (next.has(path)) {
        next.delete(path)
      } else {
        next.add(path)
      }
      return next
    })
  }

  const viewFile = async (path) => {
    try {
      const res = await fetch(`/api/files/content/${session.session_id}?path=${encodeURIComponent(path)}`)
      if (res.ok) {
        const data = await res.json()
        setSelectedFile(data)
        setActiveTab('code')
      }
    } catch (err) {
      console.error("Failed to view file", err)
    }
  }

  const handleDelete = async (e, path) => {
    e.stopPropagation()
    if (!window.confirm(`Are you sure you want to delete ${path}?`)) return

    try {
      const res = await fetch(`/api/files/${session.session_id}?path=${encodeURIComponent(path)}`, {
        method: 'DELETE'
      })
      if (res.ok) {
        setFiles(prev => prev.filter(f => !f.startsWith(path)))
      }
    } catch (err) {
      console.error("Failed to delete", err)
    }
  }

  const renderTree = (node, name, path = "", depth = 0) => {
    const isFolder = node !== null
    // currentPath should be the relative path from the repo root
    // For the root node (depth 0), we don't want to add its name to the path
    const currentPath = depth === 0 ? "" : (path ? `${path}/${name}` : name)
    const paddingLeft = depth * 12
    const isExpanded = depth === 0 || expandedFolders.has(currentPath)

    return (
      <div key={currentPath || name} className="tree-item-container">
        <div
          className={`tree-node ${isFolder ? 'folder' : 'file'}`}
          style={{ paddingLeft: `${paddingLeft}px` }}
          onClick={() => isFolder ? toggleFolder(null, currentPath) : viewFile(currentPath)}
        >
          <div className="tree-node-main">
            {isFolder && depth > 0 && (
              <span className="expand-icon" onClick={(e) => toggleFolder(e, currentPath)}>
                {isExpanded ? '▼' : '▶'}
              </span>
            )}
            {(!isFolder || depth === 0) && <span className="expand-icon"></span>}
            <span className="type-icon">{isFolder ? (isExpanded ? '📂' : '📁') : '📄'}</span>
            <span className="node-name">{name}</span>
          </div>

          <div className="node-actions" onClick={e => e.stopPropagation()}>
            {depth > 0 && (
              <button className="delete-btn" onClick={(e) => handleDelete(e, currentPath)} title="Delete">
                🗑️
              </button>
            )}
          </div>
        </div>

        {isFolder && isExpanded && (
          <div className="folder-content">
            {Object.keys(node).sort().map(childName =>
              renderTree(node[childName], childName, currentPath, depth + 1)
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className={`app-container theme-${theme} ${isInteractive ? 'interactive' : 'god-mode'}`}>
      <header className="app-header">
        <div className="header-left">
          <div className="logo">🤖 God Mode</div>
          <div className="status-badge">
            {isInteractive ? "🤝 Co-Pilot Active" : "✨ God Mode Active"}
          </div>
        </div>
        <div className="header-right">
          <button className="theme-toggle-btn" onClick={toggleTheme} title="Switch Theme">
            {theme === 'dark' ? '☀️' : '🌙'}
          </button>
          <button className="disconnect-btn" onClick={handleDisconnect}>
            🔌 Disconnect
          </button>
        </div>
      </header>

      <div className="main-layout">
        <aside className="sidebar left">
          <div className="sidebar-section">
            <h3>📂 Project Context</h3>
            <div className="sidebar-item">
              <div className="sidebar-label">Path</div>
              <div className="sidebar-value">{session?.repo_path}</div>
            </div>
            <div className="sidebar-item">
              <div className="sidebar-label">Files</div>
              <div className="sidebar-value">{session?.file_count} identified</div>
            </div>
            <div className="sidebar-item">
              <div className="sidebar-label">Stack</div>
              <div className="sidebar-value">{session?.stack}</div>
            </div>
          </div>

          <div className="sidebar-section">
            <div className="sidebar-header">
              <h3>🌳 File Tree</h3>
              <button className="refresh-btn" onClick={refreshFiles} title="Refresh Tree">🔄</button>
            </div>
            <div className="file-tree">
              {(() => {
                const tree = buildFileTree(files)
                const repoName = session?.repo_path?.split('/').pop() || 'Project'
                return renderTree(tree, repoName, "", 0)
              })()}
            </div>
          </div>
        </aside>

        <div className="chat-area">
          <div className="tab-bar">
            <button
              className={`tab ${activeTab === 'chat' ? 'active' : ''}`}
              onClick={() => setActiveTab('chat')}
            >
              💬 Chat
            </button>
            <button
              className={`tab ${activeTab === 'code' ? 'active' : ''}`}
              onClick={() => setActiveTab('code')}
              disabled={!selectedFile}
            >
              📄 {selectedFile ? selectedFile.path.split('/').pop() : 'Viewer'}
            </button>
          </div>

          <div className="tab-content">
            {activeTab === 'chat' ? (
              <div className="messages-container">
                {messages.map((msg, idx) => (
                  <div key={idx} className={`message ${msg.role} ${msg.isError ? 'error' : ''}`}>
                    <div className="message-content">
                      {msg.role === 'assistant' && (msg.phases || msg.logs?.length > 0) && (
                        <div className="agent-execution-block">
                          <details className="execution-status" open={msg.isLoading}>
                            <summary>
                              <div className={`status-summary ${msg.isLoading ? 'isLoading' : ''}`}>
                                <span className="status-dot"></span>
                                <span className="status-text">
                                  {msg.isLoading ? `Agent is ${msg.currentStep || 'working'}...` : 'Execution Steps'}
                                </span>
                              </div>
                            </summary>
                            <div className="phase-stepper">
                              {(msg.phases || []).map((p, i) => {
                                const labels = {
                                  'ANALYZING': 'Analyzing Requirements',
                                  'RESEARCHING': 'Researching Codebase',
                                  'PLANNING': 'Developing Plan',
                                  'EXECUTING': 'Implementing Changes',
                                  'REFLECTING': 'Refining Code Quality',
                                  'VERIFYING': 'Visual Verification',
                                  'SYNCING': 'Finalizing Logs',
                                  'DONE': 'Task Complete'
                                };
                                return (
                                  <div key={i} className={`phase-item ${p.status}`}>
                                    <span className="phase-icon">
                                      {p.status === 'done' ? '✅' : '⏳'}
                                    </span>
                                    {labels[p.name] || p.name}
                                  </div>
                                );
                              })}
                            </div>
                            {msg.logs && msg.logs.length > 0 && (
                              <div className="live-logs">
                                {msg.logs.map((logLine, i) => <div key={i}>{logLine}</div>)}
                              </div>
                            )}
                          </details>
                        </div>
                      )}

                      <div className="bubble-text">
                        {msg.content.split('\n').map((line, i) => <div key={i}>{line}</div>)}
                      </div>
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
            ) : (
              <div className="code-viewer-pane">
                <div className="code-viewer-header">
                  <span>{selectedFile.path}</span>
                  <button onClick={() => setSelectedFile(null) || setActiveTab('chat')}>✕</button>
                </div>
                <div className="code-view-container">
                  <SyntaxHighlighter
                    language={getLanguage(selectedFile.path)}
                    style={oneLight}
                    customStyle={{
                      margin: 0,
                      padding: '1.5rem',
                      fontSize: '0.9rem',
                      lineHeight: '1.6',
                      height: '100%',
                      background: 'transparent'
                    }}
                  >
                    {selectedFile.content}
                  </SyntaxHighlighter>
                </div>
              </div>
            )}
          </div>
        </div>

        <aside className="sidebar right">
          <div className="sidebar-section">
            <h3>📊 Execution Stats</h3>
            <div className="sidebar-item">
              <div className="sidebar-label">Turn Count</div>
              <div className="sidebar-value">{status?.turn_count || session?.turn_count || 0}</div>
            </div>
            <div className="sidebar-item">
              <div className="sidebar-label">Total Tokens</div>
              <div className="sidebar-value">{(status?.tokens?.total || 0).toLocaleString()}</div>
            </div>
            <div className="sidebar-item">
              <div className="sidebar-label">Input / Output</div>
              <div className="sidebar-value">
                {(status?.tokens?.input || 0).toLocaleString()} / {(status?.tokens?.output || 0).toLocaleString()}
              </div>
            </div>
          </div>

          <div className="sidebar-section">
            <h3>🧩 Current Mode</h3>
            <div className={`sidebar-value ${isInteractive ? 'interactive' : 'god-mode'}`} style={{ color: isInteractive ? 'var(--accent-copilot)' : 'var(--accent-god)' }}>
              {isInteractive ? "🤝 Co-Pilot" : "✨ God Mode"}
            </div>
            <p style={{ fontSize: '0.8rem', color: '#64748b', marginTop: '0.5rem' }}>
              {isInteractive ? "Agent asks for approval before taking action." : "Agent operates autonomously to achieve the goal."}
            </p>
          </div>
        </aside>
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
