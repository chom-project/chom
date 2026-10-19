"""Command-line interface for chom."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

from .builds import BUILD_SYSTEMS
from .formula import FormulaError, parse_formula, select_formula
from .installer import InstallError, host_arch, host_platform, install_formula, list_installed, remove_package
from .repository import (
    DEFAULT_REPOSITORY,
    RepositoryError,
    add_repository,
    all_cached_formulas,
    list_repositories,
    remove_repository,
    update_repository,
)


def command_validate(args: argparse.Namespace) -> int:
    try:
        formula = parse_formula(args.formula)
        selected = select_formula(formula, args.platform, args.arch)
    except FormulaError as exc:
        print(f"chom: {exc}", file=sys.stderr)
        return 2
    print(f"valid formula: {formula['name']} {formula['version']}")
    print(f"build system: {selected['build']['system']}")
    return 0


def command_info(args: argparse.Namespace) -> int:
    try:
        formula = parse_formula(args.formula)
        selected = select_formula(formula, args.platform, args.arch)
    except FormulaError as exc:
        print(f"chom: {exc}", file=sys.stderr)
        return 2
    print(f"{formula['name']} {formula['version']}")
    print(formula["description"])
    print(f"license: {formula['license']}")
    print(f"build system: {selected['build']['system']}")
    print(f"source: {selected['source']['url']}")
    print(f"platform: {args.platform}/{args.arch}")
    return 0



def command_install(args: argparse.Namespace) -> int:
    try:
        if args.file:
            if args.package:
                raise RepositoryError("use either a package name or --file, not both")
            formula = parse_formula(args.file)
        else:
            if not args.package:
                raise RepositoryError("provide a package name or use --file PATH")
            matches = [item for item in all_cached_formulas() if item["formula"]["name"].casefold() == args.package.casefold()]
            if not matches:
                raise RepositoryError(f"package not found in configured repositories: {args.package}")
            repository_order = [item["name"] for item in list_repositories()]
            selected_repo = next((name for name in repository_order if any(item["repo"] == name for item in matches)), None)
            repo_matches = [item for item in matches if item["repo"] == selected_repo]
            formula_entry = sorted(repo_matches, key=lambda item: item["formula"]["version"])[-1]
            formula = formula_entry["formula"]
            print(f"Formula: {formula['name']} {formula['version']} ({formula_entry['repo']})")
        formula = select_formula(formula, host_platform(), host_arch())
        install_formula(formula, yes=args.yes)
    except (FormulaError, InstallError, RepositoryError, OSError, RuntimeError, EOFError) as exc:
        print(f"chom: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"chom: dependency installation failed with exit code {exc.returncode}", file=sys.stderr)
        return 1
    return 0


def command_hash(args: argparse.Namespace) -> int:
    digest = hashlib.sha256()
    try:
        with args.file.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        print(f"chom: cannot hash {args.file}: {exc}", file=sys.stderr)
        return 2
    print(f"{digest.hexdigest()}  {args.file}")
    return 0


def command_systems(_: argparse.Namespace) -> int:
    for name, description in BUILD_SYSTEMS.items():
        print(f"{name:12} {description}")
    return 0


def command_list(_: argparse.Namespace) -> int:
    packages = list_installed()
    if not packages:
        print("No packages installed.")
        return 0
    for package in packages:
        print(f"{package['name']} {package['version']} ({package.get('platform', 'unknown')}/{package.get('architecture', 'unknown')})")
    return 0


def command_remove(args: argparse.Namespace) -> int:
    try:
        candidates = [package for package in list_installed() if package["name"] == args.package]
        if args.version:
            candidates = [package for package in candidates if package["version"] == args.version]
        if not candidates:
            raise InstallError(f"package is not installed: {args.package}")
        if len(candidates) > 1 and not args.all_versions:
            raise InstallError(f"multiple versions of {args.package} are installed; specify --version or --all")
        targets = ", ".join(f"{item['name']} {item['version']}" for item in candidates)
        if not args.yes and input(f"Remove {targets}? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Removal cancelled.")
            return 0
        removed = remove_package(args.package, args.version, all_versions=args.all_versions)
    except (InstallError, OSError, EOFError) as exc:
        print(f"chom: {exc}", file=sys.stderr)
        return 1
    for path in removed:
        print(f"Removed: {args.package} {path.name}")
    return 0


def command_search(args: argparse.Namespace) -> int:
    try:
        entries = all_cached_formulas()
    except RepositoryError as exc:
        print(f"chom: {exc}", file=sys.stderr)
        return 1
    query = args.query.casefold()
    if args.repo:
        entries = [item for item in entries if item["repo"].casefold() == args.repo.casefold()]
    found = []
    for item in entries:
        formula = item["formula"]
        searchable = " ".join((formula["name"], formula["version"], formula["description"])).casefold()
        if query in searchable:
            found.append(item)
    for item in found:
        formula = item["formula"]
        print(f"{formula['name']} {formula['version']}\t{formula['description']}\t{item['repo']}")
    if not found:
        print("No matching formulas found.")
    return 0


def command_repo(args: argparse.Namespace) -> int:
    try:
        if args.repo_command == "list":
            for repository in list_repositories():
                marker = " (default)" if repository["name"] == DEFAULT_REPOSITORY["name"] else ""
                print(f"{repository['name']}{marker}\t{repository['url']}\t{repository.get('branch') or 'default branch'}")
            return 0
        if args.repo_command == "add":
            repository = add_repository(args.url, args.name, args.branch)
            print(f"Added repository: {repository['name']} ({repository['url']})")
            return 0
        if args.repo_command == "remove":
            remove_repository(args.name)
            print(f"Removed repository: {args.name}")
            return 0
        repositories = list_repositories()
        targets = [item for item in repositories if not args.name or item["name"].casefold() == args.name.casefold()]
        if not targets:
            raise RepositoryError(f"repository not found: {args.name}")
        for repository in targets:
            formulas = update_repository(repository)
            print(f"Updated {repository['name']}: {len(formulas)} formulas ({repository.get('branch')})")
        return 0
    except (RepositoryError, OSError) as exc:
        print(f"chom: {exc}", file=sys.stderr)
        return 1


def _host_platform() -> str:
    import platform

    return {"Darwin": "macos", "Windows": "windows"}.get(platform.system(), "linux")


def _host_arch() -> str:
    import platform

    machine = platform.machine().lower()
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chom", description="Cross-platform package manager")
    parser.add_argument("--version", action="version", version="chom 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, handler, help_text in (
        ("validate", command_validate, "check a .cho formula"),
        ("info", command_info, "show package formula details"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("formula", type=Path)
        command.add_argument("--platform", choices=("linux", "macos", "windows"), default=_host_platform())
        command.add_argument("--arch", choices=("x86_64", "arm64", "aarch64", "i686"), default=_host_arch())
        command.set_defaults(handler=handler)
    install = subparsers.add_parser("install", help="install a package from configured repositories")
    install.add_argument("package", nargs="?", help="package name from a configured repository")
    install.add_argument("--file", type=Path, help="install from a local .cho formula file")
    install.add_argument("--yes", action="store_true", help="skip the installation confirmation")
    install.set_defaults(handler=command_install)
    hash_command = subparsers.add_parser("hash", help="calculate a file's SHA-256 digest")
    hash_command.add_argument("file", type=Path)
    hash_command.set_defaults(handler=command_hash)
    systems = subparsers.add_parser("systems", help="list supported build systems")
    systems.set_defaults(handler=command_systems)
    list_command = subparsers.add_parser("list", aliases=("ls",), help="list installed packages")
    list_command.set_defaults(handler=command_list)
    remove = subparsers.add_parser("remove", aliases=("uninstall",), help="remove an installed package")
    remove.add_argument("package")
    remove.add_argument("--version")
    remove.add_argument("--all", dest="all_versions", action="store_true", help="remove every installed version")
    remove.add_argument("--yes", action="store_true", help="skip the removal confirmation")
    remove.set_defaults(handler=command_remove)
    search = subparsers.add_parser("search", help="search configured formula repositories")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--repo", help="limit search to one configured repository")
    search.set_defaults(handler=command_search)
    repo = subparsers.add_parser("repo", help="manage formula repositories")
    repo_subparsers = repo.add_subparsers(dest="repo_command", required=True)
    repo_list = repo_subparsers.add_parser("list", help="list configured repositories")
    repo_list.set_defaults(handler=command_repo)
    repo_add = repo_subparsers.add_parser("add", help="add a GitHub formula repository")
    repo_add.add_argument("url")
    repo_add.add_argument("--name")
    repo_add.add_argument("--branch")
    repo_add.set_defaults(handler=command_repo)
    repo_remove = repo_subparsers.add_parser("remove", help="remove a configured repository")
    repo_remove.add_argument("name")
    repo_remove.set_defaults(handler=command_repo)
    repo_update = repo_subparsers.add_parser("update", help="download formula indexes")
    repo_update.add_argument("name", nargs="?")
    repo_update.set_defaults(handler=command_repo)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.handler(args)
