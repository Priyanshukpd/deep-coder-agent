import subprocess
import json
import os
import re
from typing import List, Dict, Any, Optional

class LSPTool:
    """
    A tool that acts as a lightweight Language Server Protocol (LSP) client.
    It runs static analysis tools (linters/type checkers) to find errors in code.
    Currently supports:
    - Python: pylint
    - TypeScript/JavaScript: eslint (if available)
    """

    def get_diagnostics(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Runs the appropriate linter for the given file and returns a list of diagnostics.
        """
        if not os.path.exists(file_path):
            return [{"error": f"File not found: {file_path}"}]

        if file_path.endswith(".py"):
            return self._check_python(file_path)
        elif file_path.endswith(".ts") or file_path.endswith(".js") or file_path.endswith(".tsx") or file_path.endswith(".jsx"):
            return self._check_javascript(file_path)
        elif file_path.endswith(".go"):
            return self._check_go(file_path)
        elif file_path.endswith(".rs"):
            return self._check_rust(file_path)
        elif file_path.endswith(".java"):
            return self._check_java(file_path)
        elif file_path.endswith(".dart"):
            return self._check_dart(file_path)
        elif file_path.endswith(".php"):
            return self._check_php(file_path)
        elif file_path.endswith(".sql"):
            return self._check_sql(file_path)
        else:
            return [{"warning": f"No LSP support for file type: {os.path.basename(file_path)}"}]

    def _check_python(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Runs pylint on a Python file.
        """
        diagnostics = []
        try:
            # Run pylint with JSON output
            # pylint --output-format=json file.py
            result = subprocess.run(
                ["pylint", "--output-format=json", file_path],
                capture_output=True,
                text=True
            )
            
            # Pylint returns exit code based on issues found, so we don't check returncode strictly
            if result.stdout:
                try:
                    errors = json.loads(result.stdout)
                    for err in errors:
                        # Pylint JSON format:
                        # {
                        #     "type": "error",
                        #     "module": "test",
                        #     "obj": "",
                        #     "line": 1,
                        #     "column": 0,
                        #     "endLine": 1,
                        #     "endColumn": 4,
                        #     "path": "test.py",
                        #     "symbol": "undefined-variable",
                        #     "message": "Undefined variable 'x'",
                        #     "message-id": "E0602"
                        # }
                        diag = {
                            "line": err.get("line"),
                            "column": err.get("column"),
                            "severity": err.get("type"),
                            "message": err.get("message"),
                            "code": err.get("symbol")
                        }
                        diagnostics.append(diag)
                except json.JSONDecodeError:
                    # Fallback if json parsing fails
                    diagnostics.append({"error": "Failed to parse pylint output", "raw": result.stdout})
            else:
                 # If no stdout, maybe it crashed or found nothing (implausible for pylint usually)
                 pass

        except FileNotFoundError:
            diagnostics.append({"error": "pylint not installed. Please install it (pip install pylint)."})
        except Exception as e:
            diagnostics.append({"error": str(e)})

        return diagnostics

    def _check_javascript(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Runs eslint on a JS/TS file.
        """
        diagnostics = []
        try:
            # Run eslint with JSON output
            # eslint --format json file.ts
            # We assume eslint is in the path or node_modules
            
            cmd = ["npx", "eslint", "--format", "json", file_path]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True
            )

            if result.stdout:
                try:
                    # ESLint JSON format is a list of file results
                    # [
                    #   {
                    #     "filePath": "/path/to/file.ts",
                    #     "messages": [
                    #       {
                    #         "ruleId": "no-unused-vars",
                    #         "severity": 2,
                    #         "message": "'x' is defined but never used.",
                    #         "line": 1,
                    #         "column": 5,
                    #         ...
                    #       }
                    #     ]
                    #   }
                    # ]
                    files_results = json.loads(result.stdout)
                    for file_res in files_results:
                         for msg in file_res.get("messages", []):
                             diag = {
                                 "line": msg.get("line"),
                                 "column": msg.get("column"),
                                 "severity": "error" if msg.get("severity") == 2 else "warning",
                                 "message": msg.get("message"),
                                 "code": msg.get("ruleId")
                             }
                             diagnostics.append(diag)
                except json.JSONDecodeError:
                     pass
        except FileNotFoundError:
             diagnostics.append({"error": "eslint not found. Ensure checks are run in a project with eslint installed."})
        except Exception as e:
            diagnostics.append({"error": str(e)})

        return diagnostics

    def _check_go(self, file_path: str) -> List[Dict[str, Any]]:
        """Run 'go vet' on a Go file."""
        diagnostics = []
        try:
            # go vet ./... or go vet file.go
            # go vet output is text: "./main.go:4:2: printf: ..."
            cmd = ["go", "vet", file_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            # go vet prints to stderr
            output = result.stderr or result.stdout
            if output:
                for line in output.splitlines():
                    # Parse basic go vet format: filename:line:col: message
                    parts = line.split(":", 3)
                    if len(parts) >= 4:
                        try:
                            diag = {
                                "line": int(parts[1]),
                                "column": int(parts[2]),
                                "severity": "error",
                                "message": parts[3].strip(),
                                "code": "go-vet"
                            }
                            diagnostics.append(diag)
                        except ValueError:
                            pass
        except FileNotFoundError:
             diagnostics.append({"error": "go command not found."})
        except Exception as e:
            diagnostics.append({"error": str(e)})
        return diagnostics

    def _check_rust(self, file_path: str) -> List[Dict[str, Any]]:
        """Run 'cargo check' on a Rust file."""
        diagnostics = []
        try:
            # cargo check --message-format=json
            # Needs to be run from crate root, but let's try generally
            cwd = os.path.dirname(file_path) or "."
            cmd = ["cargo", "check", "--message-format=json"]
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
            
            for line in result.stdout.splitlines():
                try:
                    msg = json.loads(line)
                    if msg.get("reason") == "compiler-message":
                        actual_msg = msg.get("message", {})
                        if actual_msg.get("level") == "error":
                            spans = actual_msg.get("spans", [])
                            for span in spans:
                                if span.get("is_primary"):
                                    diag = {
                                        "line": span.get("line_start"),
                                        "column": span.get("column_start"),
                                        "severity": "error",
                                        "message": actual_msg.get("message"),
                                        "code": actual_msg.get("code", {}).get("code")
                                    }
                                    diagnostics.append(diag)
                except json.JSONDecodeError:
                    pass
        except FileNotFoundError:
             diagnostics.append({"error": "cargo command not found."})
        except Exception as e:
            diagnostics.append({"error": str(e)})
        return diagnostics

    def _check_java(self, file_path: str) -> List[Dict[str, Any]]:
        """Run 'javac -Xlint' on a Java file."""
        diagnostics = []
        try:
            cmd = ["javac", "-Xlint", file_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            # javac prints to stderr: "File.java:4: error: ..."
            output = result.stderr
            if output:
                for line in output.splitlines():
                    parts = line.split(":", 3)
                    if len(parts) >= 4 and "error" in parts[2]:
                        try:
                            diag = {
                                "line": int(parts[1]),
                                "column": 0, # Javac doesn't always strictly give col in same format
                                "severity": "error",
                                "message": parts[3].strip(),
                                "code": "javac"
                            }
                            diagnostics.append(diag)
                        except ValueError:
                            pass
        except FileNotFoundError:
             diagnostics.append({"error": "javac command not found."})
        except Exception as e:
            diagnostics.append({"error": str(e)})
        return diagnostics

    def _check_dart(self, file_path: str) -> List[Dict[str, Any]]:
        """Run 'dart analyze' on a Dart/Flutter file."""
        diagnostics = []
        try:
            # dart analyze --format=json
            # returns a JSON object with a "diagnostics" list
            cmd = ["dart", "analyze", "--format=json", file_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            # dart analyze outputs the JSON to stdout
            if result.stdout:
                try:
                    data = json.loads(result.stdout)
                    for diag in data.get("diagnostics", []):
                        diagnostics.append({
                            "line": diag.get("location", {}).get("range", {}).get("startLine"),
                            "column": diag.get("location", {}).get("range", {}).get("startColumn"),
                            "severity": diag.get("severity", "INFO"),
                            "message": diag.get("problemMessage"),
                            "code": diag.get("code")
                        })
                except json.JSONDecodeError:
                    pass
        except FileNotFoundError:
             diagnostics.append({"error": "dart command not found."})
        except Exception as e:
            diagnostics.append({"error": str(e)})
        return diagnostics

    def _check_php(self, file_path: str) -> List[Dict[str, Any]]:
        """Run 'php -l' (syntax check) on a PHP file."""
        diagnostics = []
        try:
            # php -l file.php
            # Output: "No syntax errors detected in file.php" or "Parse error: syntax error, unexpected ... in file.php on line 10"
            cmd = ["php", "-l", file_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            output = result.stdout or result.stderr
            if output and "Parse error" in output:
                # Parse: "Parse error: syntax error, unexpected '}' in /path/to/file.php on line 5"
                match = re.search(r"Parse error:\s+(.*)\s+in\s+.*\s+on line\s+(\d+)", output)
                if match:
                    diagnostics.append({
                        "line": int(match.group(2)),
                        "column": 0,
                        "severity": "error",
                        "message": match.group(1),
                        "code": "php-syntax"
                    })
        except FileNotFoundError:
             diagnostics.append({"error": "php command not found."})
        except Exception as e:
            diagnostics.append({"error": str(e)})
        return diagnostics

    def _check_sql(self, file_path: str) -> List[Dict[str, Any]]:
        """Run 'sqlfluff lint' on a SQL file."""
        diagnostics = []
        try:
            # sqlfluff lint --format=json file.sql
            cmd = ["sqlfluff", "lint", "--format=json", file_path]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.stdout:
                try:
                    # Output is a list of objects, one per file
                    # [ { "filepath": "...", "violations": [ ... ] } ]
                    files = json.loads(result.stdout)
                    for f in files:
                        for v in f.get("violations", []):
                            diagnostics.append({
                                "line": v.get("line_no"),
                                "column": v.get("line_pos"),
                                "severity": "warning", # sqlfluff is mostly lint/style
                                "message": v.get("description"),
                                "code": v.get("code")
                            })
                except json.JSONDecodeError:
                    pass
        except FileNotFoundError:
             diagnostics.append({"error": "sqlfluff command not found."})
        except Exception as e:
            diagnostics.append({"error": str(e)})
        return diagnostics

