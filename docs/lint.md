
# Lint
Linting tools are incorporated into Riescue to enforce a uniform code style and make change requests easier to review. A uniform coding style also makes other parts of code much easier to analyze.

The following tools are used in CI/CD flows and are included in the container.

## CI/CD
The CI/CD pipeline starts with running lint. If either fail, the pipeline will fail. This ensures that all the python code being added to the repo has uniform styling. Be sure to run lint locally before submitting changes.

If there's interest a pre-commit install guide can be added for automatically running the lint flow when commiting.

## `black`
`black` is a formatting tool that targets whitespace rules. Info on the current black style can be found on the [Black Code Style Page](https://black.readthedocs.io/en/stable/the_black_code_style/current_style.html).

### Installing Black Locally
Users can install `black` using the following pip install command:
```
python3 -m pip install black --user
```

### Usage
`black` reads a config file using the `--config` argument on a TOML file with a `[tool.black]` section along with default arguments. The file is `.black`.

You can run lint locally and apply changes using:
```
./infra/container-run black .
```
- Adding the `--diff` switch shows what changes need to be made.

#### CI flow
The CI flow can be replicated by running:
```
./infra/container-run black   --check
```
This fails if there are changes that are needed and exits 0 if no changes are needed.


## `flake8`
`flake8` is a style guide enforcment tool. Info can be found on the [`flake8` site](https://flake8.pycqa.org/en/latest/)

### Usage
`flake8` reads a config file using the `--config` argument on a TOML with a `[flake8]` section along with default arguments. It can be found in `.flake8`

#### CI flow
The CI flow can be replicated by running:
```sh
./infra/container-run flake8 --config .flake8 .
```

## `pyright`
`pyright` is a static type checker for python. It's used to catch errors and edge cases in code before running. This prevents runtime errors (undefined variables, incorrect type usage) before longer unit testing. It also helps to enforce a statically-typed style to improve code readability.

Theres multiple static type checkers out there, but `pyright` was selected becuase it's used by the [Pylance](https://marketplace.visualstudio.com/items?itemName=ms-python.vscode-pylance) VS Code extension. It's recommended to use to avoid type issues before running the CI flow. Having typed python code also makes it easier for IDEs to jump to object definitions and track valid/invalid variable and methods.

This project has a dependency on the [pyright-python](https://github.com/RobertCraigie/pyright-python) package to call pyright using python.

### Usage

`pyright` reads a `pyrightconfig.json`. Since VS Code defaults to `pyrightconfig.json`, we point to the version controlled `pyproject.toml` instead. Directories that should pass static type checkers will be updated in this file.

```sh
./infra/container-run pyright -p pyproject.toml
```


# Git Hook and Pre-commit Hook Integration for Lint
Git hooks are scripts that are automatically triggered by a commit. The [git hooks](https://git-scm.com/book/ms/v2/Customizing-Git-Git-Hooks) documentation has some information on setting them up.

They can be used to save some time by run `black` before committing changes. Some recommended setups are included below:

## Pre-commit
Pre-commit is a framework for managing pre-commit git hooks.

By default, pre-commit pulls Black from its GitHub repository to ensure a reproducible environment. However, if you prefer to use the version of Black installed via pip on your system, you can set up a local hook. Note that this approach relies on your local environment, so it won't offer the same isolation or reproducibility as the default configuration.

### Steps to Use a Local Hook
See the [Installing Black Locally](#installing-black-locally) section to install black outside the container.

1. Install `pre-commit` as a tool and install this repo with:
```
python3 -m pip install pre-commit --user
pre-commit install
```

This uses the checked in `.pre-commit-config.yaml` to use a pre-commit hook to automatically `black` python files.
