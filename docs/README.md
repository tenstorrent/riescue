# Docs
This is where the documentation for the project will live

## Sphinx documentation
This repo uses [Sphinx](https://www.sphinx-doc.org/) to generate HTML docs. Info about generating and modifying the flow can be found below.

## Public and Internal docs
`public` docs is documentation that the Riescue team is committing to support. Any changes that will remove from `public` APIs and documented features will undergo a deprecation period.

`internal` docs are documentation for Riescue developers. These are API documents for python classes and lower-level features that are not for public use.
These may change over time without warning. It's recommended that users are making use of the `public` API and avoid depending on internal features

### Themes
To stay consistent with other TT documentation, we are reusing the `tt_theme.css`.

### Build flow
The build flow can be ran using `./docs/build.py`. It'll default to placing the html in `docs/_build`. To dump in the top-level directory like for a CI, you can run something like

```
./docs/build.py --build_dir public
```

Currently testing this before Pages / docs are ready by `cd`-ing into the built directory and running
```
python3 -m http.server 19999  --bind 0.0.0.0
```

then opening `http://localhost:19999` to view the generated code.