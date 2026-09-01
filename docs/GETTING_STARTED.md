# Getting started

Everything you need to go from nothing to a working Bin-Tel on your own
machine. Follow it top to bottom; each step says what you should see.

You will type commands into a **terminal**:

* **Windows** — press `Win`, type `powershell`, press Enter.
* **macOS** — press `Cmd+Space`, type `terminal`, press Enter.
* **Linux** — press `Ctrl+Alt+T`.

---

## Step 1 — Install Python

Bin-Tel needs **Python 3.12 or newer**. Check whether you already have it:

```
python3 --version
```

*(On Windows type `python --version` instead.)*

If it prints `Python 3.12.x` or higher, skip to Step 2. Otherwise:

* **Windows** — download from [python.org/downloads](https://www.python.org/downloads/).
  On the first screen of the installer, **tick "Add python.exe to PATH"**
  before clicking Install. This matters; without it the commands below will
  not be found.
* **macOS** — download from [python.org/downloads](https://www.python.org/downloads/)
  and run the installer, or `brew install python@3.12` if you use Homebrew.
* **Linux** — `sudo apt install python3.12 python3.12-venv python3-pip`
  (Debian/Ubuntu), or your distribution's equivalent.

Close and reopen the terminal, then check the version again.

---

## Step 2 — Download Bin-Tel

```
git clone https://github.com/G33l0/Bin-Tel.git
cd Bin-Tel
```

No `git`? Download the ZIP from the repository page, unzip it, then `cd` into
the unzipped folder.

**Everything from here on is typed inside this folder.** If you close the
terminal and come back later, `cd` into it again first.

---

## Step 3 — Make a private space for the parts it needs

This keeps Bin-Tel's components separate from the rest of your machine, so
nothing else can break it and it cannot break anything else.

**Windows**

```
python -m venv .venv
.venv\Scripts\activate
```

**macOS / Linux**

```
python3 -m venv .venv
source .venv/bin/activate
```

Your prompt should now start with `(.venv)`. That is how you know it worked.

> Every time you open a new terminal to use Bin-Tel, run the `activate` line
> again first. It is the one step people forget.

---

## Step 4 — Install the parts

```
pip install -r requirements.txt
```

Takes under a minute. A wall of text scrolling past is normal. When it stops
with no red `ERROR` lines, it worked.

*Linux only:* if the app later refuses to start with an error mentioning
`libGL` or `xcb`, install the graphics libraries Qt needs:

```
sudo apt install libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0
```

---

## Step 5 — Put your BINs in the list

This is the important one. **Bin-Tel starts with no data.** Its database is
built from one file that you fill in:

```
data/bin-list.csv
```

Open it in any text editor (Notepad, TextEdit, VS Code — anything). You will
see some `#` comment lines, and at the very bottom a line that reads:

```
bin,bank
```

Add one line underneath for each BIN you have — the BIN, a comma, the bank:

```
bin,bank
414720,Chase Bank
542418,Wells Fargo
37828224,American Express
```

That is the whole format. Rules worth knowing:

* A BIN is **6 or 8 digits**. Both work, and an 8-digit BIN stays 8 digits.
* **Never** paste a full card number here. Anything longer than 8 digits is
  rejected on purpose.
* Order does not matter, and spare spaces are fine.
* Lines starting with `#` are ignored, so you can leave yourself notes.

Save the file.

> Want to record more than the bank — the network, the country, a website?
> Add those column names to the `bin,bank` line. The comments at the top of the
> file list every column, and [BIN_LIST.md](BIN_LIST.md) explains them all.

---

## Step 6 — Build the database

```
python -m app.cli rebuild
```

You should see something like:

```
Database 2026.09.1 is live.
  Rows read                  3
  BINs                       3
  Institutions               3
  Filled in from evidence    3 network(s) from the BIN range
```

**If it says "no rows to build from"**, go back to Step 5 — the file still has
only its header.

**If it lists skipped rows**, it tells you the line number and what was wrong
with each. Fix those lines and run it again.

---

## Step 7 — Check it before opening the app

```
python -m app.cli lookup 414720
```

*(Use one of your own BINs.)* You should get the BIN, the bank, the network
and the rest. If that works, the database is good.

---

## Step 8 — Start Bin-Tel

```
python -m app.main
```

The window opens. Press `Ctrl+2` for BIN Lookup, type a BIN, press Enter.

That is it — you are running.

---

## Coming back later

Two lines, every time:

```
cd Bin-Tel
source .venv/bin/activate     # Windows: .venv\Scripts\activate
python -m app.main
```

## Adding more BINs later

1. Open `data/bin-list.csv` and add your new lines.
2. Run `python -m app.cli rebuild` — **or** in the app, go to **Database** and
   click **Rebuild from BIN list**.

The new data is live immediately. The previous database is kept, so if you
make a mess:

```
python -m app.cli rollback
```

puts it back exactly as it was. Running `rollback` again returns you to the
newer one — neither copy is thrown away.

---

## When something goes wrong

| What you see | What it means |
|---|---|
| `python: command not found` | Python is not installed, or not on PATH. Redo Step 1 — on Windows, tick "Add python.exe to PATH". |
| `No module named app` | You are in the wrong folder. `cd` into the `Bin-Tel` folder. |
| `No module named PyQt6` | The `.venv` is not active. Run the `activate` line from Step 3. |
| `The BIN list has a header but no rows to build from` | The list is still empty. Redo Step 5. |
| `The BIN list has column(s) Bin-Tel does not recognise` | A column name on the header line is misspelled. It names the one it did not recognise. |
| `No database at …` | You have not built one yet. Run `python -m app.cli rebuild`. |
| Rows "skipped" after a rebuild | Those lines could not be read. It gives the line number and reason for each. |
| The app opens but every lookup is empty | The database was built from an empty or wrong list. Run `python -m app.cli check-list` to see what it can read. |

Useful checks:

```
python -m app.cli check-list     # read the list, report on it, change nothing
python -m app.cli stats          # what is actually in the database
python -m app.cli verify-db      # check the database is intact
```

---

## Good to know

* **Nothing leaves your machine.** No account, no sign-in, no telemetry. Bin-Tel
  works with the network cable unplugged.
* **Your list is the truth.** The database is built from it and nothing else,
  so it can never drift away from what you wrote.
* **It says "Unknown" rather than guessing.** A BIN not in your list comes back
  unknown; it never borrows a nearby BIN's bank.
