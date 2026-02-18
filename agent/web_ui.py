"""
Streamlit Web UI for God Mode Agent.

Provides a modern chat interface for the agent using Streamlit.
Wraps the ChatSession engine to provide a persistent, visual experience.

Usage:
    streamlit run agent.web_ui -- --repo /path/to/repo
"""

import streamlit as st
import os
import sys
import argparse
from datetime import datetime

# Add the project root to sys.path so we can import agent modules
sys.path.append(os.path.abspath(os.getcwd()))

from agent.config import AgentConfig
from agent.core.factory import create_provider
from agent.core.chat import ChatSession, ChatAction
import logging
import io
import contextlib

# ── Log Handler ─────────────────────────────────────────────────

class StreamlitLogHandler(logging.Handler):
    """Handler that updates a Streamlit container with logs."""
    def __init__(self, container):
        super().__init__()
        self.container = container
        self.log_area = container.empty()
        self.buffer = []

    def emit(self, record):
        msg = self.format(record)
        self.buffer.append(msg)
        if len(self.buffer) > 100:
            self.buffer.pop(0)
        try:
            self.log_area.code("\n".join(self.buffer), language="text")
        except Exception:
            pass  # Ignore NoSessionContext errors from background threads


class StreamlitStream:
    """Redirects stdout to a Streamlit container."""
    def __init__(self, container):
        self.container = container
        self.log_area = container.empty()
        self.buffer = []

    def write(self, data):
        # Filter out empty writes
        if not data: return
        
        # Split by lines to handle partial writes effectively
        lines = data.split('\n')
        for line in lines:
            if line:
                self.buffer.append(line)
        
        if len(self.buffer) > 100:
             self.buffer = self.buffer[-100:]
             
        try:
            self.log_area.code("\n".join(self.buffer), language="text")
        except Exception:
            pass  # Ignore NoSessionContext errors

    def flush(self):
        pass

# ── Config ──────────────────────────────────────────────────────

st.set_page_config(
    page_title="God Mode Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for premium feel
st.markdown("""
<style>
    .stChatMessage {
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 0.5rem;
    }
    .stChatMessage[data-testid="stChatMessageUser"] {
        background-color: #f0f2f6;
        border-left: 4px solid #4a90e2;
    }
    .stChatMessage[data-testid="stChatMessageAssistant"] {
        background-color: #e8f5e9;
        border-left: 4px solid #66bb6a;
    }
    .stCodeBlock {
        border-radius: 0.5rem;
    }
    h1 {
        font-family: 'Inter', sans-serif;
        font-weight: 700;
        background: -webkit-linear-gradient(45deg, #4a90e2, #9b59b6);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .mode-badge {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: bold;
        font-size: 0.8rem;
        margin-bottom: 10px;
    }
    .god-mode {
        background: rgba(155, 89, 182, 0.1);
        color: #9b59b6;
        border: 1px solid rgba(155, 89, 182, 0.2);
        animation: pulse 2s infinite;
    }
    .copilot-mode {
        background: rgba(74, 144, 226, 0.1);
        color: #4a90e2;
        border: 1px solid rgba(74, 144, 226, 0.2);
    }
    @keyframes pulse {
        0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(155, 89, 182, 0.4); }
        70% { transform: scale(1.02); box-shadow: 0 0 0 10px rgba(155, 89, 182, 0); }
        100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(155, 89, 182, 0); }
    }
    .stChatInputContainer {
        border-top: 1px solid rgba(128, 128, 128, 0.1);
        padding-top: 20px;
        background: transparent !important;
    }
    .fixed-controls-container {
        position: fixed;
        bottom: 85px;
        left: 20rem; /* Adjusted for sidebar */
        right: 2rem;
        z-index: 999;
        background: rgba(255, 255, 255, 0.8);
        backdrop-filter: blur(10px);
        padding: 10px 20px;
        border-radius: 15px;
        border: 1px solid rgba(128, 128, 128, 0.1);
        display: flex;
        justify-content: space-between;
        align-items: center;
        box-shadow: 0 -5px 15px rgba(0,0,0,0.05);
    }
    @media (max-width: 768px) {
        .fixed-controls-container {
            left: 1rem;
            right: 1rem;
        }
    }
</style>
""", unsafe_allow_html=True)


# ── Session State Setup ─────────────────────────────────────────

def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".", help="Path to repo")
    try:
        args = parser.parse_args()
        return args
    except SystemExit:
        return argparse.Namespace(repo=".")

