"""
Impact Analysis — Dependency graph-based change impact estimation.

Uses the DependencyGraph to determine:
    - What files are affected by changes to a given file
    - Risk classification of changes (low/medium/high/critical)
    - Suggested review scope
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from agent.core.context_graph import DependencyGraph

logger = logging.getLogger(__name__)


class ImpactLevel(Enum):
    LOW = auto()         # 0-2 dependents
    MEDIUM = auto()      # 3-5 dependents
    HIGH = auto()        # 6-10 dependents
    CRITICAL = auto()    # 11+ dependents


@dataclass
class FileImpact:
    """Impact analysis for a single changed file."""
    file_path: str
    direct_dependents: list[str] = field(default_factory=list)
    transitive_dependents: list[str] = field(default_factory=list)
    impact_level: ImpactLevel = ImpactLevel.LOW

    @property
    def total_affected(self) -> int:
        return len(self.transitive_dependents)


@dataclass
class ImpactReport:
    """Aggregate impact analysis report."""
    file_impacts: list[FileImpact] = field(default_factory=list)
    total_files_affected: int = 0
    max_impact_level: ImpactLevel = ImpactLevel.LOW
    requires_approval: bool = False
    summary: str = ""

    @property
    def is_high_risk(self) -> bool:
        return self.max_impact_level in {ImpactLevel.HIGH, ImpactLevel.CRITICAL}


class ImpactAnalyzer:
    """
    Analyzes the impact of file changes using the dependency graph.
    
    Usage:
        graph = DependencyGraph()
        graph.scan_python_directory("agent")
        
        analyzer = ImpactAnalyzer(graph)
        report = analyzer.analyze(["agent/state.py", "agent/intent.py"])
    """

    def __init__(self, graph: DependencyGraph = None):
        self._graph = graph or DependencyGraph()

    def analyze(self, changed_files: list[str]) -> ImpactReport:
        """
        Analyze the impact of a set of file changes.
        
        Returns an ImpactReport with impact levels and affected files.
        """
        all_affected = set()
        file_impacts = []

        for file_path in changed_files:
            # Convert file path to module name
            module_name = self._file_to_module(file_path)

            # Get direct and transitive dependents
            direct = list(self._graph.get_importers(module_name))
            transitive = list(self._graph.get_transitive_importers(module_name))

            # Classify impact
            impact_level = self._classify_impact(len(transitive))

            fi = FileImpact(
                file_path=file_path,
                direct_dependents=direct,
                transitive_dependents=transitive,
                impact_level=impact_level,
            )
            file_impacts.append(fi)
            all_affected.update(transitive)

        # Build report
        max_level = max(
            (fi.impact_level for fi in file_impacts),
            default=ImpactLevel.LOW,
            key=lambda x: x.value,
        )

        report = ImpactReport(
            file_impacts=file_impacts,
            total_files_affected=len(all_affected),
            max_impact_level=max_level,
            requires_approval=max_level in {ImpactLevel.HIGH, ImpactLevel.CRITICAL},
            summary=self._build_summary(file_impacts, all_affected),
        )

        logger.info(f"Impact analysis: {report.summary}")
        return report

    @staticmethod
    def _classify_impact(dependent_count: int) -> ImpactLevel:
        if dependent_count <= 2:
            return ImpactLevel.LOW
        elif dependent_count <= 5:
            return ImpactLevel.MEDIUM
        elif dependent_count <= 10:
            return ImpactLevel.HIGH
        else:
            return ImpactLevel.CRITICAL

    @staticmethod
    def _file_to_module(file_path: str) -> str:
        """Convert a file path to a Python module name."""
        module = file_path.replace("/", ".").replace("\\", ".")
        if module.endswith(".py"):
            module = module[:-3]
        return module

    @staticmethod
    def _build_summary(impacts: list[FileImpact], all_affected: set) -> str:
        if not impacts:
            return "No files changed"

        parts = [f"{len(impacts)} file(s) changed"]
        parts.append(f"{len(all_affected)} total affected")

        max_impact = max(fi.impact_level for fi in impacts)
        parts.append(f"max impact: {max_impact.name}")

        return ", ".join(parts)
