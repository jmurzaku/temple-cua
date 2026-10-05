# TempleOSBench website

`index.html`, `styles.css`, and `app.js` are the static site source. The site
displays task prompts, screenshots, inputs, and the results of one six-task run.

Build from the repository root:

```sh
.venv/bin/python scripts/build_site.py
```

Serve the build to preview:

```sh
python -m http.server 8000 --directory build/site
```

All asset paths are relative, so the site works under a GitHub Pages project
path. No framework, font service, or external JavaScript is required.
