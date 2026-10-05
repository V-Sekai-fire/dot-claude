# dot-claude

The workspace's agent configuration, checked out at `.claude/`: the reviewed permission set, the skills and the attribution-stripping hook.

## What it is for

The agent harness reads `.claude/` at the workspace root, so this checkout applies to every project in the workspace. `settings.json` is the shared, reviewed permission set, and a desk's own `settings.local.json` stays untracked beside it. A permission arrives as a diff somebody approved rather than through a link. The working agreements live in `manuals-weftspun`, RFD 2294 states the standard practices, and the goal manifest links some RFDs in here as skills.

## Build

```sh
python scripts/check_skills.py --self-test
python scripts/check_skills.py
```

## Licence

MIT; see `LICENSE`.
