"""
Docker Inspector Tool.
Provides programmatic access to Docker container state for debugging.
"""
import subprocess
import json
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class DockerInspector:
    def list_containers(self, all: bool = False) -> List[Dict]:
        """Get running containers as structured data."""
        try:
            cmd = ["docker", "ps", "--format", "{{json .}}"]
            if all:
                cmd.append("-a")
                
            result = subprocess.run(cmd, capture_output=True, text=True)
            containers = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    try:
                        containers.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            return containers
        except Exception as e:
            logger.warning(f"Failed to list docker containers: {e}")
            return []

    def get_logs(self, container_id: str, tail: int = 200) -> str:
        """Get logs for a container."""
        try:
            cmd = ["docker", "logs", "--tail", str(tail), container_id]
            result = subprocess.run(cmd, capture_output=True, text=True)
            return (result.stdout + result.stderr).strip()
        except Exception as e:
            return f"Error getting logs: {e}"

    def inspect(self, container_id: str) -> Dict:
        """Get low-level container info (env vars, networks, mounts)."""
        try:
            cmd = ["docker", "inspect", container_id]
            result = subprocess.run(cmd, capture_output=True, text=True)
            data = json.loads(result.stdout)
            return data[0] if data else {}
        except Exception:
            return {}

    def exec_command(self, container_id: str, command: str) -> str:
        """Run a command inside a running container."""
        try:
            # interactive=False, tty=False
            cmd = ["docker", "exec", container_id, "sh", "-c", command]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return (result.stdout + result.stderr).strip()
        except Exception as e:
            return f"Exec failed: {e}"
