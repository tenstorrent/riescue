# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

from pathlib import Path
import sys

# This removes Bases: object from output, not sure how else to do this
from sphinx.ext import autodoc


class MockedClassDocumenter(autodoc.ClassDocumenter):
    def add_line(self, line: str, source: str, *lineno: int) -> None:
        if line == "   Bases: :py:class:`object`":
            return
        super().add_line(line, source, *lineno)


autodoc.ClassDocumenter = MockedClassDocumenter


project = "Riescue"
copyright = "© 2025 Tenstorrent AI ULC"
author = "Tenstorrent AI ULC"
pyproject_path = Path(__file__).parents[2] / "pyproject.toml"

version = None
with open(pyproject_path, "r") as f:
    for line in f:
        if line.startswith("version =") or line.startswith("version="):
            version = line.split("=")[1].strip().strip('"')
            break
if version is None:
    raise ValueError(f"Version not found in {pyproject_path}, ensure pyproject.toml has a 'version=str' or 'version = str'")

release = version

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
]

# Control autodoc behavior
autodoc_default_options = {"show-inheritance": False}

exclude_patterns = ["public", "_build", "**/_build_api/**", "**/_templates"]
templates_path = ["_templates", "../common/_templates"]
autodoc_member_order = "bysource"

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "sphinx_rtd_theme"
html_logo = "common/images/tt_logo.svg"
html_favicon = "common/images/favicon.png"
html_context = {"logo_link_url": "https://docs.tenstorrent.com/"}

html_static_path = ["_static"]
# Configure the theme to keep global TOC
html_theme_options = {
    "prev_next_buttons_location": "bottom",
    # "style_nav_header_background": "#2980B9",
    # TOC options
    "collapse_navigation": False,
    "sticky_navigation": True,
    "navigation_depth": 4,
    "includehidden": True,
    "titles_only": False,
}

repo_path = Path(__file__).parents[2]
if not (repo_path / ".git").exists():
    raise FileNotFoundError(f"Expected path to be top of repostiory. {repo_path} does not contain .git directory")

print("Adding root", repo_path)
sys.path.insert(0, str(repo_path))  # Just need top-level path to avoid import errors


def setup(app):
    app.add_css_file("tt_theme.css")