if "session" not in st.session_state:
    # Initialize the agent session
    args = get_args()
    repo_path = os.path.abspath(args.repo)
    
    config = AgentConfig()
    if not config.has_api_key:
        st.error("⚠️ No API key configured. Set TOGETHER_API_KEY env var.")
        st.stop()

    provider = create_provider(config)
    
    with st.spinner(f"📡 Scanning repository: {repo_path}..."):
        session = ChatSession(provider, repo_path)
        # Pre-load context
        session._load_repo_context()
        st.session_state.session = session
        st.session_state.messages = []  # Store UI-friendly message history

session = st.session_state.session


# ── Sidebar ─────────────────────────────────────────────────────

with st.sidebar:
    st.title("🤖 God Mode")
    st.caption(f"v{sys.modules.get('agent.cli').VERSION if 'agent.cli' in sys.modules else '7.5.1'}")
    
    st.divider()
    
    st.subheader("📂 Repository")
    new_repo_path = st.text_input(
        "Target Path",
        value=session._repo_path,
        help="Paste the absolute path to a different repository to switch context."
    )
    
    if new_repo_path != session._repo_path:
        # Validate path
        abs_new_path = os.path.abspath(new_repo_path)
        if os.path.exists(abs_new_path) and os.path.isdir(abs_new_path):
            with st.spinner(f"🔄 Switching to: {abs_new_path}..."):
                # Create a new session with the same provider
                new_session = ChatSession(session._provider, abs_new_path)
                new_session._load_repo_context()
                
                # Update session state
                st.session_state.session = new_session
                # Optionally clear messages or keep them? Usually better to clear for new context
                st.session_state.messages = [] 
                st.success(f"Context switched to {os.path.basename(abs_new_path)}")
                st.rerun()
        else:
            st.error("Invalid directory path.")
    
    st.code(session._repo_path, language="bash")
    
    # Extract stats from repo context if available
    if session._repo_context:
        try:
            # Quick hack to parse the context string for stats
            lines = session._repo_context.split('\n')
            files = next((l.split(': ')[1] for l in lines if l.startswith('Files: ')), "?")
            stack = next((l.split(': ')[1] for l in lines if l.startswith('Stack: ')), "?")
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Files", files)
            with col2:
                st.metric("Stack", stack)
        except:
            pass
            
    st.divider()
    
    st.subheader("📊 Session Stats")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Turns", session._turn_count)
    with col2:
        st.metric("LLM Calls", session._provider.call_count)
    
    st.divider()
    
    st.subheader("🎛️ Control Mode")
    mode_options = ["🚀 Auto (God Mode)", "✋ Interactive (Co-pilot)"]
    default_index = 1 if session.interactive_mode else 0
    selected_mode = st.radio(
        "Execution Strategy",
        options=mode_options,
        index=default_index,
        help="Auto: Agent executes autonomously. Interactive: Agent asks for approval before each step."
    )
    
    # Sync UI selection to session state
    new_interactive = (selected_mode == mode_options[1])
    if new_interactive != session.interactive_mode:
        session.interactive_mode = new_interactive
        st.toast(f"Switched to {'Interactive' if new_interactive else 'Auto'} mode")
    
    # Token usage
    provider = session._provider
    if provider.total_tokens > 0:
        st.divider()
        st.subheader("🪙 Token Usage")
        col_in, col_out = st.columns(2)
        with col_in:
            st.metric("Input", f"{provider.total_input_tokens:,}")
        with col_out:
            st.metric("Output", f"{provider.total_output_tokens:,}")
        st.metric("Total", f"{provider.total_tokens:,}")
    
    if st.button("🗑️ Clear Conversation", type="primary"):
        session._messages.clear()
        session._turn_count = 0
        st.session_state.messages = []
        st.rerun()

    st.divider()
    
    with st.expander("📜 Live Logs", expanded=True):
        log_container = st.empty()
        
        # Avoid adding duplicate handlers on reruns
        root = logging.getLogger()
        # Remove old StreamlitLogHandlers if any (they hold stale containers)
        root.handlers = [h for h in root.handlers if not isinstance(h, StreamlitLogHandler)]
        
        handler = StreamlitLogHandler(log_container)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)


