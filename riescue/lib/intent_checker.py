# SPDX-FileCopyrightText: © 2025 Tenstorrent AI ULC
# SPDX-License-Identifier: Apache-2.0

import shutil
import sys
from pathlib import Path


"""
    To work with the intent checker, you need to create a class that implements the check method.
"""


class Rule:
    def __init__(self) -> None:
        self.test_labels = None
        self.label_appearances = None

    def get_test_labels(self, source_filepath: str) -> list:
        # open the file and look for lines like: ;#discrete_test(test=test_plugins6)
        test_labels = []
        with open(source_filepath, "r") as source_file:
            lines = source_file.readlines()
            for index in range(0, len(lines), 1):
                first_line = lines[index]
                label_guess = None
                test_declared = False
                if first_line.startswith(";#discrete_test"):
                    test_declared = True
                    label_guess = first_line.split("test=")[1].split(")")[0]

                if not test_declared:
                    continue

                if index + 1 < len(lines):
                    second_line = lines[index + 1]
                    if ":" in second_line and not second_line.startswith("#"):
                        label_guess = second_line.split(":")[0].strip()

                if index + 2 < len(lines):
                    third_line = lines[index + 2]
                    if ":" in third_line and not third_line.startswith("#"):
                        label_guess = third_line.split(":")[0].strip()

                if label_guess is not None:
                    test_labels.append(label_guess)

        assert len(test_labels) > 0, "No test labels were found in the source file"
        return test_labels

    def validate_log_file(self, log_filepath: str) -> bool:
        with open(log_filepath, "r") as log_file:
            lines = log_file.read()
            count = lines.count("__section__code")
            if count > 0:
                return False

        return True

    # If the section labels appear only incidentally, we can still proceed with the checks since a given test label
    # will not refer to multiple addresses. Therefor if it appears at all it has the potential to appear the correct
    # number of times.
    def conditionally_validate_log_file(self, log_filepath: str, label_appearances: dict) -> bool:
        for test_label in label_appearances:
            for hartid in label_appearances[test_label]:
                if label_appearances[test_label][hartid] == 0:
                    validation = self.validate_log_file(log_filepath)
                    if not validation:
                        return (False, "The log file contained a section label, likely aliasing a test label, making it impossible to check for label appearances.")
        return (True, "The log file did not contain a section label aliasing a test label.")

    def get_label_addresses(self, test_labels: list, disasm_filepath: str) -> dict:
        all_labels_to_addresses = dict()
        with open(disasm_filepath, "r") as disasm_file:
            for line in disasm_file.readlines():
                if line.rstrip().endswith(">:"):
                    split_line = line.split()
                    address = split_line[0]
                    label = split_line[1][1:-2]
                    if label in test_labels:
                        all_labels_to_addresses[label] = address

        addresses_to_test_labels = dict()
        last_test_label = None
        try:
            for test_label in test_labels:
                last_test_label = test_label
                addresses_to_test_labels[all_labels_to_addresses[test_label]] = test_label
        except KeyError:
            # print(f"Test label {last_test_label} was not found in the disassembly file")
            # print(f"Test labels: {test_labels}")
            # print(f"Addresses to test labels: {addresses_to_test_labels}")
            # print(f"All labels to addresses: {all_labels_to_addresses}")
            raise

        return addresses_to_test_labels

    def init_label_appearances(self, test_labels: list, num_cpus: int) -> dict:
        label_appearances = dict()
        for test_label in test_labels:
            label_appearances[test_label] = dict()
            for i in range(num_cpus):
                label_appearances[test_label][i] = 0

        return label_appearances

    def count_label_appearances(self, log_filepath: str, addresses_to_test_labels: dict, num_cpus: int) -> dict:
        label_appearances = self.init_label_appearances(list(addresses_to_test_labels.values()), num_cpus)

        # open the log file and look for lines like ': >>>>  test01\n'
        with open(log_filepath, "r") as log_file:
            for line in log_file.readlines():
                if not line.startswith("core"):
                    continue
                split_line = line.split(":")
                hartid = int(split_line[0].split()[1])
                address = split_line[1].split()[0]
                if address.startswith("0x"):
                    address = address[2:]
                else:
                    continue
                label = addresses_to_test_labels.get(address, None)
                if label:
                    label_appearances[label][hartid] += 1

        return label_appearances

    # Replace with a specific check method for each rule
    def check(self, source_filepath: str, log_filepath: str, disasm_filepath: str, features: dict) -> tuple[bool, str]:
        self.test_labels = self.get_test_labels(source_filepath)
        self.addresses_to_test_labels = self.get_label_addresses(self.test_labels, disasm_filepath)
        self.label_appearances = self.count_label_appearances(log_filepath, self.addresses_to_test_labels, features["num_cpus"])

        validation = self.conditionally_validate_log_file(log_filepath, self.label_appearances)
        if not validation[0]:
            return validation

        return (True, "This is a base rule and is just initializing the test labels and label appearances")


