# Docs
This is where the documentation for the project will live

## Sphinx documentation
This repo uses [Sphinx](https://www.sphinx-doc.org/) to generate HTML docs. Info about generating and modifying the flow can be found below.

## Public and Internal docs
`public` docs are documentation that the Riescue team commits to support. Changes removing `public` APIs undergo deprecation periods.

`internal` docs are for Riescue developers - Python classes and lower-level features not for public use. These may change without warning. Use the `public` API instead.

### Themes
To stay consistent with other TT documentation, we are reusing the `tt_theme.css`.

### Build flow
The build flow can be run using `./docs/build.py`. It'll default to placing the HTML in `docs/_build`. To dump in the top-level directory like for a CI, you can run something like

```sh
./docs/build.py --build_dir public
```


### Testing locally
Using the `--local_host` option will launch a simple `http.server` locally and allow the HTML to be viewed in a web browser. Note this cleans the build directory if it exists to avoid changed files sticking around. This will print out the link to the locally hosted docs:

```sh
./docs/build.py --local_host
```

```
The HTML pages are in docs/_build.
Starting local host server on:
        http://localhost:8888
CTRL+C to stop
```

Alternatively, manually cd into the build directory and run:
```
python3 -m http.server 8888 --bind 0.0.0.0
```

Then open `http://localhost:8888` to view the generated documentation.
