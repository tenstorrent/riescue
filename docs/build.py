#!/usr/bin/env python3
# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

"""
Wrapper script for generating Sphinx documentation.
Usage: ./build_docs.py [options]
"""

import http.server
import socketserver
import argparse
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path


class DocBuilder:
    docs_dir = Path(__file__).parent
    if os.getenv("BUILD_TT_DOCS"):
        defualt_src = docs_dir / "tt_source"
    else:
        defualt_src = docs_dir / "source"
    repo_dir = docs_dir.parent

    def __init__(
        self,
        clean: bool,
        source_dir: Path,
        build_dir: Path,
        check: bool,
        local_host: bool,
        port: int,
        host: str,
        interactive: bool,
    ):
        self.check = check
        self.clean = clean

        self.source_dir = source_dir
        self.build_dir = build_dir

        self.port = port
        self.host = host
        self.interactive = interactive
        self.local_host = local_host or interactive  # interactive implies local_host
        # Building locally will remove the build directory if it exists before building
        if self.local_host:
            self.clean = True

    @classmethod
    def add_args(cls, parser: argparse.ArgumentParser):
        """Parse command line arguments."""
        parser.add_argument("--clean", action="store_true", help="Clean build directory before building")
        parser.add_argument("--check", action="store_true", help="Check for sphinx warnings as errors")
        build_opts = parser.add_argument_group("Build Path options", description="Path options for building the documentation")
        build_opts.add_argument("--source_dir", type=Path, default=cls.defualt_src, help="Source directory")
        build_opts.add_argument("--build_dir", type=Path, default=cls.docs_dir / "_build", help="Build directory")

        local_host_opts = parser.add_argument_group(
            "Local host options",
            description=(
                "Options for viewing generated docs locally. Users will need to CTRL+C to stop the local host server."
                "Using local host will build from scratch and remove the build directory if it exists before building."
            ),
        )
        local_host_opts.add_argument("--local_host", action="store_true", help="Launch local host server to view generated docs.")
        local_host_opts.add_argument("--interactive", "-i", action="store_true", help="Launch local host server to view generated docs in interactive mode. STOP and RELOAD commands are available.")
        local_host_opts.add_argument("--port", type=int, default=8888, help="Port to run the local host server on. Default: %(default)s")
        local_host_opts.add_argument("--host", type=str, default="0.0.0.0", help="Host to run the local host server on. Default: %(default)s")

    @classmethod
    def run_cli(cls):
        parser = argparse.ArgumentParser(description="Build Sphinx documentation. Requires interactive container for some reason. I don't know why :(")
        cls.add_args(parser)
        klass = cls(**vars(parser.parse_args()))
        klass.run()
        return klass

    def run(self):
        """Run the build."""
        if self.clean:
            self.clean_build_dir(self.build_dir)
        self.build_docs()

        if self.local_host:
            if self.interactive:
                self.start_interactive_host()
            else:
                self.start_local_host()

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
        sphinx_cmd.append(str(self.source_dir))
        sphinx_cmd.append(str(self.build_dir))
        print(f"Running: {' '.join(str(s) for s in sphinx_cmd)}")

        # Set safe locale environment variables to avoid locale errors in containers
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        env["LANG"] = "C"

        subprocess.run(sphinx_cmd, check=True, env=env)

    def start_interactive_host(self):
        """Start interactive host server with STOP/RELOAD commands."""
        host = self.host
        if host == "0.0.0.0":
            host = "localhost"

        while True:
            print(f"Starting local host server on:\n\thttp://{host}:{self.port}")
            print("Type 's / stop' to quit or 'r / reload' to rebuild and restart, or CTRL+C to stop")

            server_thread = None
            httpd = None
            stop_event = threading.Event()
            up_character = b"\x1b\x5b\x61"

            try:
                # Start server in thread
                server_thread = threading.Thread(target=self.start_local_host, args=(stop_event,), daemon=True)
                server_thread.start()

                # Handle user input
                while not stop_event.is_set():
                    try:
                        cmd = input().strip().lower()
                        if cmd == "stop" or cmd == "s":
                            print("Stopping server...")
                            stop_event.set()
                            return
                        elif cmd == "reload" or cmd == "r" or bytes(cmd, "utf-8") == up_character:
                            print("Reloading documentation...")
                            stop_event.set()
                            break
                        else:
                            print("Commands: STOP, RELOAD")
                    except (EOFError, KeyboardInterrupt):
                        print("\nStopping server...")
                        stop_event.set()
                        return

                # Wait for server thread to finish
                if server_thread:
                    server_thread.join(timeout=2)

                # If we broke out of the loop, rebuild and restart
                if not stop_event.is_set():
                    continue

                print("Rebuilding documentation...")
                self.build_docs()
                time.sleep(1)  # Brief pause before restart

            except Exception as e:
                print(f"Error: {e}")
                stop_event.set()
                return

    def start_local_host(self, interactive=False, stop_event=None):
        """Start the local host server."""
        host = self.host
        if host == "0.0.0.0":
            host = "localhost"

        if not interactive:
            print(f"Starting local host server on:\n\thttp://{host}:{self.port}\nCTRL+C to stop")

        start_dir = Path.cwd()
        try:
            os.chdir(self.build_dir)

            # https://stackoverflow.com/a/25529620 - allows reusing address
            class ReusableTCPServer(socketserver.TCPServer):
                allow_reuse_address = True

                def serve_forever(self, poll_interval=0.5):
                    """Override to check stop_event in interactive mode."""
                    if interactive and stop_event:
                        while not stop_event.is_set():
                            self.handle_request()
                    else:
                        super().serve_forever(poll_interval)

            with ReusableTCPServer((self.host, self.port), http.server.SimpleHTTPRequestHandler) as httpd:
                if interactive and stop_event:
                    httpd.timeout = 1  # Set timeout for handle_request
                httpd.serve_forever()

        except KeyboardInterrupt as e:
            if not interactive:
                print("Local host server stopped")
        except Exception as e:
            if interactive and stop_event and not stop_event.is_set():
                print(f"Server error: {e}")
            elif not interactive:
                raise
        finally:
            os.chdir(start_dir)


if __name__ == "__main__":
    DocBuilder.run_cli()
