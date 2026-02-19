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
from datetime import datetime
import asyncio

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
        # We need to run the blocking _send method in a threadpool
        # wrapper to make it async-friendly
        response = await asyncio.to_thread(session._send, request.message)
        
        result = {
            "mode": response.mode,
            "message": response.message,
            "action": None
        }
        
        if response.mode == "ACTION" and response.action:
            # If there's an action, we might want to return it so the UI shows "Executing..."
            # For now, let's just return the plan details
            result["action"] = {
                "type": response.action.type,
                "task": response.action.task
            }
            
            # Execute action asynchronously?
            # For this MVP, we'll execute it synchronously (thread-blocked) to return the result
            # Ideally, we should use websockets or streaming for real-time logs.
            # But adhering to the plan: simpler REST first.
            
            exec_result = await asyncio.to_thread(session._execute_action, response.action)
            result["execution_result"] = exec_result
            
            # Save session
            session.save_session()
            
        return result
        
    except Exception as e:
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
