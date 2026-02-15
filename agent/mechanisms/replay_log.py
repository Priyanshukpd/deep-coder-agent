"""
Replay Log — Tool Hash + Sampling Hash + Manifest.

Architecture §1 (Forensic Integrity):
    - Log every tool invocation with input/output hashes
    - Include sampling_policy_hash for LLM calls
    - Include toolchain_manifest for reproducibility
    - Enable exact replay verification
"""

from __future__ import annotations

import hashlib
import json
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class ReplayEntry:
    """Single entry in the replay log — one tool invocation."""
    sequence_id: int
    timestamp: float
    tool_name: str
    input_hash: str
    output_hash: str
    sampling_policy_hash: str = ""
    duration_ms: float = 0.0
    success: bool = True
    error: str = ""
    metadata: dict = field(default_factory=dict)


class ReplayLog:
    """
    Forensic replay log for deterministic verification.
    
    Records every tool invocation with content hashes.
    Enables replay verification: re-running the same inputs
    should produce the same output hashes.
    """

    def __init__(self, session_id: str = ""):
        self.session_id = session_id
        self._entries: list[ReplayEntry] = []
        self._sequence = 0
        self._toolchain_manifest: dict = {}
        self._start_time = time.time()

    @property
    def entries(self) -> list[ReplayEntry]:
        return list(self._entries)

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def set_toolchain_manifest(self, manifest: dict):
        """Set the toolchain manifest for this session."""
        self._toolchain_manifest = manifest

    @staticmethod
    def hash_content(content: Any) -> str:
        """Hash arbitrary content for the replay log."""
        if isinstance(content, str):
            data = content
        elif isinstance(content, bytes):
            data = content.decode("utf-8", errors="replace")
        else:
            data = json.dumps(content, sort_keys=True, default=str)
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def record(
        self,
        tool_name: str,
        input_data: Any,
        output_data: Any,
        sampling_policy_hash: str = "",
        success: bool = True,
        error: str = "",
        metadata: dict = None,
    ) -> ReplayEntry:
        """
        Record a tool invocation in the replay log.
        
        Args:
            tool_name: Name of the tool (e.g., "run_command", "llm_complete")
            input_data: The input to the tool
            output_data: The output from the tool
            sampling_policy_hash: For LLM calls, the sampling policy hash
            success: Whether the tool succeeded
            error: Error message if failed
            metadata: Additional context
        """
        self._sequence += 1

        entry = ReplayEntry(
            sequence_id=self._sequence,
            timestamp=time.time(),
            tool_name=tool_name,
            input_hash=self.hash_content(input_data),
            output_hash=self.hash_content(output_data),
            sampling_policy_hash=sampling_policy_hash,
            success=success,
            error=error,
            metadata=metadata or {},
        )

        self._entries.append(entry)

        logger.debug(
            f"Replay[{entry.sequence_id}] {tool_name}: "
            f"in={entry.input_hash} out={entry.output_hash}"
        )

        return entry

    def verify_against(self, other: "ReplayLog") -> list[str]:
        """
        Verify this log against another replay log.
        
        Returns list of mismatches (empty if identical).
        Used for Architecture §2.B: Replay runs must match hash exactly.
        """
        mismatches = []

        if len(self._entries) != len(other._entries):
            mismatches.append(
                f"Entry count mismatch: {len(self._entries)} vs {len(other._entries)}"
            )
            return mismatches

        for i, (a, b) in enumerate(zip(self._entries, other._entries)):
            if a.tool_name != b.tool_name:
                mismatches.append(f"[{i}] Tool mismatch: {a.tool_name} vs {b.tool_name}")
            if a.input_hash != b.input_hash:
                mismatches.append(f"[{i}] Input hash mismatch: {a.input_hash} vs {b.input_hash}")
            if a.output_hash != b.output_hash:
                mismatches.append(f"[{i}] Output hash mismatch: {a.output_hash} vs {b.output_hash}")

        return mismatches

    def to_json(self) -> str:
        """Serialize the replay log to JSON."""
        return json.dumps({
            "session_id": self.session_id,
            "toolchain_manifest": self._toolchain_manifest,
            "start_time": self._start_time,
            "entries": [asdict(e) for e in self._entries],
        }, indent=2, default=str)

    @classmethod
    def from_json(cls, data: str) -> "ReplayLog":
        """Deserialize a replay log from JSON."""
        obj = json.loads(data)
        log = cls(session_id=obj.get("session_id", ""))
        log._toolchain_manifest = obj.get("toolchain_manifest", {})
        log._start_time = obj.get("start_time", time.time())

        for entry_data in obj.get("entries", []):
            entry = ReplayEntry(**entry_data)
            log._entries.append(entry)
            log._sequence = max(log._sequence, entry.sequence_id)

        return log

    def session_hash(self) -> str:
        """Compute a hash of the entire session for comparison."""
        combined = "|".join(
            f"{e.tool_name}:{e.input_hash}:{e.output_hash}"
            for e in self._entries
        )
        return hashlib.sha256(combined.encode()).hexdigest()
