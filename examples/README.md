# Formula examples

The builder examples show compiler selection through build.env and common build options:

- cmake-gcc.cho, cmake-clang.cho, and cmake-msvc.cho show GCC, Clang, and MSVC.
- meson-gcc.cho and autotools-clang.cho show environment settings in other C/C++ build systems.
- cargo.cho, go.cho, npm.cho, maven.cho, and xcodebuild.cho show project-specific options.

The MSVC example assumes CMake is run from a Visual Studio developer environment so the compiler, SDK, and linker are available. The binary example selects separate source archives and executable paths by operating system and CPU architecture. Declared binaries are copied into ~/.local/bin.

All example URLs and SHA-256 values are placeholders. The examples are for formula design only; do not install them. The installer runs the declared build steps for supported native build systems with argument arrays and supports Python applications and prebuilt binary files. Compilers, build tools, and system libraries must already be available; Chom does not install operating-system build dependencies. Formula authors must declare generated binaries or launcher targets under install so Chom can expose and remove them safely.

Prebuilt packages can also use DMG, MSI, and Debian bundle sources. MSI extraction uses Windows Installer administrative extraction on Windows. Debian bundles are unpacked into the Chom package prefix without running package maintainer scripts; system integration packages with absolute symlinks are omitted.
