from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

import nox

DIR = Path(__file__).parent.resolve()

nox.options.sessions = ["lint", "pylint", "tests"]


@nox.session(reuse_venv=True)
def lint(session: nox.Session) -> None:
    """
    Run the linter.
    """
    session.install("pre-commit")
    session.run("pre-commit", "run", "--all-files", *session.posargs)


@nox.session(reuse_venv=True)
def pylint(session: nox.Session) -> None:
    """
    Run PyLint.
    """
    # This needs to be installed into the package environment, and is slower
    # than a pre-commit check
    session.install(
        "-e.[dev,test]", "pylint", "hatch-fancy-pypi-readme", "setuptools-scm"
    )
    session.run("pylint", "scikit_build_core", *session.posargs)


def _run_tests(
    session: nox.Session, *, install_args: Sequence[str], run_args: Sequence[str] = ()
) -> None:
    posargs = list(session.posargs)
    env = {"PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    extra = []
    # This will not work if system CMake is too old (<3.15)
    if shutil.which("cmake") is None and shutil.which("cmake3") is None:
        extra.append("cmake")
    if shutil.which("ninja") is None:
        extra.append("ninja")
    if (3, 8) <= sys.version_info < (3, 12):
        extra.append("numpy")

    install_arg = "-e.[test,cov]" if "--cov" in posargs else "-e.[test]"
    session.install(install_arg, *extra, *install_args)
    session.run("pytest", *run_args, *posargs, env=env)


@nox.session
def tests(session: nox.Session) -> None:
    """
    Run the unit and regular tests. Includes coverage if --cov passed.
    """
    _run_tests(session, install_args=["hatch-fancy-pypi-readme", "setuptools-scm"])


@nox.session
def minimums(session: nox.Session) -> None:
    """
    Test the minimum versions of dependencies.
    """
    _run_tests(
        session,
        install_args=["--constraint=tests/constraints.txt"],
        run_args=["-Wdefault"],
    )


@nox.session(reuse_venv=True)
def docs(session: nox.Session) -> None:
    """
    Build the docs. Pass "--serve" to serve.
    """

    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true", help="Serve after building")
    args = parser.parse_args(session.posargs)

    session.install(".[docs,pyproject]")
    session.chdir("docs")
    session.run("sphinx-build", "-M", "html", ".", "_build")

    if args.serve:
        print("Launching docs at http://localhost:8000/ - use Ctrl-C to quit")
        session.run("python", "-m", "http.server", "8000", "-d", "_build/html")


@nox.session
def build_api_docs(session: nox.Session) -> None:
    """
    Build (regenerate) API docs.
    """

    session.install("sphinx")
    session.chdir("docs")
    session.run(
        "sphinx-apidoc",
        "-o",
        "api/",
        "--no-toc",
        "--force",
        "--module-first",
        "../src/scikit_build_core",
    )


@nox.session
def build(session: nox.Session) -> None:
    """
    Build an SDist and wheel.
    """

    session.install("build")
    session.run("python", "-m", "build", **session.posargs)


EXAMPLES = ["c", "abi3", "pybind11", "swig", "cython"]
if not sys.platform.startswith("win") and shutil.which("gfortran"):
    EXAMPLES.append("fortran")


@nox.session
@nox.parametrize("example", EXAMPLES, ids=EXAMPLES)
def test_doc_examples(session: nox.Session, example: str) -> None:
    session.chdir(f"docs/examples/getting_started/{example}")
    session.install(".", "--config-settings=cmake.verbose=true")
    session.run("python", "../test.py")


@nox.session(reuse_venv=True)
def downstream(session: nox.Session) -> None:
    """
    Build a downstream project.
    """

    # If running in manylinux:
    #   docker run --rm -v $PWD:/sk -w /sk -t quay.io/pypa/manylinux2014_x86_64:latest \
    #       pipx run --system-site-packages nox -s downstream -- https://github.com/...
    # (requires tomli, so allowing access to system-site-packages)

    if sys.version_info < (3, 11):
        import tomli as tomllib
    else:
        import tomllib

    parser = argparse.ArgumentParser()
    parser.add_argument("project", help="A project to build")
    parser.add_argument("--subdir", help="A subdirectory to build")
    args, remaining = parser.parse_known_args(session.posargs)

    tmp_dir = Path(session.create_tmp())
    proj_dir = tmp_dir / "_".join(args.project.split("/"))

    session.install("build", "hatch-vcs", "hatchling")
    session.install(".[pyproject]", "--no-build-isolation")

    if proj_dir.is_dir():
        session.chdir(proj_dir)
        session.run("git", "pull", external=True)
    else:
        session.run(
            "git",
            "clone",
            args.project,
            *remaining,
            proj_dir,
            "--recurse-submodules",
            external=True,
        )
        session.chdir(proj_dir)

    # Read and strip requirements
    pyproject_toml = Path("pyproject.toml")
    with pyproject_toml.open("rb") as f:
        pyproject = tomllib.load(f)
    requires = [
        x
        for x in pyproject["build-system"]["requires"]
        if "scikit-build-core" not in x.replace("_", "-")
    ]
    if not shutil.which("ninja"):
        requires.append("ninja")
    if not shutil.which("cmake"):
        requires.append("cmake")
    if requires:
        session.install(*requires)

    if args.subdir:
        session.chdir(args.subdir)

    session.run(
        "python",
        "-m",
        "build",
        "--no-isolation",
        "--skip-dependency-check",
        "--wheel",
        ".",
    )
