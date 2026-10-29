---
id: WPBULK-003
title: 'No siteoptions values in log files: smtp_password, b24_webhook, tokens leak
  at DEBUG'
status: publish
attempts: 0
blocked: false
blocked_reason: null
phase: null
tests_hash: null
publish_stage: null
created_by: architect
approved_by: human
approved_words: утверди обе
worker: claude-code
type: bugfix
owns_paths:
- src/wordpress/options.py
- src/providers/wordpress.py
- tests/test_options_log_secrets.py
- changelog.d
test_paths:
- tests/test_options_log_secrets.py
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
translation: 'Баг: wp-bulk options пишет значения настроек siteoptions (smtp_password,
  b24_webhook с токеном, tg_bot_token) в лог-файл — файл всегда на DEBUG. Логировать
  только имена ключей; вывод команды не меняется.'
---

Found while answering marketer T415. `wp-bulk options` (fetch and update) writes site option VALUES into the log file: OptionsEndpoint.get/update log the full dict at DEBUG, and the provider's post()/put() log 'with full data'. The file sink is always DEBUG, so smtp_password, b24_webhook (its URL holds the token), tg_bot_token and any other secret of the siteoptions plugin end up in <project>/logs/options_*.log on every run, not only with --verbose.

Fix: OptionsEndpoint logs the count and the key names only. The provider's post()/put() log the endpoint and the data keys, never the values (as put() already does for keys; drop the 'full data' lines). The JSON/console OUTPUT of the command is not changed: wp-bulk returns what the endpoint gives (transport), only logs stop carrying values. Check nothing else under src/ logs the options dict. Add a changelog.d fragment (fix: secrets no longer written to logs).

## Acceptance

- Given a MockTransport siteoptions endpoint returning {"smtp_password": "s3cret-123", "b24_webhook": "https://x/rest/3/tok-456/"}, When OptionsEndpoint.get() and update() run with a loguru sink at DEBUG, Then the sink holds neither s3cret-123 nor tok-456, and it holds the key names [check: tests/test_options_log_secrets.py]
- Given WordPressHttpProvider.post/put with data {"password": "s3cret-123"}, When the call runs with a DEBUG sink, Then the sink does not hold s3cret-123 [check: tests/test_options_log_secrets.py]

## How to test

Unit: httpx.MockTransport through the provider's transport seam; loguru logger.add(list.append-style sink, level='DEBUG'); assert on the captured text. Existing tests stay green.

## Basis

The log file sink is always DEBUG (utils/logger.py: 'В файл пишем всё'), so every logger.debug lands in <project>/logs/*.log. OptionsEndpoint.get/update log the whole dict (logger.debug(f'Опции: {result}') / {options}), and WordPressHttpProvider.post/put log 'with full data: {data}'. A real log from 15.05.2026 (options_fetch on ost-term.ru) holds b24_webhook with its secret in plain text; the same dict carries smtp_password, tg_bot_token. Follow the existing pattern already in put(): log the keys, not the values.

## Read first

- `src/wordpress/options.py`
- `src/providers/wordpress.py`
- `src/utils/logger.py`

## Out of scope

- The options command's JSON/console output: it keeps returning every key and value the endpoint gives
- The siteoptions plugin, its key list or schema (not this project)
- Old log files already on disk (report them, do not delete)
