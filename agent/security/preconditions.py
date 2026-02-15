import hashlib
import subprocess
import os
from typing import List, Dict, Optional
from dataclasses import dataclass

@dataclass
class PreconditionViolation:
    check_type: str  # "GIT_HEAD" or "FILE_CHECKSUM"
    details: str
    severity: str = "BLOCKING"

class PreconditionChecker:
    """
    Ensures that the environment (Git state, File contents) matches 
    the Agent's expected state before execution proceeds.
    """

    @staticmethod
    def get_git_head(repo_path: str = ".") -> str:
        """Capture the current git commit hash."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            # Not a git repo or error
            return "unknown_or_no_git"

    @staticmethod
    def get_file_checksum(file_path: str) -> Optional[str]:
        """Calculate MD5 checksum of a file."""
        if not os.path.exists(file_path):
            return None
        
        try:
            with open(file_path, "rb") as f:
                file_hash = hashlib.md5()
                while chunk := f.read(8192):
                    file_hash.update(chunk)
            return file_hash.hexdigest()
        except Exception:
            return None

    @staticmethod
    def check_git_consistency(expected_head: str, repo_path: str = ".") -> Optional[PreconditionViolation]:
        """Verify Git HEAD hasn't moved."""
        current_head = PreconditionChecker.get_git_head(repo_path)
        
        if expected_head == "unknown_or_no_git":
            # If we started without git, we probably don't enforce it, or we assume safe.
            # For strict mode, we might want to block. For now, pass.
            return None
            
        if current_head != expected_head:
            return PreconditionViolation(
                check_type="GIT_HEAD",
                details=f"Drift detected! Expected HEAD {expected_head[:7]}, found {current_head[:7]}."
            )
        return None

    @staticmethod
    def check_file_consistency(expected_checksums: Dict[str, str]) -> List[PreconditionViolation]:
        """Verify target files haven't changed."""
        violations = []
        for file_path, expected_hash in expected_checksums.items():
            current_hash = PreconditionChecker.get_file_checksum(file_path)
            
            if current_hash is None:
                violations.append(PreconditionViolation(
                    check_type="FILE_MISSING",
                    details=f"File {file_path} went missing during execution."
                ))
            elif current_hash != expected_hash:
                violations.append(PreconditionViolation(
                    check_type="FILE_CHECKSUM",
                    details=f"File {file_path} was modified externally."
                ))
        return violations
