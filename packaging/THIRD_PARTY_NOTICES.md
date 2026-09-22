# Third-party components in QuickAsk binaries

QuickAsk itself is MIT-licensed (see LICENSE). The single-file binaries published on the
Releases page also contain the libraries below, unmodified, as built by the system that
produced each binary: Ubuntu 24.04 (Linux), MSYS2 UCRT64 (Windows), Homebrew (macOS).
The exact set differs slightly between platforms.

Libraries under the LGPL are included as separate, dynamically loaded shared libraries.
QuickAsk's complete source code and build recipe are public: https://github.com/delarun/quickask
(packaging/quickask.spec, .github/workflows/build.yml), so anyone can rebuild the binary with
modified versions of these libraries. Their corresponding source code is available from the
upstream projects below, and from the distribution that built them (Ubuntu source packages,
MSYS2 MINGW-packages, Homebrew formulae). The full license texts follow this list in
THIRD_PARTY_LICENSES.txt.

## LGPL-2.1-or-later

| Component | Source |
|---|---|
| GTK 4 (incl. GDK, GSK) | https://gitlab.gnome.org/GNOME/gtk |
| GLib, GIO, GObject, GModule | https://gitlab.gnome.org/GNOME/glib |
| PyGObject | https://gitlab.gnome.org/GNOME/pygobject |
| Pango | https://gitlab.gnome.org/GNOME/pango |
| GdkPixbuf | https://gitlab.gnome.org/GNOME/gdk-pixbuf |
| dconf GSettings backend (Linux) | https://gitlab.gnome.org/GNOME/dconf |
| FriBidi | https://github.com/fribidi/fribidi |
| libthai, libdatrie | https://github.com/tlwg/libthai, https://github.com/tlwg/libdatrie |
| libblkid, libmount (util-linux, Linux) | https://github.com/util-linux/util-linux |
| cairo (dual LGPL-2.1 / MPL-1.1, used under LGPL-2.1) | https://gitlab.freedesktop.org/cairo/cairo |
| Graphite2 (LGPL-2.1 / MPL-2.0 / GPL-2.0+, used under LGPL-2.1) | https://github.com/silnrsi/graphite |

## LGPL-3.0 / CC-BY-SA-3.0

| Component | Source |
|---|---|
| Adwaita icon theme | https://gitlab.gnome.org/GNOME/adwaita-icon-theme |

## GPL with linking exceptions

These exceptions allow bundling into software under any license.

| Component | License | Source |
|---|---|---|
| PyInstaller bootloader | GPL-2.0-or-later with the PyInstaller bootloader exception | https://github.com/pyinstaller/pyinstaller |
| libgcc, libstdc++ (Linux, Windows) | GPL-3.0 with the GCC Runtime Library Exception | https://gcc.gnu.org |

## GPL-2.0

| Component | License | Source |
|---|---|---|
| JBIG-KIT (libjbig), a dependency of libtiff | GPL-2.0-or-later | https://www.cl.cam.ac.uk/~mgk25/jbigkit/ |
| LZO (liblzo2), a dependency of libcairo-script-interpreter | GPL-2.0-or-later | https://www.oberhumer.com/opensource/lzo/ |

GTK 4 as built by Linux distributions and by MSYS2/Homebrew links against libtiff and cairo,
which in turn pull in these two libraries, so they cannot be left out of a self-contained
binary. The binaries are therefore distributed in compliance with GPL-2.0-or-later as a whole.
QuickAsk's own source code remains MIT-licensed, which is GPL-compatible. The complete
corresponding source is QuickAsk's public repository plus the distribution source packages
named above.

## Permissive

| Component | License |
|---|---|
| Python | PSF-2.0 |
| gtk4-layer-shell (Linux) | MIT |
| Graphene, pixman, HarfBuzz, libepoxy, Fontconfig, libffi, Expat, Brotli, libxkbcommon, X.Org / XCB client libraries | MIT and MIT-style |
| FreeType | FreeType License (FTL) |
| OpenSSL | Apache-2.0 |
| Vulkan loader | Apache-2.0 |
| PCRE2, libwebp, libjpeg-turbo, zstd, libbsd, libmd | BSD-style / IJG |
| zlib, libpng, bzip2, xz (liblzma), libselinux | Zlib / libpng-2.0 / bzip2 / 0BSD / public domain |
