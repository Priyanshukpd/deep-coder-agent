# God Mode Agent

A deterministic orchestration system around a probabilistic model.

## Installation

```bash
pip install -e .
```

## Usage

After installation, you can use the `god-mode` command:

```bash
# Start the interactive chat interface
god-mode chat

# Run a specific task
god-mode run "Implement a new feature"

# Start in dry-run mode to see what would be done
god-mode run --dry-run "Fix the authentication bug"

# Show all available options
god-mode --help
```

### Direct Python execution

You can also run directly with Python without installing:

```bash
# Interactive chat mode
python -m agent --interactive

# Run a task
python -m agent "Fix the bug in the login module"

# Dry run to see the plan
python -m agent --dry-run "Add dark mode support"
```

## Development

Install development dependencies:

```bash
pip install -e ".[dev]"
```

Run tests:

```bash
pytest
```
