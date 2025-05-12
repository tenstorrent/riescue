# Docs
This is where the documentation for the project will live

## Sphinx documenation
This repo uses [Sphinx](https://www.sphinx-doc.org/) to generate HTML docs. Info about generating and modifying the flow can be found below.

## Public and Internal docs
`public` docs is documentation that the Riescue team is committing to support. Any changes that will remove from `public` APIs and documented features will undergo a deprecation period.

`internal` docs are documentation for Riescue developers. These are API documents for python classes and lower-level features that are not for public use.
These may change over time without warning. It's recommended that users are making use of the `public` API and avoid depending on internal feautres

### Themes
To stay consistent with other TT docuemnation, we are reusing the `tt_theme.css`.

### Build flow
The build flow can be ran using `./docs/build.py`