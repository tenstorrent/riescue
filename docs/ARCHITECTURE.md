# Docs Architecture

This page contains the documentation architecture for developers maintaining documentation. It doesn't discuss the auto-doc and generation flow, but uses Sphinx.

## Structure
The documentation lives in `docs/source` in several directories:

### `common`
This is where templates, images, and fonts are stored.

### `tutorials`
This is where tutorials and getting started guides are. These are for first time users or people interested in the project. They explain things like
- What `riescue` is
- How to `install` riescue and dependencies
- Use cases for Riescue
- Further reading in [`user_guides`](#user_guides)


### `user_guides`
These are in-depth guides to using tools and components of `riescue` with background info, examples, and explanation. Where possible they should point to the [`api`](#api) reference


### `api`
This is the API reference for Riescue. It mostly consists of Sphinx-generated modules sourcing the Python docstrings. It is structued into the
- `public`API
- `internal` API

Public API needs a deprecation period if we want people to be able to rely on our code. Do not add documentation to the `public` API unless we are maintaining this code.

User Guides point to different docs in this section and need to provide info and examples for how to use different modules and packages.


## Adding Documentation

If you haven't used Sphinx before, [the official Sphinx user guide](https://www.sphinx-doc.org/en/master/usage/quickstart.html) has some resources for getting started. These docs consist of `.rst` files and Python docstrings written in the code. Most docs updates will be adding to existing docstrings but for adding new features or new guides to the docs, you can follow the following steps:

1. Add an `.rst` file to the correct section using the [structure](#structure) guide.
2. Add a reference to the new `.rst` file in the directory's `index.rst`. This will link in the Table of Contents Tree (`toctree`) and link in the sidebar.



### Writing docstrings
The [reStructuredText markup guide](https://devguide.python.org/documentation/markup/) on the Python Developer's Guide provides some good information for writing docstrings. Take advantage of being able to link to other documentation where possible.
