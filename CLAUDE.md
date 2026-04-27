# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DEV_DB_Cloner is a Python TUI tool that clones SQL Server databases to a dev environment, applying PII anonymization in-flight. It uses **Textual** for the terminal UI, **pymssql** (TDS protocol, no ODBC driver needed) for SQL Server connectivity, **SQLAlchemy** with SQLite for project config persistence, and **pandas** for chunked data transfer.

## Running the Tool

```bash
# Normal TUI mode
python db_replicator.py

# Demo/simulation mode (no real DB connections needed)
python db_replicator.py --demo

# Pass connection info via CLI (bypasses TUI connection screen)
python db_replicator.py \
  --src-server 172.22.1.34 --src-database hrm --src-uid sa --src-pwd "password" \
  --tgt-server localhost --tgt-database hrm_dev --tgt-uid sa --tgt-pwd "password"
```

## Environment Setup

```bash
# Option A: Conda
conda env create -f environment.yml
conda activate dev_db_cloner

# Option B: venv
python -m venv .clone.venv
source .clone.venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
```

## Building Executables

```bash
pyinstaller --onefile --name DB_Cloner_Linux --clean db_replicator.py
# Output: dist/DB_Cloner_Linux
```

Cross-platform builds use GitHub Actions (see `PACKAGING.md` for the full workflow).

## Architecture

### Module Responsibilities

| File | Role |
|---|---|
| `db_replicator.py` | TUI screens + replication execution + CLI entrypoint |
| `config_manager.py` | SQLAlchemy ORM models + `ConfigManager` class for SQLite persistence |
| `data_anonymizer.py` | All PII anonymization functions |

### TUI Flow

```
ProjectSelector (App)
  └─ ConnectionScreen (ModalScreen)   ← per-project DB credentials
  └─ TableSelector (App)              ← object selection (tabs: TABLE/VIEW/SP/FUNCTION/TRIGGER)
       └─ FilterEditorScreen          ← SQL WHERE clause per table
       └─ PIIEditorScreen             ← anonymization rules per table (JSON input)
       └─ ProjectSettingsScreen       ← name-source configuration
run_replication()                     ← executes the actual copy
```

`ProjectSelector` and `TableSelector` are full `App` instances that `.run()` and return values to the controlling `run_replication()` loop. Modal screens return via `.dismiss()`.

### Config Persistence (`config.db` — SQLite)

Three ORM models (see `schema.md` for full field docs):

- **`Project`** — name, description, name-source config, per-project connection credentials (stored plaintext — internal tool only), `demo_mode` flag
- **`ProjectTable`** — one row per object per project; `object_type` ∈ `{TABLE, VIEW, SP, FUNCTION, TRIGGER}`; `filter_clause` (SQL WHERE); `is_selected`
- **`SensitiveColumn`** — PII rule rows: `column_name`, `function_name`, `seed_column`

The global `config_mgr = ConfigManager()` in `db_replicator.py` is shared by all TUI screens.

### Replication Order

Tables → Views → Functions → Stored Procedures → Triggers

Views/Functions/SPs are topologically sorted by `sys.sql_expression_dependencies` before execution to respect inter-object dependencies. DDL is retrieved via `OBJECT_DEFINITION()` and database names are substituted with regex before execution on the target.

### Anonymization

`apply_anonymization()` in `db_replicator.py` dispatches to functions in `data_anonymizer.py`:

| Function | Effect |
|---|---|
| `obfuscate_name` | Replaces name using seeded RNG from `emp_no` |
| `obfuscate_spouse_name` | Same but with an offset seed (+139420) to differ from employee name |
| `anonymize_id` | Masks digits 4–8 of a TW national ID: `A12*****89` |
| `obfuscate_phone` | Replaces last 5 digits with seeded random digits |
| `obfuscate_address` | Keeps city, randomizes district + street address |
| `clear_content` | Returns `""` |

The `DATE_SALT` (today's `YYYYMMDD` string) is appended to every seed so anonymized results are consistent within a day but differ across days.

Name data source priority: `FILE` mode → `DB` mode → cached `OBFUSCATE_NAME.json` → hardcoded fallback lists.

### Connection Priority

CLI args → Environment variables (`SRC_DB_SERVER`, `SRC_DB_NAME`, `SRC_DB_UID`, `SRC_DB_PWD`, `TGT_*`) → Project stored config → empty string.

Passwords with special characters are percent-encoded for the SQLAlchemy URL but passed raw to `pymssql.connect()`.

## Key Runtime Files

- `config.db` — SQLite project config (back this up; carry it when moving the tool)
- `OBFUSCATE_NAME.json` — auto-generated name cache; delete to force rebuild from source
- `YYYYMMDD_Clone.log` — daily log with DEBUG detail and Before/After anonymization samples

Both `config.db` and `OBFUSCATE_NAME.json` are in `.gitignore`.

## Migrations

**v1.2.0 → v1.2.2**: `ConfigManager.__init__` runs `_migrate_connection_columns()` automatically (idempotent ALTER TABLE). No manual step needed.

**v1.1.0 → v1.2.0** (manual, one-time):
```bash
sqlite3 config.db "ALTER TABLE project_tables ADD COLUMN object_type VARCHAR DEFAULT 'TABLE'; \
  UPDATE project_tables SET object_type = 'TABLE' WHERE object_type IS NULL;"
```

Legacy JSON (`large_table_filters.json` + `sensitive_columns.json`) is auto-migrated to a "Default" project on first run if `config.db` is empty.
