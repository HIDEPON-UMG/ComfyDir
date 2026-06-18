# Repo-Local Hook Fallback

This repo does not pin `"hook_source": "repo"`, so active hook execution is
user-level and central-first:

`~/.codex/hooks.json` / `~/.claude/settings.json` -> `repo-harness-hook` ->
packaged hooks from the installed repo-harness runtime.

The files under `.ai/hooks/lib/` are kept only for repo workflow helper scripts
that source shared shell utilities. Full hook runtime scripts are not vendored
here by default because stale copies can be mistaken for the active hook path.

Set `"hook_source": "repo"` in `.ai/harness/policy.json` only for self-hosted
hook development or an explicitly reviewed repo-local hook override.
