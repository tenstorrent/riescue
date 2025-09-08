# Riescue Contributing Guide
Thanks for your interest in Riescue. Here you can find some information on how to contribute to the project.

Contributions require an issue to be filed. Please select the appropriate fields when [opening an issue](https://github.com/tenstorrent/riescue/issues/new/choose).

Please be sure to follow the [Code Of Conduct](./CODE_OF_CONDUCT.md).

## Bug Reports
Bug reports can be filed using an issue on the issue board.

## Feature Requests
Feature Requests can be made on the issue board.

## Support and Discussion
If you need support in using Riescue, please look in the Discussions for any previous topics. If there aren't any, please create a new Support topic to get some help in creating and debugging the issue.

## Development - Getting Started
To contribute to the Riescue repository, it's recommended to work interactively in the container. Please be sure to read the [Contributing Standards](#contribution-standards) to understand how changes can be linted and qualified before committing.

### Install Singularity
Installation for Singularity can be found [here](https://docs.sylabs.io/guides/3.0/user-guide/installation.html).
Docker containers are not currently supported but can be in the future.

### Installing Editable package
Riescue can be installed locally by cloning and `cd`ing the repo, launching the container, then running:
```bash
pip install -e .
```
to make Riescue available.

### Command line scripts
After entering the container, the command line scripts can be run inside the repo.

If Riescue has been installed using `pip`, the command line utility `riescued` can also be run outside the `riescue/` directory.

## Contribution Standards

### Pull Requests
- Contributions require an issue to be filed and a Pull Request (PR) to be created
- PRs need to have an accurate description of the changes and should link to the existing issue
- Include a clear description of changes
- Have commits with descriptive messages
- Pass all CI checks (lint, tests)
- Include relevant tests for new features
- Be focused on a single logical change
- Have changes rebased on latest main branch

### Testing
Code needs to pass lint and unit tests before it can be merged. PRs should include unit tests for any new features.

### Documentation
Please follow the [Sphinx Docstring Format](https://sphinx-rtd-tutorial.readthedocs.io/en/latest/docstrings.html) format when updating docstrings. Please add docstrings to any new python code.

### Python Code Requirements
- Using `python 3.9`, use type hints where possible
- Avoid using global variables
- Use absolute imports in `riescue/` source code
- Handle errors gracefully with appropriate exceptions

## Lint, Format, and Coding Style
This repo uses `flake8`, `pyright`, and `black` to enforce a uniform code style. This makes the code easier to read and makes pull requests easier to manage.

Contributions must pass a CI flow of lint and unit tests to be merged. Information about installing/running lint locally and the automated lint flow can be found in the [Lint Docs](../docs/lint.md).

## Running Tests Locally
Riescue uses Python's unittest framework for testing. Tests are located in the `tests/` directory and can be run using the unittest discovery mechanism. For detailed information about test structure, writing tests, and running specific test cases, see [tests/README.md](../tests/README.md).

Basic test execution:
```bash
python3 -m unittest discover -v -s tests -p "*_test.py"
```

