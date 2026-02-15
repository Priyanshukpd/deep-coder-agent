"""
Process Manager — Handles background processes, streaming output, and health checks.

Key Features:
- Parallel execution (Backend + Frontend)
- Real-time log streaming (no more silent 10m builds)
- Health checks (wait for localhost:PORT)
- Graceful cleanup (SIGTERM -> SIGKILL)
"""

import subprocess
import threading
import time
import os
import signal
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Callable

logger = logging.getLogger(__name__)


@dataclass
class ProcessInfo:
    cmd: str
    process: subprocess.Popen
    name: str
    output_lines: List[str] = field(default_factory=list)
    is_background: bool = False
    start_time: float = field(default_factory=time.time)


class ProcessManager:
    """
    Manages specific OS processes for the agent.
    Replaces ad-hoc subprocess.run calls with a managed approach.
    """

    def __init__(self):
        self._processes: List[ProcessInfo] = []
        self._lock = threading.Lock()

    def run_stream(self, cmd: str, cwd: str, 
                   timeout: int = 300, 
                   env: Optional[dict] = None) -> tuple[int, str]:
        """
        Run a foreground command and stream its output to stdout in real-time.
        Returns (exit_code, combined_output).
        """
        print(f"  ⚡ Executing: {cmd}")
        
        # Merge env
        full_env = os.environ.copy()
        if env:
            full_env.update(env)

        # Use Popen to capture stream
        process = subprocess.Popen(
            cmd, shell=True, cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, # Merge stderr into stdout
            text=True,
            bufsize=1, # Line buffered
            env=full_env,
            preexec_fn=os.setsid if os.name != 'nt' else None # Process group for easier kill
        )

        info = ProcessInfo(cmd=cmd, process=process, name="foreground")
        with self._lock:
            self._processes.append(info)

        # Stream output
        combined_output = []
        start_time = time.time()
        
        try:
            # Read stdout line by line
            while True:
                # Check timeout
                if time.time() - start_time > timeout:
                    print(f"\n  ⏱️  Timeout reached ({timeout}s). Killing process...")
                    self._kill_process_group(process)
                    return -1, "".join(combined_output) + "\n[TIMEOUT]"

                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                
                if line:
                    print(f"    {line.rstrip()}")
                    combined_output.append(line)
                    info.output_lines.append(line)

            rc = process.poll()
            return rc, "".join(combined_output)

        except KeyboardInterrupt:
            print("\n  🛑 Interrupted by user.")
            self._kill_process_group(process)
            return -2, "".join(combined_output) + "\n[INTERRUPTED]"
            
        finally:
            with self._lock:
                if info in self._processes:
                    self._processes.remove(info)

    def start_background(self, cmd: str, cwd: str, name: str, env: Optional[dict] = None) -> ProcessInfo:
        """
        Start a background process (e.g., database, backend server).
        Does NOT block. Returns ProcessInfo handle.
        """
        print(f"  🚀 Starting background service: {name} ({cmd})")
        
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
            
        process = subprocess.Popen(
            cmd, shell=True, cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=full_env,
            preexec_fn=os.setsid if os.name != 'nt' else None
        )
        
        info = ProcessInfo(cmd=cmd, process=process, name=name, is_background=True)
        
        # Start a thread to consume output (prevent buffer deadlock)
        t = threading.Thread(target=self._stream_background_output, args=(info,), daemon=True)
        t.start()
        
        with self._lock:
            self._processes.append(info)
            
        return info

    def _stream_background_output(self, info: ProcessInfo):
        """Consume output from background process so it doesn't hang."""
        try:
            for line in info.process.stdout:
                # We don't print generic bg output to console to avoid noise,
                # but we capture it for debugging/health checks.
                # Maybe print only if it looks like an error?
                info.output_lines.append(line)
        except Exception:
            pass

    def wait_for_port(self, port: int, timeout: int = 30) -> tuple[bool, str]:
        """
        Wait for a localhost port to be open. 
        Returns (success, reason). Checks for crashed background processes while waiting.
        """
        import socket
        start = time.time()
        while time.time() - start < timeout:
            # 1. Check if any background process died
            with self._lock:
                for p in self._processes:
                    if p.is_background and p.process.poll() is not None:
                        # Capture tail of output
                        output_tail = "".join(p.output_lines[-20:])
                        if not output_tail and p.process.stdout:
                            # Try to read whatever is left
                            try:
                                output_tail = p.process.stdout.read() or ""
                            except Exception:
                                pass
                        return False, f"Process '{p.name}' crashed with code {p.process.returncode}. Output: {output_tail.strip()}"

            # 2. Check port
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    if s.connect_ex(('localhost', port)) == 0:
                        return True, "Port open"
            except Exception:
                pass
                
            time.sleep(1)
            
        return False, f"Timed out waiting for port {port} after {timeout}s"

    def stop_all(self):
        """Stop all managed processes (foreground and background)."""
        with self._lock:
            if not self._processes:
                return
                
            print(f"  🧹 Cleaning up {len(self._processes)} processes...")
            for p in self._processes:
                if p.process.poll() is None:
                    print(f"     Stopping {p.name} (PID {p.process.pid})...")
                    self._kill_process_group(p.process)

    def _kill_process_group(self, process: subprocess.Popen):
        """Kill the entire process group (process + children)."""
        try:
            if os.name != 'nt':
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()
                
            # Wait briefly
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if os.name != 'nt':
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
        except OSError:
            pass # Already dead
