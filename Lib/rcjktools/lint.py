import argparse
import logging
import os
import traceback
from .project import RoboCJKProject
from .objects import InterpolationError
from . import lintChecks


def commaSeparatedList(arg):
    return set(arg.split(","))


def main():
    checkNames = ", ".join(lintChecks.checks)
    parser = argparse.ArgumentParser(
        description=f"Perform lint checks on one or more rcjk projects: {checkNames}"
    )
    parser.add_argument("rcjkproject", nargs="+")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print a full traceback when an exception occurs",
    )
    parser.add_argument(
        "--include",
        type=commaSeparatedList,
        default=set(),
        help="Comma separated list of checks to include",
    )
    parser.add_argument(
        "--exclude",
        type=commaSeparatedList,
        default=set(),
        help="Comma separated list of checks to exclude",
    )
    parser.add_argument(
        "--custom-checks",
        type=existingPythonSource,
        action="append",
        default=[],
        help="A custom Python file containing custom lint checks",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)
    if args.verbose:
        lintChecks.VERBOSE = True

    for customChecksSource in args.custom_checks:
        execFile(customChecksSource)

    previousMsgGroup = None
    for projectPath in args.rcjkproject:
        project = RoboCJKProject(projectPath, decomposeClassicComponents=True)
        for checkName, checkFunc in lintChecks.checks.items():
            if args.include and checkName not in args.include:
                continue
            if checkName in args.exclude:
                continue
            try:
                for msg in checkFunc(project):
                    msgGroup = (projectPath, checkName)
                    if previousMsgGroup is not None and msgGroup != previousMsgGroup:
                        print()
                    previousMsgGroup = msgGroup
                    print(f"{projectPath}:{checkName}: {msg}")
            except Exception as e:
                print(f"{projectPath}:{checkName}: ERROR {e!r}")
                if args.verbose:
                    traceback.print_exc()


def existingPythonSource(path):
    if not os.path.isfile(path):
        raise argparse.ArgumentTypeError(f"not an existing file: '{path}'")
    if os.path.splitext(path)[1].lower() != ".py":
        raise argparse.ArgumentTypeError(f"not a Python source file: '{path}'")
    return path


def execFile(path):
    with open(path) as f:
        code = compile(f.read(), path, "exec")
        exec(code, {})


if __name__ == "__main__":
    main()
