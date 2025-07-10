#!/usr/bin/env python3
import argparse
import json
import glob

# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0


def main():
    # Create an argument parser
    parser = argparse.ArgumentParser()

    # Add command-line arguments
    parser.add_argument("--source_json_dirs", type=str, help="Packed comma separated list of source json dirs to recursively search")
    parser.add_argument("--target_json", type=str, help="Target json to merge into")
    parser.add_argument("--output_json", type=str, help="Output json to write to if different from target json")
    parser.add_argument("--mock", action="store_true", help="Mock run, do not write to output json")

    # Parse the command-line arguments
    args = parser.parse_args()

    # Access and use the keyword arguments
    source_json_dirs = args.source_json_dirs.split(",")
    target_json = args.target_json
    output_json = args.output_json
    mock = args.mock

    base_dictionary = {}
    # Check that target json exists, if it does, load it into base_dictionary
    try:
        with open(target_json) as f:
            base_dictionary = json.load(f)
    except FileNotFoundError:
        print("Target json {} does not exist, it will be created".format(target_json))
    except json.decoder.JSONDecodeError:
        print("Target json {} is not a valid json".format(target_json))
        return

    source_jsons = []
    # try to find all source jsons in source_json_dirs and add them to source_jsons
    for source_json_dir in source_json_dirs:
        if mock:
            print("Searching for source jsons in {}".format(source_json_dir))

        try:
            source_jsons += glob.glob(source_json_dir + "/**/*cache.json", recursive=True)
        except FileNotFoundError:
            print("Source json dir {} does not exist".format(source_json_dir))
            return
        except IOError:
            print("Source json dir {} could not be used".format(source_json_dir))
            return

    if len(source_jsons) == 0:
        print("No source jsons found in {}".format(source_json_dirs))
        print("This usually indicates that there were no cache misses during the run")
        return

    # try to merge all source jsons into base_dictionary
    for source_json in source_jsons:
        if "voyager" in source_json.lower():
            continue

        if mock:
            print("Merging source json {}".format(source_json))

        try:
            with open(source_json) as f:
                source_dictionary = json.load(f)
                base_dictionary.update(source_dictionary)
        except FileNotFoundError:
            print("Source json {} does not exist".format(source_json))
            return
        except IOError:
            print("Source json {} could not be used".format(source_json))
            return
        except json.decoder.JSONDecodeError:
            print("Source json {} is not a valid json".format(source_json))
            return

    # if output json is not specified, write to target json, dont write if mock is true
    if output_json is None:
        output_json = target_json

    if not mock:
        print("Writing to {}".format(output_json))
        with open(output_json, "w") as f:
            json.dump(base_dictionary, f, indent=4, sort_keys=True)
    else:
        print("Mock run, not writing to {}".format(output_json))
        print("Output json:")
        print(json.dumps(base_dictionary, indent=4, sort_keys=True))


if __name__ == "__main__":
    main()
