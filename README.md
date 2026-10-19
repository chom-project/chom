# chom

chom is a cross-platform package manager for Linux, macOS, and Windows. It reads package recipes written in `.cho` (TOML) files and installs supported applications and prebuilt software.

## Features

- Validate formulas, including platform and architecture-specific settings.
- Install Python command-line applications and prebuilt binaries, files, and launchers.
- Verify downloaded archives with SHA-256 before installation.
- Track installed files so packages can be removed safely.
- Add and update GitHub-hosted formula repositories.
- Parse and validate recipes for common native build systems. Native source builds are not yet executed by chom.

## Requirements

- Python 3.9 or newer
- Git, to install chom from GitHub

## Install chom

chom is not currently published on PyPI. The commands below install it from this repository with `pipx`, which keeps its Python environment separate from other applications.

Install `pipx` with the package manager for your system, then add its application directory to `PATH`:

### macOS

```sh
brew install pipx
pipx ensurepath
```

### Ubuntu or Debian

```sh
sudo apt update
sudo apt install pipx
pipx ensurepath
```

### Fedora

```sh
sudo dnf install pipx
pipx ensurepath
```

### Windows PowerShell

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
```

Close and reopen the terminal after `pipx ensurepath`. Then install chom on any of these systems:

```sh
pipx install git+https://github.com/chom-project/chom.git
chom --help
```

## Use chom

Install a formula by name from a configured repository, or install directly from a local `.cho` file:

```sh
chom search package-name
chom install package-name
chom install --file path/to/package.cho
```

Inspect and manage packages with:

```sh
chom info path/to/package.cho
chom validate path/to/package.cho
chom list
chom remove package-name
```

An installation asks for confirmation before downloading. Pass `--yes` to skip the prompt in scripts. chom stores package data under `~/.local/share/chom` and writes command launchers to `~/.local/bin` on Linux and macOS. Add `~/.local/bin` to `PATH` to run installed commands by name. On Windows, chom uses the equivalent directory under the user's home folder.

Manage formula repositories with:

```sh
chom repo list
chom repo add https://github.com/OWNER/REPOSITORY
chom repo update
```

chom uses `https://github.com/chom-project/repo` as its default formula repository. A formula must be present in a configured repository before it can be installed by name.

## Formula format

A `.cho` file uses TOML 1.0. Every formula declares a package name, version, license, source URL, SHA-256 digest, and build system. Platform-specific source, build, and install settings can be placed under `[platform.linux]`, `[platform.macos]`, or `[platform.windows]`, with architecture overrides such as `x86_64` and `arm64`.

For example:

```toml
format = 1
name = "sample-tool"
version = "1.0.0"
description = "A sample command-line application."
license = "MIT"
platforms = ["linux", "macos"]

[source]
url = "https://example.org/sample-tool-1.0.0.tar.gz"
sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
archive = "tar.gz"
strip_components = 1

[build]
system = "prebuilt"

[install]
binaries = ["bin/sample-tool"]
```

Calculate a digest for a local archive with `chom hash path/to/archive`. The files in `examples/` show formula structure; their URLs and checksums are placeholders and they are not installable packages.

The formula parser supports profiles for CMake, Meson, Autotools, Make, Ninja, Cargo, Go, npm, pnpm, Yarn, Bun, pip, Poetry, uv, Hatch, .NET, Maven, Gradle, and Xcodebuild. These build settings are validated, but native source build execution is still in progress. The installer currently handles Python applications and prebuilt packages.

## Install from a source checkout

```sh
git clone https://github.com/chom-project/chom.git
cd chom
python3 -m venv .venv
```

Activate the environment and install chom in editable mode:

```sh
# macOS or Linux
source .venv/bin/activate
python -m pip install -e .
```

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
python -m pip install -e .
```
