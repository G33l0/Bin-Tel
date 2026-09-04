# Building an installer

Turning a checkout into something a person can install, per platform.

```bash
pip install -r requirements-dev.txt   # brings in PyInstaller
python scripts/build_installer.py
```

That builds the application bundle and then wraps it:

| Platform | Result | Needs |
|---|---|---|
| Windows | `dist/installer/Bin-Tel-Setup-1.0.0.exe` | [Inno Setup 6](https://jrsoftware.org/isdl.php) |
| macOS | `dist/installer/Bin-Tel-1.0.0.dmg` | `hdiutil` (built in) |
| Linux | `dist/installer/bin-tel-1.0.0-linux-x86_64.tar.gz` | nothing extra |

**PyInstaller does not cross-compile.** Each installer must be built on the
platform it targets — a Windows `.exe` has to come from a Windows machine. Ask
for one elsewhere and the script says so rather than producing something that
will not run.

---

## Windows

1. Install Python 3.12+ and [Inno Setup 6](https://jrsoftware.org/isdl.php).
2. From a checkout:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python scripts\build_installer.py
```

You get `dist\installer\Bin-Tel-Setup-1.0.0.exe`.

The installer is **per-user by default**: it installs into Local AppData,
raises no UAC prompt and needs no administrator. Someone who wants it
machine-wide can run it as an administrator and Inno offers the choice.

Two steps if you prefer them separate:

```
python scripts\build_windows.py          → dist\Bin-Tel\Bin-Tel.exe
iscc packaging\windows\bintel.iss        → dist\installer\Bin-Tel-Setup-1.0.0.exe
```

### Code signing

The installer is unsigned, so SmartScreen will warn on first run. To sign it,
add these to `[Setup]` in `packaging/windows/bintel.iss`:

```
SignTool=signtool
SignedUninstaller=yes
```

and register the tool with Inno (Tools → Configure Sign Tools), pointing at
your certificate. Signing is a real certificate purchase; without one the
warning is unavoidable and expected for a self-built application.

---

## macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/build_installer.py
```

You get `dist/installer/Bin-Tel-1.0.0.dmg`, containing the `.app` and a
shortcut to Applications — the usual drag-to-install layout.

Gatekeeper will refuse an unsigned application on first launch. Either
right-click → Open the first time, or sign and notarise it:

```bash
codesign --deep --force --options runtime \
    --sign "Developer ID Application: Your Name (TEAMID)" dist/Bin-Tel.app
xcrun notarytool submit dist/installer/Bin-Tel-1.0.0.dmg \
    --apple-id you@example.com --team-id TEAMID --wait
xcrun stapler staple dist/installer/Bin-Tel-1.0.0.dmg
```

That needs a paid Apple Developer account. Without one the right-click-Open
route works fine for your own machines.

---

## Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/build_installer.py
```

You get `bin-tel-1.0.0-linux-x86_64.tar.gz`. To install it:

```bash
tar xzf bin-tel-1.0.0-linux-x86_64.tar.gz
cd bin-tel-1.0.0
./install.sh
```

It installs into `~/.local` — no root, nothing outside your home directory —
and registers a desktop entry so Bin-Tel appears in your applications menu.
`PREFIX=/opt/bin-tel ./install.sh` puts it somewhere else.

To remove it: `./uninstall.sh`.

Qt needs a few system libraries that some minimal installs lack:

```bash
sudo apt install libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0
```

---

## What ships, and what does not

**Ships:** the application, Qt, branding, icons, theme tokens, and an empty
**BIN list template**.

**Also ships:** the 343,063-row `binlist-data.csv`, **with its CC BY 4.0
licence and attribution**, so an install arrives able to answer something
instead of with an empty database. About 3 MB on the archive.

Removing either the licence or the attribution from `DATA_FILES` breaks the
terms the file is carried under — CC BY permits redistribution *provided the
attribution travels with it*, and an installer is redistribution.

**Does not ship:** any built database, and the sample lists beside the dataset
— those are placeholders for the maintainer's own research and have no
business in someone else's install.

Shipping data takes two halves, and doing one alone ships a file that is never
read. `DATA_FILES` puts it in the bundle; `seed_datasets` copies it out to the
user's data folder on first run. The bundle is read-only, and when frozen it
may be a temporary directory that disappears on exit, so `list_sources`
deliberately looks beside the *user's* list and nowhere else.

The template matters more than it looks. A packaged application unpacks its
data files into a temporary directory that is deleted on exit, so the shipped
copy can never be the file you edit. On first run the application seeds a
**writable** copy in your data directory from that template
(`app.services.bin_list.seed_bin_list`), which is what you then edit:

| Platform | Your BIN list |
|---|---|
| Windows | `%LOCALAPPDATA%\Bin-Tel\bin-list.csv` |
| macOS | `~/Library/Application Support/Bin-Tel/bin-list.csv` |
| Linux | `~/.local/share/bin-tel/bin-list.csv` |

---

## Uninstalling keeps your data

On every platform, removing Bin-Tel leaves your BIN list, database, saved
searches and watchlists where they are. That is deliberate: the list is the
source of truth for the database, and an uninstall that silently deleted a list
somebody spent months curating would be indefensible.

The Windows uninstaller *offers* to remove them, defaulting to **No**. The
Linux `uninstall.sh` prints the two folders and leaves them alone.

---

## Size

About 190 MB installed, 76 MB compressed. Almost all of it is Qt. The build
already excludes the Qt modules Bin-Tel does not use — WebEngine, Quick, QML,
Multimedia, 3D and the rest — along with tkinter and the scientific stack; see
`EXCLUDES` in `scripts/build_common.py`.

`--onefile` produces a single executable instead of a folder. It is tidier to
hand around, but it unpacks to a temporary directory on every launch, so
startup is noticeably slower. The default `--onedir` is the better trade for an
application you open regularly.

---

## Verifying a build

Before shipping one, check the bundle actually runs — a bundle that imports
cleanly on your machine can still miss a hidden import:

```bash
./dist/Bin-Tel/Bin-Tel --version          # Linux/macOS
dist\Bin-Tel\Bin-Tel.exe --version        # Windows
```

Then launch it with a scratch data directory, so you see the true first-run
experience rather than your own existing setup:

```bash
BINTEL_DATA_DIR=/tmp/bintel-check ./dist/Bin-Tel/Bin-Tel
```

You should get the welcome screen, and afterwards `/tmp/bintel-check/` should
hold a writable `bin-list.csv` **and** a `bin-lists/` folder with the dataset,
its licence and its attribution in it. The welcome screen should report about
343,000 rows and say roughly how long building them takes — a build that size
runs for minutes, and a window that says nothing about it looks hung.
