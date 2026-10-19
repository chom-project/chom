"""Parsing and validation for .cho package formulas."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


class FormulaError(ValueError):
    """A formula could not be parsed or does not match the schema."""


BUILD_OPTIONS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "prebuilt": {},
    "python": {"interpreter": str},
    "cmake": {"generator": str, "build_type": str},
    "meson": {"backend": str, "build_type": str},
    "autotools": {"autoreconf": bool},
    "make": {"jobs": int},
    "ninja": {"jobs": int},
    "cargo": {"profile": str, "locked": bool, "features": list, "no_default_features": bool},
    "go": {"packages": list, "tags": list, "ldflags": str},
    "npm": {"script": str, "production": bool},
    "pnpm": {"script": str, "production": bool},
    "yarn": {"script": str, "production": bool},
    "bun": {"script": str, "production": bool},
    "pip": {"wheel": bool},
    "poetry": {"groups": list},
    "uv": {"groups": list},
    "hatch": {"environment": str},
    "dotnet": {"configuration": str, "framework": str, "runtime": str},
    "maven": {"profiles": list},
    "gradle": {"tasks": list},
    "xcodebuild": {"scheme": str, "configuration": str, "sdk": str},
}

ROOT_KEYS = {
    "format", "name", "version", "description", "license", "homepage",
    "source", "build", "dependencies", "platform", "platforms", "architectures", "install",
}
SOURCE_KEYS = {"url", "sha256", "archive", "strip_components"}
BUILD_KEYS = {
    "system", "configure_args", "build_args", "install_args", "env", "options",
}
PLATFORMS = {"linux", "macos", "windows"}
ARCHITECTURES = {"x86_64", "arm64", "aarch64", "i686"}
ARCHIVE_TYPES = {"auto", "tar", "tar.gz", "tar.xz", "tar.bz2", "zip", "dmg", "none"}


def _fail(path: str, message: str) -> None:
    raise FormulaError(f"{path}: {message}")


def _unknown_keys(data: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        _fail(context, f"unknown field(s): {', '.join(sorted(unknown))}")


def _string(data: dict[str, Any], key: str, context: str, *, required: bool = True) -> str | None:
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(f"{context}.{key}", "must be a non-empty string")
    return value


def _string_list(value: Any, context: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _fail(context, "must be an array of strings")


def _parse_source(source: Any, context: str, *, require_all: bool) -> None:
    if not isinstance(source, dict):
        _fail(context, "must be a table")
    _unknown_keys(source, SOURCE_KEYS, context)
    url = _string(source, "url", context, required=require_all)
    digest = _string(source, "sha256", context, required=require_all)
    if url is not None:
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http", "file"}:
            _fail(f"{context}.url", "must use https, http, or file")
        if parsed.scheme in {"https", "http"} and not parsed.netloc:
            _fail(f"{context}.url", "must include a host")
    if digest is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        _fail(f"{context}.sha256", "must be a 64-character hexadecimal SHA-256 digest")
    archive = source.get("archive", "auto")
    if not isinstance(archive, str) or archive not in ARCHIVE_TYPES:
        _fail(f"{context}.archive", f"must be one of: {', '.join(sorted(ARCHIVE_TYPES))}")
    strip_components = source.get("strip_components", 0)
    if not isinstance(strip_components, int) or isinstance(strip_components, bool) or strip_components < 0:
        _fail(f"{context}.strip_components", "must be a non-negative integer")


def _parse_build(build: Any, context: str) -> None:
    if not isinstance(build, dict):
        _fail(context, "must be a table")
    _unknown_keys(build, BUILD_KEYS, context)
    system = _string(build, "system", context)
    if system not in BUILD_OPTIONS:
        _fail(f"{context}.system", f"unsupported build system '{system}'")
    for key in ("configure_args", "build_args", "install_args"):
        if key in build:
            _string_list(build[key], f"{context}.{key}")
    env = build.get("env", {})
    if not isinstance(env, dict) or any(
        not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
        or not isinstance(value, str)
        for key, value in env.items()
    ):
        _fail(f"{context}.env", "must be a table of environment variable names and string values")
    options = build.get("options", {})
    if not isinstance(options, dict):
        _fail(f"{context}.options", "must be a table")
    allowed = BUILD_OPTIONS[system]
    for key, value in options.items():
        if key not in allowed:
            _fail(f"{context}.options.{key}", f"not supported by {system}")
        if not isinstance(value, allowed[key]) or (isinstance(value, bool) and allowed[key] is int):
            expected = getattr(allowed[key], "__name__", "valid value")
            _fail(f"{context}.options.{key}", f"must be {expected}")


def _parse_platforms(platforms: Any) -> None:
    if not isinstance(platforms, dict):
        _fail("platform", "must be a table")
    _unknown_keys(platforms, PLATFORMS, "platform")
    for platform, config in platforms.items():
        context = f"platform.{platform}"
        if not isinstance(config, dict):
            _fail(context, "must be a table")
        _unknown_keys(config, {"source", "build", "install", "architectures", *ARCHITECTURES}, context)
        if "architectures" in config:
            _string_list(config["architectures"], f"{context}.architectures")
            unknown_arches = set(config["architectures"]) - ARCHITECTURES
            if unknown_arches or not config["architectures"]:
                _fail(f"{context}.architectures", "must be a non-empty array of supported architectures")
        if "source" in config:
            _parse_source(config["source"], f"{context}.source", require_all=False)
        if "build" in config:
            _parse_build(config["build"], f"{context}.build")
        if "install" in config:
            _parse_install(config["install"], f"{context}.install")
        for arch in ARCHITECTURES.intersection(config):
            arch_config = config[arch]
            arch_context = f"{context}.{arch}"
            if not isinstance(arch_config, dict):
                _fail(arch_context, "must be a table")
            _unknown_keys(arch_config, {"source", "build", "install"}, arch_context)
            if "source" in arch_config:
                _parse_source(arch_config["source"], f"{arch_context}.source", require_all=False)
            if "build" in arch_config:
                _parse_build(arch_config["build"], f"{arch_context}.build")
            if "install" in arch_config:
                _parse_install(arch_config["install"], f"{arch_context}.install")


def _parse_install(install: Any, context: str) -> None:
    if not isinstance(install, dict):
        _fail(context, "must be a table")
    _unknown_keys(install, {"binaries", "files", "launchers", "script", "command", "config_path", "config_content"}, context)
    if "binaries" in install:
        _string_list(install["binaries"], f"{context}.binaries")
    files = install.get("files", {})
    if not isinstance(files, dict) or any(
        not isinstance(source, str) or not isinstance(target, str)
        or PurePosixPath(source).is_absolute() or ".." in PurePosixPath(source).parts
        or PurePosixPath(target).is_absolute() or ".." in PurePosixPath(target).parts
        for source, target in files.items()
    ):
        _fail(f"{context}.files", "must map safe relative source paths to safe home-relative destination paths")
    launchers = install.get("launchers", {})
    if not isinstance(launchers, dict) or any(
        not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._+-]+", name)
        or not isinstance(source, str) or not source.strip()
        or PurePosixPath(source).is_absolute() or ".." in PurePosixPath(source).parts
        for name, source in launchers.items()
    ):
        _fail(f"{context}.launchers", "must map command names to safe relative paths in the extracted package")
    for field in ("script", "command", "config_path", "config_content"):
        if field in install:
            _string(install, field, context)
    if "config_path" in install:
        config_path = PurePosixPath(install["config_path"])
        if config_path.is_absolute() or ".." in config_path.parts:
            _fail(f"{context}.config_path", "must be a relative path without '..'")
        if "config_content" not in install:
            _fail(f"{context}.config_content", "is required when config_path is set")


def parse_formula(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail(str(path), f"cannot read file: {exc}")
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        _fail(str(path), "file is not valid UTF-8")
    except tomllib.TOMLDecodeError as exc:
        location = ""
        if getattr(exc, "lineno", None):
            location = f"line {exc.lineno}, column {exc.colno}: "
        _fail(str(path), f"invalid TOML: {location}{exc}")

    if not isinstance(data, dict):
        _fail(str(path), "must contain a TOML table")
    _unknown_keys(data, ROOT_KEYS, "formula")
    if type(data.get("format")) is not int or data["format"] != 1:
        _fail("format", "must be integer 1")
    for field in ("name", "version", "description", "license"):
        _string(data, field, "formula")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+_.-]*", data["name"]):
        _fail("name", "may contain only letters, numbers, '+', '_', '.', and '-'")
    homepage = data.get("homepage")
    if homepage is not None:
        _string(data, "homepage", "formula")
    _parse_source(data.get("source"), "source", require_all=True)
    _parse_build(data.get("build"), "build")

    dependencies = data.get("dependencies", [])
    _string_list(dependencies, "dependencies")
    requirement = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?(?:\s*(?:===|==|!=|~=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+(?:\s*,\s*(?:===|==|!=|~=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+)*)?")
    if any(not requirement.fullmatch(item) for item in dependencies):
        _fail("dependencies", "must contain package names with optional version constraints")
    if len(set(dependencies)) != len(dependencies):
        _fail("dependencies", "must not contain duplicates")

    if "platform" in data:
        _parse_platforms(data["platform"])
    for field, allowed_values in (
        ("platforms", PLATFORMS),
        ("architectures", ARCHITECTURES),
    ):
        if field in data:
            _string_list(data[field], field)
            unknown = set(data[field]) - allowed_values
            if unknown:
                _fail(field, f"unsupported value(s): {', '.join(sorted(unknown))}")
            if not data[field]:
                _fail(field, "must not be empty")
    install = data.get("install", {})
    _parse_install(install, "install")
    if data["build"]["system"] == "python":
        _string(install, "script", "install")
        _string(install, "command", "install")
    elif data["build"]["system"] == "prebuilt" and not install.get("binaries") and not install.get("files") and not install.get("launchers"):
        _fail("install", "prebuilt formulas must declare binaries, files, or launchers")

    return data


def select_formula(formula: dict[str, Any], platform: str, architecture: str) -> dict[str, Any]:
    result = dict(formula)
    platform_data = formula.get("platform", {}).get(platform, {})
    platform_data = dict(platform_data)
    arch_data = platform_data.pop(architecture, {})
    for key in ("source", "build", "install"):
        for override in (platform_data.get(key), arch_data.get(key)):
            if override:
                merged = {**result[key], **override}
                for nested in ("options", "env", "source", "files", "launchers"):
                    if isinstance(result[key].get(nested), dict) and isinstance(override.get(nested), dict):
                        merged[nested] = {**result[key][nested], **override[nested]}
                result[key] = merged
    return result
