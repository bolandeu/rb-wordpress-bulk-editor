---
id: WPBULK-002
title: 'Help: the whole credential chain, and where Doppler fits'
status: done
attempts: 2
blocked: false
blocked_reason: null
phase: null
tests_hash: null
publish_stage: null
created_by: architect
approved_by: human
approved_words: утверди обе
worker: claude-code
type: docs
owns_paths:
- src/cli/auth_commands.py
- src/cli/__init__.py
- tests/snapshots
- tests/test_auth_help.py
- changelog.d
test_paths:
- tests/test_auth_help.py
docs: none
tools: []
worker_args: null
class: standard
decision: false
visual: null
depends_on: []
budget_usd: null
epic: null
spec_refs: []
checks:
- python3 scripts/check_command_docstrings.py
resident: null
tool: null
pr: null
translation: 'Справка: вся цепочка доступов (флаг → окружение → keychain → .env),
  точные имена переменных, и что wp-bulk сам Doppler не читает — значения в окружение
  кладёт вызывающий, с примером doppler run и переименованием префикса сайта.'
---

Agents (the marketer Architect and others) do not understand where wp-bulk gets a site's login and password when they live in Doppler. Write the whole chain into the help, so an agent reading only --help (or describe) knows it.

Put the full text in `wp-bulk auth --help` (the auth group's help, auth_commands.py) and one line in the root `wp-bulk --help` pointing to it. Keep it short, Russian like the rest of the help, facts only:
1. Order: CLI flags (--domain/--username/--password where a command has them) -> process environment -> OS keychain (`wp-bulk auth login`, provider wp:<domain>) -> project .env (legacy, last; found from cwd upward or --project). The first source that has a field wins, per field.
2. The exact variable names, with the domain slug rule (example.com -> EXAMPLE_COM) shown on one example: WP_DOMAIN; WP_<SLUG>_API_USER / WP_<SLUG>_API_TOKEN, then WP_API_USER / WP_API_TOKEN; the antagor aliases SITE_DOMAIN / SITE_REST_API_USER / SITE_REST_API_PASSWORD (own WP_* names win; aliases off when SITE_CMS is set and not wordpress). SITE_PROD is never read. Verify each name against the code and a test, do not copy this brief blindly.
3. wp-bulk itself never reads Doppler (nor Windmill or any other secret store). Doppler is a store: the caller puts the values into the environment under the names above, e.g. `doppler run --project projects --config prd -- ...`. Names there carry a site prefix (TZM_SITE_REST_API_PASSWORD); wp-bulk does not strip prefixes, the caller maps them to the names above. Give one copy-paste example of that mapping with sh -c.
4. How to see what is used: `wp-bulk auth status --json` shows each field's source (cli/env/keychain/.env) without values; --check tests it with one read-only request.
5. No credentials is not an error for read-only commands (anonymous); writes need them.
Update the help snapshots. Add a changelog.d fragment (user-visible: help).

## Acceptance

- Given the installed CLI, When an agent runs `wp-bulk auth --help`, Then the text names the order cli -> env -> keychain -> .env, every variable name the code reads, says wp-bulk does not read Doppler and shows how a Doppler value with a site prefix reaches the environment [check: tests/test_auth_help.py]
- Given the env name constants in utils/auth.py and credentials/manager.py, When a name there changes, Then the help test fails: the test reads the names from the code, not from a copied list [check: tests/test_auth_help.py]

## How to test

Unit: Typer CliRunner on `auth --help` and `--help`; assert each name from _ANTAGOR_ENV_ALIASES, _GLOBAL_ENV_VARS and env_var_names('example.com', ...) appears in the help; help snapshots updated.

## Basis

The chain is one implementation: utils/auth.resolve_credentials (sources cli/env/keychain/.env) and credentials/manager.env_var_names (WP_<DOMAIN_SLUG>_API_USER/_API_TOKEN, then WP_API_USER/WP_API_TOKEN; antagor aliases SITE_DOMAIN/SITE_REST_API_USER/SITE_REST_API_PASSWORD via _ANTAGOR_ENV_ALIASES, ignored when SITE_CMS is set and not wordpress). The help describes that code; it does not change it. Doppler: marketer's contract /Users/bolandau/Work/marketer/windmill/docs/contracts/secrets.md keeps site access in Doppler project 'projects', config 'prd', as {SITE}_{KEY} with the site code prefix (e.g. TZM_SITE_REST_API_PASSWORD).

## Read first

- `src/utils/auth.py`
- `src/credentials/manager.py`
- `src/cli/auth_commands.py`

## Out of scope

- Any change to how credentials are resolved (utils/auth.py, credentials/manager.py stay as they are)
- Reading Doppler from wp-bulk; a Doppler integration of any kind
- README/AGENTS.md rewrite: the help is the source; at most one pointer line

## Feedback

### Attempt 1: doc kind

a new .md file is none of the doc kinds:
  changelog.d/+wpbulk-002-auth-help.changed.md
ways out:
  - `antagor idea add` (a plan, backlog or brief is an idea or a task, not a file)
  - an ADR: docs/adr/NNNN-slug.md
  - a research record: docs/research/YYYY-MM-DD-slug.md
  - a living doc: a name or folder from the default list, or `[docs] living` in antagor.toml
  - `[docs] data` or `[docs] generated` in antagor.toml for product data or generated files

### Attempt 2: the Architect's verdict

Two fixes. (1) The doppler example breaks on copy-paste: rich rewraps it, at 80 columns (an agent's non-TTY output) the line splits inside sh -c '...', so 'WP_DOMAIN=...' becomes a separate shell line and never reaches wp-bulk's environment. Keep the example's lines verbatim: a \b paragraph (Typer keeps its line breaks) and every line short enough for 80 columns with the indent, e.g. a multi-line sh -c script with one 'export NAME=...' per line and 'wp-bulk fetch ...' last. Add to tests/test_auth_help.py: render 'auth --help' at width 80 (COLUMNS=80 / terminal_width) and assert each line of the example appears whole. (2) changelog.d fragments are obsolete: towncrier was removed in a2670ff, CHANGELOG.md is written by 'antagor publish' from task titles. Delete changelog.d/+wpbulk-002-auth-help.changed and changelog.d/.gitkeep (the whole folder). My brief asked for a fragment by mistake.
