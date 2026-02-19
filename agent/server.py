"""
FastAPI Backend for God Mode Agent.

Exposes the ChatSession and TaskExecutor as a REST API.
"""

from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import sys
from typing import Dict, Optional, List, Any
import asyncio
import queue
import threading
import json
from fastapi.responses import StreamingResponse

class SessionLogger:
    def __init__(self):
        self.queues = []
    
    def write(self, msg):
        for q in self.queues:
            q.put(msg)
            
    def flush(self):
        pass

session_loggers: Dict[str, SessionLogger] = {}

class ThreadedStdout:
    def __init__(self, original_stdout):
        self.original = original_stdout
        self.local = threading.local()
    
    def write(self, msg):
        logger = getattr(self.local, 'logger', None)
        if logger is not None:
            logger.write(msg)
        self.original.write(msg)
        
    def flush(self):
        self.original.flush()

global_threaded_stdout = ThreadedStdout(sys.stdout)
sys.stdout = global_threaded_stdout

# Ensure agent modules are importable
sys.path.append(os.path.abspath(os.getcwd()))

from agent.config import AgentConfig
from agent.core.factory import create_provider
from agent.core.chat import ChatSession

app = FastAPI(title="God Mode Agent API", version="1.0.0")

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this to frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store
# Key: session_id, Value: ChatSession instance
sessions: Dict[str, ChatSession] = {}

class ConnectRequest(BaseModel):
    repo_path: str = "."

class ChatRequest(BaseModel):
    session_id: str
    message: str

class ModeRequest(BaseModel):
    session_id: str
    mode: str  # "auto" or "interactive"

class ConnectResponse(BaseModel):
    session_id: str
    repo_path: str
    file_count: int
    stack: str

@app.post("/api/connect", response_model=ConnectResponse)
async def connect(request: ConnectRequest):
    """Initialize a new agent session."""
    try:
        repo_path = os.path.abspath(request.repo_path)
        if not os.path.exists(repo_path):
            raise HTTPException(status_code=400, detail="Repository path does not exist.")
        
        config = AgentConfig()
        if not config.has_api_key:
             raise HTTPException(status_code=500, detail="TOGETHER_API_KEY not configured.")
             
        provider = create_provider(config)
        session = ChatSession(provider, repo_path)
        
        # Load context
        session._load_repo_context()
        
        # Extract stats from context string (hacky but works with current implementation)
        file_count = 0
        stack = "Unknown"
        if session._repo_context:
            for line in session._repo_context.split('\n'):
                if line.startswith('Files: '):
                    try:
                        file_count = int(line.split(': ')[1])
                    except: pass
                if line.startswith('Stack: '):
                    stack = line.split(': ')[1]

        sessions[session._session_id] = session
        
        return ConnectResponse(
            session_id=session._session_id,
            repo_path=repo_path,
            file_count=file_count,
            stack=stack
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/chat")
async def chat(request: ChatRequest):
    """Send a message to the agent."""
    session_id = request.session_id
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    
    session = sessions[session_id]
    
    try:
        def run_with_logs():
            if session_id not in session_loggers:
                session_loggers[session_id] = SessionLogger()
            global_threaded_stdout.local.logger = session_loggers[session_id]
            try:
                # This call may trigger internal research/actions which will now be captured
                response = session._send_agentic(request.message)
                
                result = {
                    "mode": response.mode,
                    "message": response.message,
                    "action": None
                }

                if response.mode == "ACTION" and response.action:
                     # This handle standard code actions (generate/fix/modify)
                     # Internal 'research' actions are already handled and synthesized inside _send_agentic
                     result["action"] = {
                         "type": response.action.type,
                         "task": response.action.task
                     }
                     # We might need to run the action again if it wasn't a research action handled internally
                     # Note: _send_agentic only loops for 'research', others return immediately
                     if response.action.type != "research":
                         exec_result = session._execute_action(response.action)
                         result["execution_result"] = exec_result

                session.save_session()
                return result
            finally:
                global_threaded_stdout.local.logger = None

        final_result = await asyncio.to_thread(run_with_logs)
        return final_result

    except Exception as e:
        logger.exception("Error in chat handler")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history/{session_id}")
async def get_history(session_id: str):
    """Get message history."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    
    session = sessions[session_id]
    return {
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp,
                "action_taken": m.action_taken
            } 
            for m in session._messages
        ]
    }

@app.get("/api/stream/{session_id}")
async def stream_logs(session_id: str):
    """Stream execution logs via Server-Sent Events (SSE)."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    
    if session_id not in session_loggers:
        session_loggers[session_id] = SessionLogger()
    
    logger = session_loggers[session_id]
    q = queue.Queue()
    logger.queues.append(q)
    
    async def event_generator():
        try:
            while True:
                try:
                    # Non-blocking get with short sleep is async friendly
                    msg = q.get(block=False)
                    if msg is None:
                        break
                    
                    if msg.startswith("ST_STEP:"):
                        step_name = msg.replace("ST_STEP:", "").strip()
                        yield f"data: {json.dumps({'status': step_name})}\n\n"
                    else:
                        yield f"data: {json.dumps({'log': msg})}\n\n"
                except queue.Empty:
                    await asyncio.sleep(0.1)
        finally:
            if q in logger.queues:
                logger.queues.remove(q)
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/mode")
async def set_mode(request: ModeRequest):
    """Toggle interactive mode."""
    if request.session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    
    session = sessions[request.session_id]
    if request.mode.lower() in ["interactive", "copilot"]:
        session.interactive_mode = True
    else:
        session.interactive_mode = False
        
    return {"interactive_mode": session.interactive_mode}

@app.get("/api/status/{session_id}")
async def get_status(session_id: str):
    """Get detailed session status."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    
    session = sessions[session_id]
    provider = session._provider
    
    return {
        "turn_count": session._turn_count,
        "interactive_mode": session.interactive_mode,
        "tokens": {
            "input": provider.total_input_tokens,
            "output": provider.total_output_tokens,
            "total": provider.total_tokens
        }
    }

@app.get("/api/files/{session_id}")
async def get_files(session_id: str):
    """Get the list of files in the repository."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    
    session = sessions[session_id]
    files = []
    
    # Common junk patterns to ignore (very minimal now)
    ignore_files = {".DS_Store", "Thumbs.db"}
    
    for root, dirs, file_list in os.walk(session._repo_path):
        # Filter out only extreme internal/junk directories
        dirs[:] = [d for d in dirs if d != '__pycache__' 
                   and d != 'node_modules'
                   and d != 'dist'
                   and d != 'build'
                   and d != '.git'
                   and d != '.agent_log']
        
        for f in file_list:
            # Hide only macos specific junk or hidden temp files
            if f in ignore_files or f.startswith('._'):
                continue
                
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, session._repo_path)
            files.append(rel_path)
    
    return {"files": sorted(files)}

@app.get("/api/files/content/{session_id}")
async def get_file_content(session_id: str, path: str):
    """Read content of a file."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    
    session = sessions[session_id]
    full_path = os.path.join(session._repo_path, path)
    
    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="File not found.")
        
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"content": content, "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/files/{session_id}")
async def delete_file(session_id: str, path: str):
    """Delete a file from the repository."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    
    session = sessions[session_id]
    full_path = os.path.join(session._repo_path, path)
    
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found.")
        
    try:
        if os.path.isfile(full_path):
            os.remove(full_path)
        else:
            import shutil
            shutil.rmtree(full_path)
        return {"status": "success", "message": f"Deleted {path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
