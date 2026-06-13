# Environment Setup Guide

Two options — pick one.

---

## Option A — pip (standard)

### 1. Create a virtual environment
```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 2. Install dependencies
```bash
pip install -e .
```

For dev dependencies (pytest, mypy):
```bash
pip install -e ".[dev]"
```

### 3. Add your OpenAI key
Create a `.env` file in the project root:
```
OPENAI_API_KEY=sk-proj-...your-key-here...
```

### 4. Verify
```bash
agent --help
```

---

## Option B — uv (faster)

### 1. Install uv (if not already)
```bash
# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -Lsf https://astral.sh/uv/install.sh | sh

# or via pip
pip install uv
```

### 2. Install dependencies
```bash
uv sync
```

For dev dependencies (pytest, mypy):
```bash
uv sync --extra dev
```

### 3. Add your OpenAI key
Create a `.env` file in the project root:
```
OPENAI_API_KEY=sk-proj-...your-key-here...
```

### 4. Run commands
Prefix every command with `uv run` — no activation needed:
```bash
uv run agent --help
uv run agent ingest "path\to\mapping.xlsx"
uv run agent ingest "path\to\mapping.xlsx" --mart "path\to\mart_org.xlsx"
uv run agent review test_query.sql --offline
uv run pytest
```

Or activate the venv once and drop the prefix:
```bash
# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# then run normally
agent review test_query.sql --offline
```

---

## Option C — No install (run directly from source)

No package install needed. Dependencies still need to be present but the `agent` CLI shortcut is not registered — use `python -m src.cli` instead.

### 1. Install dependencies only
```bash
pip install sqlglot duckdb pandas openpyxl impyla langgraph langchain-openai langchain-core pydantic typer pyyaml tenacity rich python-dotenv
```

### 2. Add your OpenAI key
```
OPENAI_API_KEY=sk-proj-...your-key-here...
```

### 3. Run directly as a module from the project root
```bash
# ingest
python -m src.cli ingest "path\to\metadata.xlsx"

# review
python -m src.cli review test_query.sql --offline

# review with JSON output
python -m src.cli review test_query.sql --offline --json report.json
```

With uv (no install at all):
```bash
uv run python -m src.cli review test_query.sql --offline
```

> `python -m src.cli` is equivalent to `agent` — it just requires typing the full module path each time.

---

## Reference — example.env

Copy the provided template before editing:
```bash
copy example.env .env      # Windows
cp example.env .env        # macOS / Linux
```

Contents:
```
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
IMPALA_HOST=your-impala-host.internal
IMPALA_PORT=21050
IMPALA_AUTH_MECHANISM=NOSASL
DUCKDB_PATH=metadata.duckdb
LOG_LEVEL=INFO
```

Only `OPENAI_API_KEY` is required. The rest are handled by `config.yaml`.