# ── Main Chat Interface ─────────────────────────────────────────

st.title("God Mode Agent")

# Display history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "action_result" in msg:
            # Show status indicator (collapsed)
            is_failed = msg.get("action_failed", False)
            status_label = "❌ Action Failed" if is_failed else "✅ Action Complete"
            status_state = "error" if is_failed else "complete"
            with st.status(status_label, expanded=False, state=status_state):
                st.write("See result below.")
            # Show result OUTSIDE the status block so it's always visible
            st.markdown(msg["action_result"])

# ── Dynamic Mode Controls (Fixed at Bottom) ─────────────────────

# We use an empty container and then fill it to keep it at the end of the script,
# but our CSS will handle the actual "fixed" positioning.
st.markdown('<div class="fixed-controls-container">', unsafe_allow_html=True)
col1, col2 = st.columns([1, 2])

with col1:
    # Quick toggle
    mode_btn_label = "✋ Switch to Co-pilot" if not session.interactive_mode else "🚀 Switch to God Mode"
    if st.button(mode_btn_label, use_container_width=True, key="fixed_mode_toggle"):
        session.interactive_mode = not session.interactive_mode
        st.rerun()

with col2:
    # Status Badge with centered alignment
    if not session.interactive_mode:
        st.markdown('<div style="text-align: right;"><div class="mode-badge god-mode" style="margin-bottom:0;">✨ GOD MODE ACTIVE</div></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div style="text-align: right;"><div class="mode-badge copilot-mode" style="margin-bottom:0;">🤝 CO-PILOT ACTIVE</div></div>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# Handle Input
placeholder = "Give God Mode a task..." if not session.interactive_mode else "Command your Co-pilot..."
if prompt := st.chat_input(placeholder):
    # 1. User Message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 2. Assistant Response
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                response = session._send(prompt)
                
                # Show the textual response
                st.markdown(response.message)
                
                action_output = None
                action_failed = False
                
                # 3. Execute Action (if any)
                if response.mode == "ACTION" and response.action:
                    action = response.action
                    status_label = f"⚡ Executing: {action.type} — {action.task}"
                    
                    with st.status(status_label, expanded=True) as status:
                        st.write("Running pipeline...")
                        
                        # Create a stdout capture stream for this status block
                        status_log_container = st.empty()
                        stream = StreamlitStream(status_log_container)
                        
                        try:
                            with contextlib.redirect_stdout(stream):
                                result = session._execute_action(action)
                        except Exception as e:
                            st.error(f"Pipeline error: {e}")
                            result = f"❌ Pipeline crashed: {e}"
                            session.last_action_success = False
                            
                        if session.last_action_success:
                            status.update(label="✅ Action Complete", state="complete", expanded=False)
                        else:
                            status.update(label="❌ Action Failed", state="error", expanded=False)
                    
                    # Display result OUTSIDE status block — always visible
                    st.markdown(result)
                    
                    action_output = result
                    action_failed = not session.last_action_success
                        
                    # Add execution result to session history for LLM context
                    session._messages.append(session._messages[-1].__class__(
                        role="assistant",
                        content=f"[Execution result]: {result}",
                        timestamp=datetime.now().isoformat()
                    ))

                # Save to UI history
                msg_data = {"role": "assistant", "content": response.message}
                if action_output:
                    msg_data["action_result"] = action_output
                st.session_state.messages.append(msg_data)
                
                # Auto-save session to disk
                session.save_session()
                
            except Exception as e:
                st.error(f"Error: {str(e)}")
