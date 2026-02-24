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
  const [diffContent, setDiffContent] = useState(null)
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

      const dRes = await fetch(`/api/diff/${session.session_id}`)
      if (dRes.ok) {
        const dData = await dRes.json()
        setDiffContent(dData.diff || null)
      }
    } catch (err) {
      console.error("Refresh failed", err)
    }
  }

  const fetchDiff = async (sid = session?.session_id) => {
    if (!sid) return
    try {
      const res = await fetch(`/api/diff/${sid}`)
      if (res.ok) {
        const data = await res.json()
        setDiffContent(data.diff || null)
      }
    } catch (err) {
      console.error("Diff fetch failed", err)
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

  useEffect(() => {
    // Phase 47: Auto-connect to current directory on initial load
    if (!isConnected && !session && !isLoading) {
      connectToRepo()
    }
  }, []) // Empty dependency array ensures this runs once on mount

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

      // Fetch diff after successful chat turn
      fetchDiff(session.session_id)

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
      <div className={`app-container theme-${theme}`} style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ textAlign: 'center' }}>
          <div className="status-dot" style={{ width: '20px', height: '20px', margin: '0 auto 1rem', animation: 'statusPulse 1.5s infinite' }}></div>
          <h2 style={{ margin: 0, fontWeight: 600, color: 'var(--text-color)', letterSpacing: '0.05em' }}>Initializing God Mode...</h2>
          <p style={{ color: 'var(--text-color)', opacity: 0.6, fontSize: '0.9rem', marginTop: '0.5rem' }}>Syncing local workspace context</p>
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
        <aside className="sidebar left threads-sidebar">
          <div className="sidebar-section">
            <h3 style={{ fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-color)', opacity: 0.7, padding: '0.5rem 0' }}>Threads</h3>
            <div className="thread-item active" style={{ padding: '0.75rem', borderRadius: '8px', background: 'rgba(168, 85, 247, 0.1)', border: '1px solid rgba(168, 85, 247, 0.2)', marginBottom: '0.5rem', cursor: 'pointer' }}>
              <div style={{ fontWeight: 600, fontSize: '0.9rem', color: 'var(--text-color)' }}>Current Session</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-color)', opacity: 0.6, marginTop: '0.2rem' }}>{session?.repo_path?.split('/').pop() || 'Workspace'}</div>
            </div>
            {/* Placeholder for past threads */}
            <div className="thread-item" style={{ padding: '0.75rem', borderRadius: '8px', marginBottom: '0.5rem', cursor: 'pointer', opacity: 0.7 }}>
              <div style={{ fontWeight: 500, fontSize: '0.9rem', color: 'var(--text-color)' }}>Implement dark mode</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-color)', opacity: 0.6, marginTop: '0.2rem' }}>8h ago</div>
            </div>
          </div>

          <div className="sidebar-section" style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', marginTop: '1rem' }}>
            <div className="sidebar-header" style={{ marginBottom: '1rem' }}>
              <h3 style={{ fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-color)', opacity: 0.7, margin: 0 }}>Workspace Files</h3>
              <button className="refresh-btn" onClick={refreshFiles} title="Refresh Files">🔄</button>
            </div>
            <div className="file-tree" style={{ flex: 1, overflowY: 'auto' }}>
              {renderTree(buildTree(files), session?.repo_path?.split('/').pop() || "project_root")}
            </div>
          </div>
        </aside>

        <div className="center-column">
          <div className="chat-area" style={{ borderRight: 'none' }}>
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
            {isLoading && <div className="typing-indicator">Thinking...</div>}
          </div>

          {/* ── Inline Chat Input ── */}
          <div className="center-input-container">
            <form onSubmit={sendMessage} className="input-form">
              <input
                type="text"
                value={input}
                onChange={e => setInput(e.target.value)}
                placeholder={isInteractive ? "Command your Co-pilot..." : "Ask God Mode anything..."}
                disabled={isLoading}
              />
              <button type="submit" disabled={isLoading || !input.trim()}>
                ↑
              </button>
            </form>
          </div>
        </div>

        {/* ── Right Pane: Code Viewer & Diff ── */}
        <div className="code-display-pane">
          <div className="code-display-header">
            <span>{selectedFile ? selectedFile.path.split('/').pop() : 'Workspace Changes'}</span>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button style={{ background: 'var(--bg-color)', border: '1px solid var(--glass-border)', color: 'var(--text-color)', padding: '0.2rem 0.6rem', borderRadius: '4px', fontSize: '0.75rem', cursor: 'pointer', fontWeight: 600 }}>Open</button>
              <button style={{ background: 'transparent', border: '1px solid var(--glass-border)', color: 'var(--text-color)', padding: '0.2rem 0.6rem', borderRadius: '4px', fontSize: '0.75rem', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.3rem' }}><span style={{ fontSize: '0.9rem' }}>⎇</span> Commit</button>
            </div>
          </div>
          <div className="code-view-container" style={{ flex: 1, overflowY: 'auto' }}>
            {diffContent ? (
              <div className="diff-viewer" style={{ padding: '1rem', fontSize: '0.85rem', fontFamily: 'monospace', lineHeight: 1.5 }}>
                {diffContent.split('\n').map((line, idx) => {
                  if (line.startsWith('--- a/') || line.startsWith('+++ b/') || line.startsWith('diff --git') || line.startsWith('index ')) {
                    return <div key={idx} style={{ color: 'var(--accent-god)', fontWeight: 600, marginTop: '1rem', paddingBottom: '0.5rem', borderBottom: '1px solid var(--glass-border)' }}>{line}</div>;
                  }
                  if (line.startsWith('@@')) {
                    return <div key={idx} style={{ color: 'var(--accent-copilot)', padding: '0.5rem 0', opacity: 0.8 }}>{line}</div>;
                  }
                  if (line.startsWith('+') && !line.startsWith('+++')) {
                    return <div key={idx} className="diff-add" style={{ backgroundColor: 'rgba(34, 197, 94, 0.1)', color: '#22c55e', padding: '0 0.5rem' }}>{line}</div>;
                  }
                  if (line.startsWith('-') && !line.startsWith('---')) {
                    return <div key={idx} className="diff-remove" style={{ backgroundColor: 'rgba(239, 68, 68, 0.1)', color: '#ef4444', padding: '0 0.5rem' }}>{line}</div>;
                  }
                  return <div key={idx} style={{ color: 'var(--text-color)', padding: '0 0.5rem', opacity: 0.8 }}>{line}</div>;
                })}
              </div>
            ) : selectedFile ? (
              <SyntaxHighlighter
                language={getLanguage(selectedFile.path)}
                style={theme === 'dark' ? vscDarkPlus : oneLight}
                customStyle={{
                  margin: 0,
                  padding: '1.5rem',
                  fontSize: '0.85rem',
                  lineHeight: '1.6',
                  height: '100%',
                  background: 'transparent'
                }}
              >
                {selectedFile.content}
              </SyntaxHighlighter>
            ) : (
              <div style={{ padding: '2rem', color: 'var(--text-color)', opacity: 0.5, textAlign: 'center' }}>
                No active changes in the workspace.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default App
