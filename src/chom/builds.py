"""Known build tool profiles accepted by .cho formulas."""

from __future__ import annotations

BUILD_SYSTEMS = {
    "prebuilt": "Use an upstream binary archive without a build step.",
    "python": "Install a Python command-line application with isolated dependencies.",
    "cmake": "CMake projects and CMake presets.",
    "meson": "Meson projects, commonly built with Ninja.",
    "autotools": "GNU Autoconf and Automake projects.",
    "make": "Makefile projects.",
    "ninja": "Ninja build files.",
    "cargo": "Rust packages and workspaces.",
    "go": "Go modules.",
    "npm": "Node.js projects using npm.",
    "pnpm": "Node.js projects using pnpm.",
    "yarn": "Node.js projects using Yarn.",
    "bun": "Node.js projects using Bun.",
    "pip": "Python packages built with pip.",
    "poetry": "Python projects managed by Poetry.",
    "uv": "Python projects managed by uv.",
    "hatch": "Python projects managed by Hatch.",
    "dotnet": ".NET projects and solutions.",
    "maven": "Java projects using Maven.",
    "gradle": "Java and Kotlin projects using Gradle.",
    "xcodebuild": "Apple platform projects built with Xcode.",
}
