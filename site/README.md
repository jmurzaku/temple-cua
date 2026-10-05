# Research website source

`dist/` contains the static task catalog, prompts, screenshots and recorded
Cua starter results. It is a replay viewer, not a live VM. It does not collect
API keys. The current deployment is owner-private:
https://ring-zero-temple-lab.yurpl.chatgpt.site

Preview locally from the repository root:

```sh
python -m http.server 8000 --directory site/dist
```

To replace the recordings after another six-task Cua run:

```sh
.venv/bin/python scripts/export_starter_site.py runs/cua-starter site
```

The six task IDs must match the starter suite shown in the root README. The
exporter preserves run provenance and creates the source/run downloads.

The live site's hosting checkout is managed separately. Its project-specific
hosting configuration and Git credentials are not copied into this repository.
These static files can be hosted on any static web server.
