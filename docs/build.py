#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Wrapper script for generating Sphinx documentation.
Usage: ./build_docs.py [options]
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import sphinx


class DocBuilder:
    docs_dir = Path(__file__).parent
    repo_dir = docs_dir.parent

    def __init__(self, clean, source_dir, build_dir, check, theme):
        self.clean = clean
        self.source_dir = source_dir
        self.build_dir = build_dir
        self.check = check
        self.theme = theme

    @classmethod
    def add_args(cls, parser: argparse.ArgumentParser):
        """Parse command line arguments."""
        parser.add_argument("--clean", action="store_true", help="Clean build directory before building")
        parser.add_argument("--source_dir", type=Path, default=cls.docs_dir / "source", help="Source directory")
        parser.add_argument("--build_dir", type=Path, default=cls.docs_dir / "_build", help="Build directory")
        parser.add_argument("--check", action="store_true", help="Check for sphinx warnings as errors")
        parser.add_argument("--theme", default=None, help="HTML theme to use")

    @classmethod
    def run_cli(cls):
        parser = argparse.ArgumentParser(description="Build Sphinx documentation")
        cls.add_args(parser)
        klass = cls(**vars(parser.parse_args()))
        klass.run()
        return klass

    def run(self):
        """Run the build."""
        if self.clean:
            self.clean_build_dir(self.build_dir)
        self.build_docs()

    def clean_build_dir(self, build_dir: Path):
        """Clean the build directory."""
        print(f"Cleaning build directory: {build_dir}")
        if build_dir.exists():
            shutil.rmtree(build_dir)
        build_dir.mkdir(parents=True, exist_ok=True)

    def build_docs(self):
        """Build documentation based on specified format."""
        self.build_dir.mkdir(parents=True, exist_ok=True)
        if not self.source_dir.exists():
            raise FileNotFoundError(f"Source directory does not exist: {self.source_dir}. Ensure --source_dir is a directory")

        print(f"Building documentation, source_dir: {self.source_dir}, build_dir: {self.build_dir}")

        sphinx_cmd = ["sphinx-build"]
        if self.check:
            sphinx_cmd.extend(["-W", "-E"])
        sphinx_cmd.append(self.source_dir)
        if self.theme:
            sphinx_cmd.append(f"-Dhtml_theme={self.theme}")
        sphinx_cmd.append(self.build_dir)
        print(f"Running: {' '.join(str(s) for s in sphinx_cmd)}")
        result = subprocess.run(sphinx_cmd, check=True)


if __name__ == "__main__":
    DocBuilder.run_cli()
