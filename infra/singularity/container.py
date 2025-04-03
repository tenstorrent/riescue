import os
import json
import subprocess
import shlex
import getpass
import argparse

from pathlib import Path

"""
Python class to handle Singularity script building, launching, and running.

Use infra/container-build to build container
Use infra/container-run to run container
"""


class Container:
    binds = "/usr/lib64:/shared"

    def __init__(self, repo_path=None):
        # Path args
        self.repo_path = repo_path or Path(__file__).parents[2]
        self.infra = self.repo_path / "infra"
        self.container_config = self.infra / ".container_config"
        self.registry_uri = None
        self.registry_remote = None

        if self.container_config.exists():
            with open(self.container_config, "r") as f:
                config = json.load(f)
            self.registry_uri = config.get("registry_uri")
            self.registry_remote = config.get("registry_remote")
            binds = config.get("binds")
            if binds:
                self.binds = self.binds + "," + binds
            if config.get("remote_token_path"):
                token_path = config.get("remote_token_path")
                if "~" in token_path:
                    token_path = Path.home() / token_path.replace("~/", "")
                else:
                    token_path = Path(token_path)
                with open(token_path, "r") as f:
                    self.token = f.read()

        self.sif = self.infra / "riescue.sif"
        self.container_def = self.infra / "Container.def"
        self.container_id_file = self.infra / "container/singularity-id"

    # main methods
    def build(self, dont_push=False):
        """
        Builds container, increments def version if building a new version.
        Updates version in infra/container/singularity-id
        optionally pushes to repostiory's registry
        """
        if not self.container_def.exists():
            raise FileNotFoundError(f"Couldn't find Container.def at {self.container_def.resolve()}")
        def_version = self.def_version
        if def_version == self.container_version:
            self.def_version = def_version + 1
        self.singularity(["build", "--fakeroot", "--force", str(self.sif), str(self.container_def)], check=True)
        version_string = f"{self.def_version}-{self.sha_payload()}"
        with open(self.container_id_file, "w") as f:
            f.write(version_string)

        if not self.registry_uri or not self.registry_remote:
            dont_push = True
        if not dont_push:
            push = None
            while True:
                push = input("Push to registry? y/n: ").lower()
                if push == "y" or push == "n":
                    break
                else:
                    print("Please type 'y' or 'n';", end=" ")
            if push == "y":
                self.push(version_string)

    def run(self, args=[]):
        """
        Launch container - if --singularity-sif passed, uses that, otherwise uses local sif
        If no local sif file found, checks for a .container_remote JSON file.
        If no file found, exits and tells users to run build command.

        pulls latest
        If no args, launches bash

        Users need to manually pull or remove sif to update container. This is to avoid doing sha_payload on every launch.
        """
        singularity_args, args = self.parse_container_args(args)

        if singularity_args.singularity_sif is not None:
            self.sif = singularity_args.singularity_sif
        else:
            if self.sif.exists() and self.sif_version == self.container_version:
                pass
            elif not self.sif.exists():
                if not self.registry_uri or not self.registry_remote:
                    print("No registry URI or remote found, unable to pull container from remote. Please build the container with ./infra/container-build")
                    raise RuntimeError("No registry URI or remote, please build the container before running with ./infra/container-build")
                print("No SIF found, pulling latest SIF")
                self.pull()
            else:
                if not self.registry_uri or not self.registry_remote:
                    print("Local def version doesn't match remote version, rebuilding container")
                    self.build(dont_push=True)
                else:
                    print("Local def version doesn't match remote version, pulling latest")
                    self.pull()

        # Resolve disk binds if they exist on this server
        binds = []
        for b in self.binds.split(","):
            full_bind = b
            if ":" in b:
                b = b.split(":")[0]
            if Path(b).exists():
                binds.append(full_bind)
        binds = ",".join(binds)

        cmd = ["exec", "--bind", binds, str(self.sif)]
        if len(args) == 0:
            cmd += ["bash"]
        elif Path(args[0]).exists():
            cmd += ["bash", "-c", " ".join(args)]
        else:
            cmd += ["bash", "-c"]
            if '"' in args[0]:
                cmd += args
            else:
                cmd.append(" ".join(args))
        cmd = ["singularity"] + cmd
        os.execvp(cmd[0], cmd)

    def push(self, version_string=None):
        "Push container to remote;"
        if version_string is None:
            version_string = f"{self.def_version}-{self.sha_payload()}"
        self.singularity(["push", str(self.sif), f"{self.registry_uri}:{version_string}"], debug=True, check=True)

    def pull(self):
        "Pulls latest singularity container and places in self.sif"
        if not self.registry_uri or not self.registry_remote:
            print("No registry URI or remote found, unable to pull container from remote. Please build the container")
        if self.sif.exists():
            print(f"Removing local riescue.sif file")
            self.sif.unlink()
        self._check_singularity_remotes()
        singularity_build = ["pull", str(self.sif), f"{self.registry_uri}:{self.container_string}"]
        self.singularity(singularity_build, check=True, debug=True)

    def sha_payload(self):
        "Returns the sha256 payload of the sif file. Assumes local sif file is set"
        sif_list = self.singularity(["sif", "list", str(self.sif)], check=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
        object_id = None
        for line in sif_list.split("\n"):
            if "FS" in line:
                object_id = line.split()[0]
        if object_id is None:
            raise ValueError(f"Couldn't find object ID for FS in sif list output: {sif_list}")
        cmd = ["singularity", "sif", "dump", object_id, str(self.sif)] + ["|", "sha256sum"]
        object_sha = subprocess.run(" ".join(cmd), shell=True, check=True, stdout=subprocess.PIPE)  # Should be safe since self.sif is a Path object, not user controlled
        object_sha = object_sha.stdout.decode("utf-8")
        object_sha = object_sha.strip().split()[0]
        return object_sha

    @property
    def sif_version(self):
        return self._get_version_from_sif()

    @property
    def def_version(self):
        return self._get_version_from_def()

    @def_version.setter
    def def_version(self, new_version: int):
        return self._set_def_version(new_version)

    # Remote version is from singularity-id
    @property
    def container_version(self):
        "Retrieves the checked in container version from the singularity-id file."
        return int(self._read_id().split("-")[0])

    @property
    def container_string(self):
        return self._read_id()

    def _version_num_from_str(self, version_str: str) -> int:
        return int(version_str.split(".")[-1])

    def _get_version_from_sif(self) -> int:
        "Returns version from sif file. Strips from 1.X to get X"
        sif_list = self.singularity(["inspect", "--labels", str(self.sif)], check=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
        version_number = None
        for line in sif_list.split("\n"):
            if "VERSION" in line:
                return self._version_num_from_str(line)
        raise ValueError(f"Couldn't find VERSION in sif inspect output: {sif_list}")

    def _get_version_from_def(self) -> str:
        "Returns version from def file"
        with open(self.container_def, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("VERSION"):
                    return self._version_num_from_str(line)

    def _set_def_version(self, new_version: int):
        with open(self.container_def, "r") as f:
            new_lines = [l if ("VERSION" not in l) else f"    VERSION 1.{new_version}\n" for l in f]
        with open(self.container_def, "w") as f:
            for line in new_lines:
                f.write(line)

    def _read_id(self) -> tuple:
        "File of format {version}-{sha_payload}"
        with open(self.container_id_file, "r") as f:
            return f.read().strip()

    def _check_singularity_remotes(self):
        "Checks that remote is added, tries to add it otherwise. Assumes if token is needed, remote_token_path is set in .container_remote"
        remote_list = self.singularity(["remote", "list"], stdout=subprocess.PIPE).stdout.decode("utf-8")
        if self.registry_remote in remote_list:
            return
        print(f"Couldn't find the registry URI in {self.registry_remote} remotes list, adding")
        remote_login_cmd = ["remote", "login", "-u", getpass.getuser()]
        if self.token:
            remote_login_cmd += ["-p", self.token]
        remote_login_cmd.append(self.registry_remote)
        self.singularity(remote_login_cmd, check=True)

    def parse_container_args(self, args):
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--singularity-sif", type=Path, default=None, help="Path to existing sif. Skips remote login and pulling")

        cli_args = [a for a in args if "--singularity" not in a]
        args, _ = parser.parse_known_args(args)

        return args, cli_args

    def singularity(self, args, debug=False, **kwargs):
        cmd = ["singularity"] + args
        if debug:
            print("Running:", " ".join(cmd))
        return self.subprocess(cmd, **kwargs)

    # Runs subprocess, doesn't check for errors
    def subprocess(self, *args, **kwargs):
        return subprocess.run(*args, **kwargs)