"""
    The MPRule checks that all tests appear the correct number of times on each hart.
    Correct number of times is expected to be equal to the repeat_times feature.
    Although it is called MP, it can and should be used for single hart simulations as well.
"""


class MPRule(Rule):
    def confirm_label_appearances(self, label_appearances: dict, repeat_times: int) -> tuple[bool, str]:
        for test_label in label_appearances:
            for hartid in label_appearances[test_label]:
                if label_appearances[test_label][hartid] != repeat_times:
                    label_appearances_list = [label_appearances.items()]
                    label_appearances_list.sort()
                    return (False, f"Test {test_label} did not appear {repeat_times} times on hart {hartid}")

        return (True, "All tests appeared the correct number of times")

    def check(self, source_filepath: str, log_filepath: str, disasm_filepath: str, features: dict) -> tuple[bool, str]:
        # if we dont have the attribute self.addresses_to_test_labels, we need to initialize it by calling parent class check
        if not hasattr(self, "addresses_to_test_labels"):
            validation = super().check(source_filepath, log_filepath, disasm_filepath, features)
            if not validation[0]:
                return validation
            assert self.addresses_to_test_labels, "The addresses to test labels were not initialized"

        num_cpus = features["num_cpus"]
        repeat_times = features["repeat_times"]
        label_appearances = self.count_label_appearances(log_filepath, self.addresses_to_test_labels, num_cpus)

        return self.confirm_label_appearances(label_appearances, repeat_times)


"""
    The ProportionalTestSelectionRule checks that the tests were selected in proportion to each other, assuming
    a uniform random chance to pick any test.
    There is quite a bit of wiggle room allowed in this rule, since it is expected that the ratio improves with
    test length.
"""


class ProportionalTestSelectionRule(Rule):
    def calculate_label_appearance_ratios(self, label_appearances: dict) -> dict:
        ratios = dict()
        for first_test_label in label_appearances.keys():
            first_count_all_cpus = sum(label_appearances[first_test_label].values())
            for second_test_label in label_appearances.keys():
                second_count_all_cpus = sum(label_appearances[second_test_label].values())
                if second_count_all_cpus == 0:
                    ratios[(first_test_label, second_test_label)] = 0
                else:
                    ratios[(first_test_label, second_test_label)] = first_count_all_cpus / second_count_all_cpus

        return ratios

    def confirm_label_appearance_ratios(self, ratios: dict, minimum: float, maximum: float) -> tuple[bool, str]:
        for ratio in ratios:
            if ratios[ratio] < minimum or ratios[ratio] > maximum:
                return (False, f"Ratio {ratio} : {ratios[ratio]} was not within the acceptable range of {minimum} to {maximum}")

        return (True, "All ratios were within the acceptable range")

    def check(self, source_filepath: str, log_filepath: str, disasm_filepath: str, features: dict) -> tuple[bool, str]:
        # if we dont have the attribute self.addresses_to_test_labels, we need to initialize it by calling parent class check
        if not hasattr(self, "addresses_to_test_labels"):
            validation = super().check(source_filepath, log_filepath, disasm_filepath, features)
            if not validation[0]:
                return validation
            assert self.addresses_to_test_labels, "The addresses to test labels were not initialized"

        label_appearances = self.count_label_appearances(log_filepath, self.addresses_to_test_labels, features["num_cpus"])
        ratios = self.calculate_label_appearance_ratios(label_appearances)

        return self.confirm_label_appearance_ratios(ratios, minimum=0.25, maximum=4.0)


"""
    The NoFaultsRule checks that there were no faults in the log file.
    A fault is considered to be any line in the log file that contains the word "fault".
"""


class NoFaultsRule(Rule):
    def check(self, source_filepath: str, log_filepath: str, disasm_filepath: str, features: dict) -> tuple[bool, str]:
        # open the log file and look for lines like 'ERROR: 0'
        # enumerate the lines and return false if any of them contain "fault" and say which line
        with open(log_filepath, "r") as log_file:
            for i, line in enumerate(log_file.readlines()):
                if "fault" in line:
                    return (False, f"There was a fault on line {i} of the log file")

        return (True, "There were no faults")


"""
    A simple check for reproducibility. Certain files are not expected to differ between runs
    when the same seed value is used. This tests checks if that property is maintained.

    The gold standard file can be a log or disassembly file.
"""


