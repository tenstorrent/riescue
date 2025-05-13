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

project = "Riescue"
copyright = "© 2025 Tenstorrent AI ULC"
author = "Tenstorrent AI ULC"
release = "0.3.0"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",
]

# templates_path = ['_templates']
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
autodoc_member_order = "bysource"


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

repo_path = Path(__file__).parents[2]
if not (repo_path / ".git").exists():
    raise FileNotFoundError(f"Expected path to be top of repostiory. {repo_path} does not contain .git directory")

print("Adding root", repo_path)
sys.path.insert(0, str(repo_path))  # Just need top-level path to avoid import errors


def setup(app):
    app.add_css_file("tt_theme.css")
