# Offline demo

The offline demo exercises the installed package against a synthetic workspace.
A successful run reports the following results.

```text
Server health: ok
Scanned synthetic files: 17
Modes applied: service.py=skeleton, client.ts=code, overview.md=code, invalid_syntax.py=skeleton, settings.toml=exclude
Preview token estimate: 513
Preview/writer token parity: True
Merged output bytes: 1978
Manifest paths: docs/overview.md, src/client.ts, src/invalid_syntax.py, src/service.py, src/worker.go
Source freshness: edit changed skeleton output (True)
Export path check: no temporary absolute workspace path
Provider calls: none; provider credentials removed for this run
Temporary demo workspace and app state removed.
```

The run generated the tiny binary fixture on demand and confirmed it was absent
from the exported manifest. The manifest records relative POSIX paths. The
invalid Python file rendered an explicit syntax-error skeleton diagnostic.
Summary generation was not part of this offline run; it requires a separately
configured provider as described in [Provider configuration](provider-configuration.md).

To repeat the run after installing MergeMonster, choose an existing temporary
directory on a volume with adequate free space and run:

```bash
python examples/run_offline_demo.py --scratch "$DEMO_SCRATCH"
```

The script creates a unique child directory there and removes it on exit.