class GoldStandardRule(Rule):
    def diff_text_files(self, path_a: str, path_b: str) -> tuple[bool, str]:
        with open(path_a, "r") as file_a:
            with open(path_b, "r") as file_b:

                file_a_string = file_a.read()
                file_b_string = file_b.read()

                print(f"File A: {path_a}")
                print(f"File B: {path_b}")

                if file_a_string == file_b_string:
                    return (True, "The files are identical")
                else:
                    # difference is in the files
                    return (False, "The files are not identical")

    def check(self, source_filepath: str, log_filepath: str, disasm_filepath: str, features: dict) -> tuple[bool, str]:
        gold_standard_filepath = features["gold_standard_filepath"]
        file_ending = gold_standard_filepath.split(".")[-1]
        if file_ending == "log":
            return self.diff_text_files(gold_standard_filepath, log_filepath)
        elif file_ending == "dis":
            return self.diff_text_files(gold_standard_filepath, disasm_filepath)
        elif file_ending == "S":
            return self.diff_text_files(gold_standard_filepath, source_filepath)
        else:
            return (False, "The gold standard file ending was not recognized")


class IntentChecker:
    def __init__(self, source_filepath: str, log_filepath: str, disasm_filepath: str, features: dict):
        self.base_rule = Rule()
        base_result = self.base_rule.check(source_filepath, log_filepath, disasm_filepath, features)
        self.disable = False
        if not base_result[0]:
            self.disable = True
            return

        assert hasattr(self.base_rule, "test_labels"), "The base rule did not initialize the test labels"
        assert hasattr(self.base_rule, "addresses_to_test_labels"), "The base rule did not initialize the addresses to test labels"

        # Determine appropriate ruleset based on the active features in the test
        if features.get("endless", False):
            self.rules = [ProportionalTestSelectionRule()]
        else:
            self.rules = [MPRule(), ProportionalTestSelectionRule()]

        if not features.get("page_faults_intentional", False):
            self.rules.append(NoFaultsRule())

        if features.get("gold_standard_filepath", "") != "":
            self.rules.append(GoldStandardRule())

        # replace the rules test_labels with the base_rule's
        # this avoids opening and scanning the files multiple times
        for rule in self.rules:
            rule.test_labels = self.base_rule.test_labels
            setattr(rule, "addresses_to_test_labels", self.base_rule.addresses_to_test_labels)

        self.source_filepath = source_filepath
        self.log_filepath = log_filepath
        self.disasm_filepath = disasm_filepath
        self.features = features

    def check(self) -> tuple[bool, str]:
        if self.disable:
            return (True, "A test label is likely aliased by a section label in the log file, making it impossible to check for label appearances.")

        for rule in self.rules:
            result, message = rule.check(self.source_filepath, self.log_filepath, self.disasm_filepath, self.features)
            if not result:
                return (False, message)

        return (True, "All intent checks passed")


"""
    Caller is responsible for making sure that either the gold_standard_filepath is a valid path
    or that the search_path is a valid path to prepend to the gold_standard_filepath.

    The result is that any gold files that might be created are put into the quals output directories
    so they can be automatically cleared by the clean command.

    a gold standard filepath of '' will disable the gold standard test feature for this test.
"""


def run_intent_checker(riescue_d_gold_test, gold_standard_filepath, replace_gold_standard, repeat_times, num_cpus, testname, search_path, page_faults_intentional, parallel_mode):
    # If this is a standard gold test, compare assembly files, otherwise compare disassembly files
    # comparing disassembly will evaluate of the stability of address assignment in riescue-d

    file_to_check = f"{testname}.S" if not riescue_d_gold_test else f"{testname}.dis"

    # Looks like this is doing some searching. This should probably be refactored with to use paths
    gold_standard_filepath = Path(gold_standard_filepath)
    search_path = Path(search_path)
    if not gold_standard_filepath.exists():
        new_path = search_path / gold_standard_filepath.name
        if not new_path.exists():
            raise FileNotFoundError(f"new_path does not exist - {new_path}")
        new_path.mkdir(mode=511, parents=True, exist_ok=True)
    else:
        new_path = gold_standard_filepath
    if new_path.is_dir():
        gold_standard_filepath = new_path / file_to_check
    else:
        gold_standard_filepath = new_path

    if replace_gold_standard:
        shutil.copy2(f"{file_to_check}", gold_standard_filepath)

    intent_checker = IntentChecker(
        source_filepath=f"./{testname}.S",
        log_filepath=f"{testname}_spike.log",
        disasm_filepath=f"{testname}.dis",
        features={
            "num_cpus": num_cpus,
            "repeat_times": max(repeat_times, 1),
            "endless": repeat_times < 0 or parallel_mode,
            "gold_standard_filepath": gold_standard_filepath,
            "page_faults_intentional": page_faults_intentional,
        },
    )

    status, message = intent_checker.check()
    if not status:
        print("Test " + "\x1b[6;30;41m" + "FAILED" + "\x1b[0m" + f" on ISS, {message}")
        raise RuntimeError("Test failed on ISS")
    return status, message
