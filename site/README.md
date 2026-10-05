# TempleOSBench website

`index.html`, `styles.css`, and `app.js` are the static site source. The site
displays task prompts, screenshots, inputs, and the results of one six-task run.

Live site: https://jmurzaku.github.io/temple-cua/

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

GitHub Pages serves the `gh-pages` branch from `/ (root)`. To update the site,
rebuild and publish the contents of `build/site` to that branch. Generated assets
stay out of `main`; the recorded run in `examples/cua-starter` is the source of
the replay data.
