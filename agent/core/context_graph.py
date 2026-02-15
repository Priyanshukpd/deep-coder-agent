"""
Graph-Based Context Retrieval.

Builds a dependency/import graph of the codebase to enable
intelligent context retrieval for the agent's planning phase.

Supports:
    - Python import graph construction
    - Reverse dependency lookup (what depends on X?)
    - Transitive closure for impact analysis
    - Subgraph extraction for focused context
"""

from __future__ import annotations

import ast
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GraphNode:
    """A node in the dependency graph (represents a module/file)."""
    name: str
    file_path: str
    imports: list[str] = field(default_factory=list)
    imported_by: list[str] = field(default_factory=list)
    line_count: int = 0
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)


class DependencyGraph:
    """
    Builds and queries a dependency graph of the codebase.
    
    Used for:
        - Impact analysis (what files are affected by changes to X?)
        - Context retrieval (what context does the agent need?)
        - Scope estimation (how many files are involved?)
    """

    def __init__(self):
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[str, set[str]] = defaultdict(set)      # imports
        self._reverse: dict[str, set[str]] = defaultdict(set)     # imported_by

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(deps) for deps in self._edges.values())

    def add_node(self, name: str, file_path: str, **kwargs) -> GraphNode:
        """Add or update a node in the graph."""
        if name in self._nodes:
            node = self._nodes[name]
            node.file_path = file_path
            for k, v in kwargs.items():
                setattr(node, k, v)
        else:
            node = GraphNode(name=name, file_path=file_path, **kwargs)
            self._nodes[name] = node
        return node

    def add_edge(self, source: str, target: str):
        """Add a dependency edge: source imports target."""
        self._edges[source].add(target)
        self._reverse[target].add(source)

    def get_imports(self, module: str) -> set[str]:
        """Get direct imports of a module."""
        return self._edges.get(module, set())

    def get_importers(self, module: str) -> set[str]:
        """Get modules that directly import this module (reverse deps)."""
        return self._reverse.get(module, set())

    def get_transitive_importers(self, module: str) -> set[str]:
        """
        Get all modules that transitively depend on this module.
        
        Used for impact analysis: "if I change X, what else could break?"
        """
        visited = set()
        queue = [module]

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            for importer in self._reverse.get(current, set()):
                if importer not in visited:
                    queue.append(importer)

        visited.discard(module)  # Remove self
        return visited

    def get_subgraph(self, modules: list[str], depth: int = 2) -> "DependencyGraph":
        """
        Extract a subgraph around the given modules up to a given depth.
        
        Used for focused context retrieval.
        """
        subgraph = DependencyGraph()
        visited = set()
        queue = [(m, 0) for m in modules]

        while queue:
            current, d = queue.pop(0)
            if current in visited or d > depth:
                continue
            visited.add(current)

            if current in self._nodes:
                node = self._nodes[current]
                subgraph.add_node(current, node.file_path,
                                  line_count=node.line_count,
                                  classes=node.classes,
                                  functions=node.functions)

                # Add outgoing edges
                for dep in self._edges.get(current, set()):
                    subgraph.add_edge(current, dep)
                    if dep not in visited:
                        queue.append((dep, d + 1))

                # Add incoming edges
                for imp in self._reverse.get(current, set()):
                    subgraph.add_edge(imp, current)
                    if imp not in visited:
                        queue.append((imp, d + 1))

        return subgraph

    def scan_python_directory(self, directory: str):
        """
        Scan a directory for Python files and build the dependency graph.
        
        Uses AST parsing to extract imports.
        """
        root = Path(directory)
        py_files = list(root.rglob("*.py"))

        for py_file in py_files:
            rel_path = py_file.relative_to(root)
            module_name = str(rel_path.with_suffix("")).replace("/", ".")

            try:
                content = py_file.read_text()
                tree = ast.parse(content)

                classes = []
                functions = []
                imports = []

                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        classes.append(node.name)
                    elif isinstance(node, ast.FunctionDef):
                        functions.append(node.name)
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.append(alias.name)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            imports.append(node.module)

                graph_node = self.add_node(
                    module_name,
                    str(py_file),
                    line_count=len(content.split("\n")),
                    classes=classes,
                    functions=functions,
                )
                graph_node.imports = imports

                for imp in imports:
                    self.add_edge(module_name, imp)

            except (SyntaxError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to parse {py_file}: {e}")

        logger.info(
            f"Dependency graph: {self.node_count} nodes, {self.edge_count} edges"
        )

    def to_dict(self) -> dict:
        """Serialize graph to dictionary."""
        return {
            name: {
                "file": node.file_path,
                "imports": list(self._edges.get(name, set())),
                "imported_by": list(self._reverse.get(name, set())),
                "lines": node.line_count,
                "classes": node.classes,
                "functions": node.functions,
            }
            for name, node in self._nodes.items()
        }
