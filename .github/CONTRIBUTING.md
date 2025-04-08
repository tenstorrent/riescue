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

# Development - Getting Started
To contribute to the Riescue repository, it's recommended to work interactively in the container. Please be sure to read the [Contributing Standards](#contribution-standards) to understand how changes can be linted and qualified before committing.

## Install Singularity
Installation for Singularity can be found [here](https://docs.sylabs.io/guides/3.0/user-guide/installation.html).
Docker containers are not currently supported but can be in the future.

# Installing Editable package
Riescue can be installed locally by cloning and `cd`ing the repo, launching the container, then running:
```
pip install -e .
```
to make Riescue available.


## Command line scripts
After entering the container, the command line scripts can be run inside the repo.

If Riescue has been installed using `pip`, the command line utility `riescued` can also be run outside the `riescue/` directory.



# Contribution Standards
Coming soon!

## Lint, Format, and Coding Style
This repo uses `flake8` and `black` to enforce a uniform code style. This makes the code easier to read and makes pull requests easier to manage.

Contributions must pass a CI flow of lint and unit tests to be merged. Information about installing/running lint locally and the automated lint flow can be found in the [Lint Docs](../docs/lint.md).



## Running Tests Locally
Coming soon!

## Pull Request Standards
Coming soon!
