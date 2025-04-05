import os
import sys
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
    registry_uri = "oras://aus-gitlab.local.tenstorrent.com:5005/riscv/riescue"  # No idea what oras is but it works here
    aus_registry_uri = "docker://aus-gitlab.local.tenstorrent.com:5005"
    binds = "/weka_scratch/fpgen,/weka_scratch/rv_bazel_cache,/proj_risc,/proj_risc_regr,/tools_risc,/tools_vendor,/usr/lib64:/shared"

    def __init__(self, repo_path=None):
        # Path args
        self.repo_path = repo_path or Path(__file__).parents[2]
        self.infra = self.repo_path / "infra"
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
        if def_version == self.remote_version:
            self.def_version = def_version + 1
        self.singularity(["build", "--fakeroot", "--force", str(self.sif), str(self.container_def)], check=True)
        version_string = f"{self.def_version}-{self.sha_payload()}"
        with open(self.container_id_file, "w") as f:
            f.write(version_string)
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
        If no local sif file found, pulls latest
        If no args, launches bash

        Users need to manually pull or remove sif to update container. This is to avoid doing sha_payload on every launch.
        """
        singularity_args, args = self.parse_container_args(args)

        if singularity_args.singularity_sif is not None:
            self.sif = singularity_args.singularity_sif
        else:
            if self.sif.exists() and self.sif_version == self.remote_version:
                pass
            elif not self.sif.exists():
                print("No SIF found, pulling latest SIF")
                self.pull()
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
        if self.sif.exists():
            print(f"Removing local riescue.sif file")
            self.sif.unlink()
        self._check_singularity_remotes()
        singularity_build = ["pull", str(self.sif), f"{self.registry_uri}:{self.remote_string}"]
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
    def remote_version(self):
        return int(self._read_id().split("-")[0])

    @property
    def remote_string(self):
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
        "Checks that remote for gitlab is added, tries to add it otherwise"
        remote_list = self.singularity(["remote", "list"], stdout=subprocess.PIPE).stdout.decode("utf-8")
        if self.aus_registry_uri in remote_list:
            return
        print(f"Couldn't find the registry URI in {self.aus_registry_uri} remotes list, adding")
        gitlab_key = Path.home() / ".gitlab_key"
        if not gitlab_key.exists():
            raise ValueError(
                f"Unable to pull singularity container, please follow the steps at https://aus-gitlab.local.tenstorrent.com/groups/riscv/-/wikis/Onboarding#set-up-ssh-keys-on-your-login-server to set up the ~/..gitlab_key to access singularity containers"
            )
        with open(gitlab_key, "r") as f:
            token = f.read()
        self.singularity(["remote", "login", "-u", getpass.getuser(), "-p", token, self.aus_registry_uri], check=True)

    def parse_container_args(self, args):
        parser = argparse.ArgumentParser()
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
