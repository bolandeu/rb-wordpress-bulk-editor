---
id: WPBULK-001
title: rb-html-to-gutenberg from the Gitea registry, not github git
status: done
attempts: 0
blocked: false
blocked_reason: null
phase: null
tests_hash: null
publish_stage: null
created_by: architect
approved_by: architect
worker: claude-code
type: chore
owns_paths:
- pyproject.toml
- uv.lock
- AGENTS.md
test_paths: []
docs:
- AGENTS.md
tools: []
worker_args: null
decision: false
visual: null
depends_on: []
budget_usd: null
epic: null
spec_refs: []
checks:
- uv lock --check
- uv run pytest -q --no-cov
resident: null
tool: null
pr: null
---

CI on Gitea cannot clone github.com (fatal: could not read Username), so the gate fails at 'uv sync'. In pyproject.toml replace [tool.uv.sources] rb-html-to-gutenberg = { git = ..., tag = v0.4.1 } with { index = "gitea" } and add [[tool.uv.index]] name="gitea" url="https://homeserver.tailee01ab.ts.net:3000/api/packages/bolandeu/pypi/simple" explicit=true (as semkit/pyproject.toml). Keep the range rb-html-to-gutenberg>=0.4,<0.5 (0.4.1 is in the registry). Run uv lock. In AGENTS.md (section on html_to_gutenberg, ~lines 92-110) replace the paragraph saying the source is git with: source is the Gitea registry, new converter version = release of its package then uv lock --upgrade-package rb-html-to-gutenberg; and in the --no-sources note say 'sources' instead of 'git sources'. A ready patch is at /private/tmp/claude-501/-Users-bolandau-Work-tools-wordpress-bulk-editor/230234ed-faba-43c0-a50d-a0e218611404/scratchpad/registry.patch (git apply it). Module docstrings: touch no .py files.

## Out of scope

- Any other dependency change; code under src/.
