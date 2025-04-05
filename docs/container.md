
# Apptainer Container
Documentation about maintaining the container. The `README` should have some basic info about building and running the container, but for more information about using remotes and sharing containers can be found here.

## Why use a container?
Containers simplify the development environment without requiring root access. It provides a manifest of dependencies that can be installed in other environments not using a container. The container flow uses `infra/Container.def` to log all external libraries and deps needed for this library to work.

# Scripts
The definition file and the scripts to launch the conatiner are in `infra/`.

`./infra/container-build` builts the container while `./infra/container-run` launches the container.

## Running the container
Users can run commands in the container with `./infra/container-run <cmd>` , or run the script without arguments to launch an interactive bash session inside the container.

# Container Config
Optional container arguments can be added to an `./infra/.container_config` file. These arguments are not required to run the container and are for using remote directories and adding binds to the container command. This is a JSON file that includes different key-value pairs.

If no `.container_config` file is found, the container will assume the local `.sif` is valid.

## Remotes
To reduce building the container, `singularity` offers remote distributions of the container using a remote registry. Users can take advantage of this by adding remote information to a `./infra/.container_config` file. Include a `"registry_uri"`key-value pair, as well as a `"registry_remote"` key-value pair with the respective addresses.


## Binds
By default singularity only binds specific directories to the container. To include binds by default, include a `"binds"` string in the `./infra/.container_config` file. This should be a string of directories joined by `,`. Any binds that shouldn't be mirrored directly should have a `:` between them. E.g. `/foo:/bar` binds `/foo` to `/bar`