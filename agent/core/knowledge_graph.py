
"""
Knowledge Graph Module.
Parses code to build a dependency graph of imports and definitions.
"""
import os
import ast
import logging
from typing import Dict, Set, List

logger = logging.getLogger(__name__)

class KnowledgeGraph:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.graph: Dict[str, Set[str]] = {} # file -> set(files that import it)
        self.reverse_graph: Dict[str, Set[str]] = {} # file -> set(files it imports)

    def build(self):
        """Scan the repo and build the graph."""
        self.graph.clear()
        self.reverse_graph.clear()
        
        for root, _, files in os.walk(self.repo_path):
            for f in files:
                if f.endswith('.py'):
                    self._parse_python(os.path.join(root, f))
                else:
                    self._parse_universal(os.path.join(root, f))



    def _parse_python(self, file_path: str):
        rel_path = os.path.relpath(file_path, self.repo_path)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=file_path)
            
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    self._process_py_import(node, rel_path)
        except Exception as e:
            logger.debug(f"Failed to parse {rel_path}: {e}")

        except Exception:
            pass

    def _parse_universal(self, file_path: str):
        """Generic regex parser for other languages."""
        rel_path = os.path.relpath(file_path, self.repo_path)
        ext = os.path.splitext(file_path)[1].lower()
        
        # Regex patterns for import detection
        patterns = []
        if ext in ('.js', '.ts', '.jsx', '.tsx'):
            patterns = [
                r'from\s+[\'"](.+?)[\'"]',
                r'import\s+[\'"](.+?)[\'"]',
                r'require\s*\(\s*[\'"](.+?)[\'"]\s*\)',
            ]
        elif ext == '.go':
            patterns = [r'import\s+[\'"](.+?)[\'"]', r'import\s*\(([\s\S]+?)\)']
        elif ext in ('.java', '.kt', '.scala'):
            patterns = [r'import\s+([\w\.]+)']
        elif ext == '.rs':
            patterns = [r'use\s+([\w\:]+)', r'mod\s+([\w]+)']
        elif ext in ('.c', '.cpp', '.h', '.hpp'):
            patterns = [r'#include\s+[\"<](.+?)[\">]']
        elif ext == '.php':
            patterns = [r'include\s+[\'"](.+?)[\'"]', r'require\s+[\'"](.+?)[\'"]', r'use\s+([\w\\]+)']
        elif ext == '.rb':
            patterns = [r'require\s+[\'"](.+?)[\'"]', r'require_relative\s+[\'"](.+?)[\'"]']
        
        if not patterns:
            return

        import re
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            for p in patterns:
                for match in re.finditer(p, content):
                    # Handle Go multiline imports
                    if ext == '.go' and '\n' in match.group(0):
                        inner = match.group(1)
                        for line in inner.split('\n'):
                            m = re.search(r'[\'"](.+?)[\'"]', line)
                            if m: self._add_edge(rel_path, m.group(1))
                    else:
                        val = match.group(1)
                        # Clean up java/rust paths (com.foo.bar -> com/foo/bar)
                        if ext in ('.java', '.kt', '.scala', '.rs', '.php') and '.' in val:
                            val = val.replace('.', '/').replace('::', '/').replace('\\', '/')
                        self._add_edge(rel_path, val)
        except Exception:
            pass



    def _process_py_import(self, node, current_file):
        """Map import to file path (heuristic)."""
        # This is a simplified resolver. 
        # Real resolution requires full python path logic.
        target = None
        if isinstance(node, ast.Import):
             for alias in node.names:
                 target = alias.name.replace('.', '/') + '.py'
                 self._add_edge(current_file, target)
        elif isinstance(node, ast.ImportFrom):
             if node.module:
                 target = node.module.replace('.', '/') + '.py'
                 self._add_edge(current_file, target)

    def _add_edge(self, source: str, target_heuristic: str):
        # Check if target exists in repo
        # target_heuristic might be "agent/core/task_executor.py"
        # or just "os.py" (stdlib)
        
        if os.path.exists(os.path.join(self.repo_path, target_heuristic)):
            self.graph.setdefault(target_heuristic, set()).add(source)
            self.reverse_graph.setdefault(source, set()).add(target_heuristic)
        
        # Try finding it as package __init__.py
        init_path = target_heuristic.replace('.py', '/__init__.py')
        if os.path.exists(os.path.join(self.repo_path, init_path)):
            self.graph.setdefault(init_path, set()).add(source)
            self.reverse_graph.setdefault(source, set()).add(init_path)

    def get_impacted_files(self, changed_files: List[str]) -> Set[str]:
        """Return files that depend on the changed files (ripple effect)."""
        impacted = set()
        queue = list(changed_files)
        visited = set(changed_files)
        
        while queue:
            current = queue.pop(0)
            dependents = self.graph.get(current, set())
            for dep in dependents:
                if dep not in visited:
                    impacted.add(dep)
                    visited.add(dep)
                    queue.append(dep) # Transitive impact
        
        return impacted

    def get_related_files(self, files: List[str], max_depth: int = 1) -> Set[str]:
        """Return both upstream (dependents) and downstream (dependencies)."""
        related = set(files)
        
        # 1. Downstream: files that these files import (dependencies)
        # 2. Upstream: files that import these files (dependents/tests)
        
        current_layer = set(files)
        for _ in range(max_depth):
            next_layer = set()
            for f in current_layer:
                # Add dependencies (who do I need?)
                next_layer.update(self.reverse_graph.get(f, set()))
                # Add dependents (who needs me?)
                next_layer.update(self.graph.get(f, set()))
            
            related.update(next_layer)
            current_layer = next_layer
            
        return related

