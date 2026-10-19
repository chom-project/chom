"""Install verified package sources into a per-user prefix."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


class InstallError(RuntimeError):
    """An installation could not be completed safely."""


class InstallProgress:
    def __init__(self, label: str):
        self.label = label
        self.tty = sys.stderr.isatty()
        self.last_update = 0.0

    def update(self, percent: int, detail: str = "") -> None:
        percent = max(0, min(100, percent))
        now = time.monotonic()
        if self.tty:
            width = 24
            filled = width * percent // 100
            bar = "=" * filled + ">" * (filled < width) + " " * (width - filled - (filled < width))
            suffix = f" {detail}" if detail else ""
            sys.stderr.write(f"\r{self.label:<18} [{bar}] {percent:3d}%{suffix}\x1b[K")
            if percent == 100:
                sys.stderr.write("\n")
            sys.stderr.flush()
        elif now - self.last_update >= 1.0 or percent == 100:
            suffix = f": {detail}" if detail else ""
            print(f"{self.label}: {percent}%{suffix}", file=sys.stderr)
        self.last_update = now


def host_platform() -> str:
    return {"Darwin": "macos", "Windows": "windows"}.get(platform.system(), "linux")


def host_arch() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)


def _download(url: str, target: Path, progress: InstallProgress | None = None, start: int = 0, span: int = 45) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "chom/0.1"})
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=45) as response, target.open("wb") as output:
            expected_size = int(response.headers.get("Content-Length", "0") or 0)
            if progress:
                progress.update(start, "downloading")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    raise InstallError("download exceeds 512 MiB")
                digest.update(chunk)
                output.write(chunk)
                if progress and expected_size:
                    current = start + min(span, int(total / expected_size * span))
                    progress.update(current, f"{total // (1024 * 1024)} MiB")
    except InstallError:
        raise
    except Exception as exc:
        raise InstallError(f"download failed: {exc}") from exc
    return digest.hexdigest()


def _safe_target(root: Path, member_name: str, strip_components: int) -> Path | None:
    member = PurePosixPath(member_name)
    if member.is_absolute() or ".." in member.parts:
        raise InstallError(f"unsafe archive path: {member_name}")
    parts = member.parts[strip_components:]
    if not parts:
        return None
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise InstallError(f"unsafe archive path: {member_name}")
    return target


def _extract(archive: Path, destination: Path, kind: str, strip_components: int, progress: InstallProgress | None = None) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if kind == "auto":
        name = archive.name.lower()
        if name.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar")):
            kind = "tar"
        elif name.endswith(".zip"):
            kind = "zip"
        else:
            kind = "none"
    if kind == "none":
        shutil.copy2(archive, destination / archive.name)
        return
    if kind == "dmg":
        if platform.system() != "Darwin":
            raise InstallError("DMG archives can only be extracted on macOS")
        with tempfile.TemporaryDirectory(prefix="chom-dmg-") as mount_name:
            mountpoint = Path(mount_name) / "volume"
            mountpoint.mkdir()
            attached = False
            try:
                subprocess.run(
                    ["hdiutil", "attach", "-quiet", "-nobrowse", "-readonly", "-mountpoint", str(mountpoint), str(archive)],
                    check=True,
                )
                attached = True
                apps = sorted(mountpoint.glob("*.app"))
                if not apps:
                    raise InstallError("DMG does not contain an application bundle")
                for app in apps:
                    shutil.copytree(app, destination / app.name, symlinks=True)
            except subprocess.CalledProcessError as exc:
                raise InstallError(f"could not mount DMG archive: {exc}") from exc
            finally:
                if attached:
                    subprocess.run(["hdiutil", "detach", "-quiet", str(mountpoint)], check=False)
        return
    extracted_size = 0
    if kind in {"tar", "tar.gz", "tar.xz", "tar.bz2"}:
        try:
            source = tarfile.open(archive, "r:*")
        except tarfile.TarError as exc:
            raise InstallError(f"invalid tar archive: {exc}") from exc
        with source:
            members = source.getmembers()
            files = sum(item.isfile() for item in members)
            completed = 0
            for item in members:
                target = _safe_target(destination, item.name, strip_components)
                if target is None:
                    continue
                if item.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif item.isfile():
                    extracted_size += item.size
                    if extracted_size > 1024 * 1024 * 1024:
                        raise InstallError("archive expands beyond 1 GiB")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stream = source.extractfile(item)
                    if stream is None:
                        raise InstallError(f"cannot read archive entry: {item.name}")
                    with stream, target.open("wb") as output:
                        shutil.copyfileobj(stream, output)
                    target.chmod(0o755 if item.mode & 0o111 else 0o644)
                    completed += 1
                    if progress and files:
                        progress.update(50 + int(completed / files * 20), f"extracting {completed}/{files}")
                else:
                    raise InstallError(f"archive links and special files are not supported: {item.name}")
        return
    if kind == "zip":
        try:
            source = zipfile.ZipFile(archive)
        except zipfile.BadZipFile as exc:
            raise InstallError("invalid ZIP archive") from exc
        with source:
            members = source.infolist()
            files = sum(not item.is_dir() for item in members)
            completed = 0
            for item in members:
                target = _safe_target(destination, item.filename, strip_components)
                if target is None:
                    continue
                mode = item.external_attr >> 16
                if mode and (mode & 0o170000) == 0o120000:
                    raise InstallError(f"archive links are not supported: {item.filename}")
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                extracted_size += item.file_size
                if extracted_size > 1024 * 1024 * 1024:
                    raise InstallError("archive expands beyond 1 GiB")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as input_file, target.open("wb") as output:
                    shutil.copyfileobj(input_file, output)
                target.chmod(0o755 if mode & 0o111 else 0o644)
                completed += 1
                if progress and files:
                    progress.update(50 + int(completed / files * 20), f"extracting {completed}/{files}")
        return
    raise InstallError(f"unsupported archive type: {kind}")


def _create_command(command: str, package_root: Path, script: Path, python: Path, bin_root: Path) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", command):
        raise InstallError("install.command may contain only letters, numbers, '.', '_', '+', and '-'")
    bin_root.mkdir(parents=True, exist_ok=True)
    launcher = bin_root / (command + (".cmd" if os.name == "nt" else ""))
    if os.name == "nt":
        contents = f'@echo off\r\n"{python}" "{script}" %*\r\n'
    else:
        contents = f"#!/bin/sh\nexec {shlex.quote(str(python))} {shlex.quote(str(script))} \"$@\"\n"
    launcher.write_text(contents, encoding="utf-8")
    if os.name != "nt":
        launcher.chmod(0o755)
    return launcher


def install_formula(formula: dict, *, yes: bool = False) -> Path:
    package_name = formula["name"]
    version = formula["version"]
    current_platform = host_platform()
    current_arch = host_arch()
    if current_platform not in formula.get("platforms", [current_platform]):
        raise InstallError(f"{package_name} does not support {current_platform}")
    platform_arches = formula.get("platform", {}).get(current_platform, {}).get("architectures")
    allowed_arches = platform_arches or formula.get("architectures", [current_arch])
    if current_arch not in allowed_arches:
        raise InstallError(f"{package_name} does not support {current_arch} on {current_platform}")
    source = formula["source"]
    build = formula["build"]
    install = formula.get("install", {})
    system = build["system"]
    root = Path.home() / ".local" / "share" / "chom" / "packages" / package_name / version
    bin_root = Path.home() / ".local" / "bin"
    command = install.get("command")
    if root.exists():
        raise InstallError(f"{package_name} {version} is already installed at {root}")
    if command:
        launcher = bin_root / (command + (".cmd" if os.name == "nt" else ""))
        if launcher.exists():
            raise InstallError(f"command already exists: {launcher}")
    binary_targets = [
        (relative, bin_root / Path(relative).name)
        for relative in install.get("binaries", [])
    ]
    if len({target.name for _, target in binary_targets}) != len(binary_targets):
        raise InstallError("binary names must be unique")
    for _, target in binary_targets:
        if target.exists():
            raise InstallError(f"command already exists: {target}")
    launcher_targets = [
        (name, relative, bin_root / (name + (".cmd" if os.name == "nt" else "")))
        for name, relative in install.get("launchers", {}).items()
    ]
    if len({target.name.casefold() for _, _, target in launcher_targets}) != len(launcher_targets):
        raise InstallError("launcher names must be unique")
    for _, _, target in launcher_targets:
        if target.exists():
            raise InstallError(f"command already exists: {target}")

    print(f"Installing {package_name} {version}")
    print(f"  platform:     {current_platform}/{current_arch}")
    print(f"  build system: {system}")
    print(f"  source:       {source['url']}")
    print(f"  sha-256:      {source['sha256']}")
    dependencies = formula.get("dependencies", [])
    if dependencies:
        print("  dependencies: " + ", ".join(dependencies))
    print("  note: build steps and dependency installers can run third-party code.")
    if not yes and input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
        raise InstallError("installation cancelled")

    root.parent.mkdir(parents=True, exist_ok=True)
    progress = InstallProgress("install")
    progress.update(0, "starting")
    with tempfile.TemporaryDirectory(prefix="chom-") as temp_name:
        temp = Path(temp_name)
        archive = temp / (Path(urlparse(source["url"]).path).name or "source.archive")
        digest = _download(source["url"], archive, progress)
        progress.update(48, "verifying checksum")
        if not hmac.compare_digest(digest.lower(), source["sha256"].lower()):
            raise InstallError(f"SHA-256 mismatch: received {digest}")
        staging = temp / "package"
        progress.update(50, "extracting archive")
        _extract(archive, staging, source.get("archive", "auto"), source.get("strip_components", 0), progress)
        progress.update(70, "preparing package")

        if system == "python":
            script = install.get("script")
            script_path = staging / script
            if not script_path.is_file() or not script_path.resolve().is_relative_to(staging.resolve()):
                raise InstallError(f"install script not found in source: {script}")
            shutil.copytree(staging, root)
            try:
                environment = root / "venv"
                progress.update(74, "creating environment")
                subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
                python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                if dependencies:
                    progress.update(80, "installing dependencies")
                    subprocess.run(
                        [str(python), "-m", "pip", "install", "--disable-pip-version-check", *dependencies],
                        check=True,
                    )
                installed_script = root / script
                progress.update(94, "creating command")
                launcher = _create_command(command, root, installed_script, python, bin_root)
            except Exception:
                shutil.rmtree(root, ignore_errors=True)
                raise
        elif system == "prebuilt":
            progress.update(74, "installing binaries")
            for relative, target in install.get("files", {}).items():
                target_path = (Path.home() / target).resolve()
                if not target_path.is_relative_to(Path.home().resolve()):
                    raise InstallError(f"destination escapes the home directory: {target}")
                if target_path.exists():
                    raise InstallError(f"refusing to overwrite existing file: {target_path}")
            for relative, target in binary_targets:
                candidate = staging / relative
                if not candidate.is_file() or not candidate.resolve().is_relative_to(staging.resolve()):
                    raise InstallError(f"declared binary not found: {relative}")
            for _, relative, _ in launcher_targets:
                candidate = staging / relative
                if not candidate.exists() or not candidate.resolve().is_relative_to(staging.resolve()):
                    raise InstallError(f"launcher target not found: {relative}")
            shutil.copytree(staging, root)
            for relative, target in binary_targets:
                binary = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(binary, target)
                if os.name != "nt":
                    target.chmod(target.stat().st_mode | 0o111)
            for relative, target in install.get("files", {}).items():
                source_path = root / relative
                target_path = Path.home() / target
                if not source_path.is_file() or not source_path.resolve().is_relative_to(root.resolve()):
                    raise InstallError(f"declared file not found: {relative}")
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)
            for _, relative, target in launcher_targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                executable = root / relative
                if os.name == "nt":
                    contents = f'@echo off\r\n"{executable}" %*\r\n'
                elif executable.is_dir() and executable.suffix == ".app":
                    contents = f'#!/bin/sh\nexec /usr/bin/open -a {shlex.quote(str(executable))} --args "$@"\n'
                else:
                    contents = f'#!/bin/sh\nexec {shlex.quote(str(executable))} "$@"\n'
                target.write_text(contents, encoding="utf-8")
                if os.name != "nt":
                    target.chmod(0o755)
            launcher = root
        else:
            raise InstallError(f"installation is not implemented for build system '{system}'")

    owned_paths = []
    if system == "python":
        owned_paths.append(launcher)
    elif system == "prebuilt":
        owned_paths.extend(target for _, target in binary_targets)
        owned_paths.extend(Path.home() / target for target in install.get("files", {}).values())
        owned_paths.extend(target for _, _, target in launcher_targets)
    receipt = {
        "name": package_name,
        "version": version,
        "source": source["url"],
        "platform": current_platform,
        "architecture": current_arch,
        "build_system": system,
        "owned_files": [
            {"path": str(path), "sha256": _file_hash(path)}
            for path in owned_paths if path.is_file()
        ],
    }
    progress.update(94, "writing receipt")
    (root / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    if "config_path" in install:
        config_path = Path.home() / install["config_path"]
        if not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(install["config_content"] + "\n", encoding="utf-8")
    progress.update(100, "complete")
    print(f"Installed {package_name} {version}")
    print(f"  location: {root}")
    if command:
        print(f"Command: {launcher}")
        print(f"Add {bin_root} to PATH if the command is not found.")
    elif binary_targets:
        print("Commands: " + ", ".join(str(target) for _, target in binary_targets))
        print(f"Add {bin_root} to PATH if a command is not found.")
    elif launcher_targets:
        print("Commands: " + ", ".join(str(target) for _, _, target in launcher_targets))
        print(f"Add {bin_root} to PATH if a command is not found.")
    return root


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_installed() -> list[dict]:
    package_root = Path.home() / ".local" / "share" / "chom" / "packages"
    packages = []
    if not package_root.is_dir():
        return packages
    for receipt_path in package_root.glob("*/*/receipt.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("name") == receipt_path.parent.parent.name and receipt.get("version") == receipt_path.parent.name:
                packages.append(receipt)
        except (OSError, json.JSONDecodeError):
            continue
    for package_dir in package_root.iterdir() if package_root.is_dir() else []:
        if not package_dir.is_dir() or package_dir.is_symlink():
            continue
        for version_dir in package_dir.iterdir():
            if version_dir.is_dir() and not version_dir.is_symlink() and not (version_dir / "receipt.json").exists():
                packages.append({"name": package_dir.name, "version": version_dir.name, "legacy": True})
    return sorted(packages, key=lambda item: (item["name"].casefold(), item["version"].casefold()))


def remove_package(name: str, version: str | None = None, *, all_versions: bool = False) -> list[Path]:
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", name):
        raise InstallError("invalid package name")
    package_root = Path.home() / ".local" / "share" / "chom" / "packages"
    name_root = package_root / name
    if not name_root.is_dir():
        raise InstallError(f"package is not installed: {name}")
    versions = sorted(path for path in name_root.iterdir() if path.is_dir() and not path.is_symlink())
    if version:
        versions = [path for path in versions if path.name == version]
    elif not all_versions and len(versions) > 1:
        raise InstallError(f"multiple versions of {name} are installed; specify --version or --all")
    if not versions:
        raise InstallError(f"no matching installed version for {name}")

    removed = []
    home = Path.home().resolve()
    for root in versions:
        if root.resolve().parent != name_root.resolve() or root.is_symlink():
            raise InstallError(f"refusing to remove unsafe package path: {root}")
        receipt_path = root / "receipt.json"
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            receipt = {}
        if not receipt:
            bin_root = home / ".local" / "bin"
            if bin_root.is_dir():
                for candidate in bin_root.iterdir():
                    if candidate.is_file() and not candidate.is_symlink():
                        try:
                            contents = candidate.read_text(encoding="utf-8")
                        except (OSError, UnicodeDecodeError):
                            continue
                        if str(root) in contents and "#!/bin/sh" in contents:
                            candidate.unlink()
        for item in receipt.get("owned_files", []):
            raw_path = item.get("path")
            expected = item.get("sha256")
            if not isinstance(raw_path, str) or not isinstance(expected, str):
                continue
            path = Path(raw_path).expanduser()
            allowed_root = home / ".local" / "bin"
            if not path.is_absolute() or not path.resolve().is_relative_to(allowed_root.resolve()) or path.is_symlink() or not path.is_file():
                continue
            if hmac.compare_digest(_file_hash(path), expected):
                path.unlink()
            else:
                print(f"keeping modified file: {path}", file=sys.stderr)
        shutil.rmtree(root)
        if name_root.is_dir() and not any(name_root.iterdir()):
            name_root.rmdir()
        removed.append(root)
    return removed
