# manageodoo

Run and manage multiple Odoo development environments from one CLI.

```bash
pip install manageodoo
manageodoo detect ~/odoo18 --name o18 --register
manageodoo run o18
```

Odoo development means juggling several source trees, each on a different
version and branch, with a matching community/enterprise pair, a specific
Python/venv, a hand-assembled `--addons-path`, unique ports, and a pile of
`odoo-bin` flags. `manageodoo` turns each install into a **named environment**
so launching Odoo becomes `manageodoo run <name>` instead of a memorized
command line.

## Table of contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Command reference](#command-reference)
  - [Set up environments](#set-up-environments) — `detect`, `add`, `edit`
  - [Inspect](#inspect) — `list`, `show`
  - [Run](#run) — `run`
  - [Clean up](#clean-up) — `rm`, `clear`
- [Common tasks](#common-tasks)
- [Parallel branches with worktrees](#parallel-branches-with-worktrees)
- [The generated `.conf` file](#the-generated-conf-file)
- [Global defaults](#global-defaults)
- [How it stores things](#how-it-stores-things)
- [Roadmap](#roadmap)

## Features

- **Auto-detection** — point it at a folder and it finds `odoo-bin`, the venv,
  the enterprise dir, extra addons dirs, the git branch, and the **real
  version** read from `odoo/release.py` (folder names often lie).
- **Named environments** — each install is registered once in a small TOML
  registry, then launched by name with the right interpreter, config, and
  addons-path every time.
- **Automatic port allocation** — every environment gets its own collision-free
  `(http, gevent)` port pair, so several Odoo instances run side by side.
- **Generated per-env config** — a standard Odoo `.conf` per environment;
  your global `~/.odoorc` is never touched.
- **Opt-in enterprise & custom addons** — community-only by default; include
  enterprise with `--with-enterprise` and extra addons dirs with `--addons`.
- **One-flag test runs** — `run NAME --test test_foo` wires up
  `--test-enable`/`--test-tags` for you.
- **Parallel branches via git worktrees** — `worktree add BRANCH --from ENV`
  gives every branch its own working directory, port, and database. Community
  and enterprise move **in lockstep** on matching branches; no stashing or
  committing to switch.
- **Safe teardown** — `rm`/`clear` remove the environment, its database,
  filestore, generated config, and (for worktree envs) the git worktrees, with
  clear confirmation prompts.
- **Scriptable** — `--json` output on `detect`, `list`, and `show`.

It never imports Odoo (it shells out to each environment's own Python), so
environments on different Odoo and Python versions coexist happily.

## Requirements

- Python **3.9+**
- Linux/macOS with `git` and PostgreSQL client tools (`psql`, `dropdb`) on
  `PATH`
- One or more local Odoo source checkouts (each with its own venv, ideally)

## Installation

```bash
pip install manageodoo
```

To work on manageodoo itself, install from a local checkout in editable mode:

```bash
git clone https://github.com/ParthPujara/manageodoo
cd manageodoo
pip install -e .
```

Check it worked:

```bash
manageodoo --version
manageodoo --help
```

`--help` groups the commands by task (set up, inspect, run, clean up,
worktrees, defaults); `manageodoo COMMAND --help` shows every option of a
command.

## Quick start

```bash
# 1. Detect an install and save it as an environment
manageodoo detect /home/odoo/odoo18 --name o18 --register

# 2. See what's registered
manageodoo list

# 3. Inspect one, including the exact command it will run
manageodoo show o18

# 4. Launch it (own port, own config — check `show` for the port)
manageodoo run o18

# 5. Install a module into a fresh database, non-interactively
manageodoo run o18 -d demo18 -i sale --stop-after-init
```

## Command reference

Run `manageodoo <command> --help` for the full option list of any command.

### Set up environments

#### `detect PATH`

Scan `PATH` and print the resolved Odoo layout (community root, odoo-bin,
python, enterprise, custom addons, version, branch). Add `--register` to save
it as an environment.

```
Usage: manageodoo detect [OPTIONS] PATH

Options:
  --name TEXT        Register under this name (default: folder name).
  --register         Persist the detected environment.
  --with-enterprise  Include the enterprise repo (off by default: community only).
  --json             Machine-readable output.
```

#### `add NAME --root PATH`

Register an environment, auto-detecting from `--root` and applying explicit
overrides (useful when detection is ambiguous — e.g. a venv it can't locate).

**Enterprise and custom addons are opt-in.** By default only the community
repo is registered (avoids community/enterprise branch-drift headaches, and
you decide what belongs in your addons-path). Include enterprise with
`--with-enterprise` (auto-detected dir) or `--enterprise PATH` (explicit), and
extra addons dirs with `--addons`. When detection finds dirs you didn't
include, it prints a hint so nothing is silently lost.

```
Usage: manageodoo add [OPTIONS] NAME

Options:
  --root TEXT          Container path of the Odoo install.  [required]
  --odoo-bin TEXT      Override the odoo-bin path.
  --python TEXT        Override the interpreter path.
  --enterprise TEXT    Include this enterprise addons dir (opt-in).
  --with-enterprise    Include the auto-detected enterprise repo (off by default).
  --addons TEXT        Extra addons dir (comma-separated or repeatable).
  --db TEXT            Default database (default: NAME).
  --http-port INTEGER  Fixed http port (default: auto-allocate).
```

#### `edit NAME`

Change a registered environment's configuration after the fact and regenerate
its `.conf`. Only the options you pass are changed; everything else stays.
This is how you permanently change a port, the venv, dev mode, demo data, or
the addons — as opposed to `run`'s flags, which only affect a single launch.

```
Usage: manageodoo edit [OPTIONS] NAME

Options:
  --rename TEXT          Rename the environment (also renames its conf).
  --python TEXT          Set the interpreter path.
  --odoo-bin TEXT        Set the odoo-bin path.
  --enterprise TEXT      Set the enterprise addons dir.
  --addons TEXT          Replace custom addons dirs (comma-separated or repeatable).
  --db TEXT              Set the default database (updates db_filter too).
  --http-port INTEGER    Set http port (gevent recomputed).
  --gevent-port INTEGER  Set the gevent port explicitly.
  --data-dir TEXT        Set the Odoo data-dir.
  --dev TEXT             Set the --dev features (empty string clears).
  --demo / --no-demo     Set demo data on/off.
```

### Inspect

#### `list`

List registered environments as a table: name, version, branch, database,
ports, enterprise, and a `WT` column marking managed worktrees (removing
those also removes their git worktrees — see
[worktrees](#parallel-branches-with-worktrees)).

```
Usage: manageodoo list [OPTIONS]

Options:
  --json  Machine-readable output.
```

#### `show NAME`

Show an environment's full config, its assembled `--addons-path`, and the
exact command line it would run. See
[The generated `.conf` file](#the-generated-conf-file) for what the `-c` file
in that command holds.

```
Usage: manageodoo show [OPTIONS] NAME

Options:
  --json  Machine-readable output.
```

### Run

#### `run NAME`

Launch Odoo for `NAME`. Regenerates the per-env config file, then execs
`<python> <odoo-bin> -c <conf>` with your overrides layered on top (Odoo's own
precedence is CLI > config > defaults). Anything after `--` is passed straight
through to `odoo-bin`.

```
Usage: manageodoo run [OPTIONS] NAME [RAW]...

Options:
  -d, --database TEXT      Database (default: env's).
  -p, --http-port INTEGER  Override http port.
  -i, --init TEXT          Install module(s), comma-separated.
  -u, --update TEXT        Update module(s), comma-separated.
  --dev TEXT               Odoo --dev features (e.g. all).
  --demo / --no-demo       Force demo data on/off.
  --test TEXT              Run test(s): a bare test_ function name (the '.' Odoo
                           needs is added for you) or a full --test-tags spec.
                           Comma-separated or repeatable. Implies --test-enable.
  --stop-after-init        Init then exit.
  --log-level TEXT         Odoo log level.
```

### Clean up

#### `rm NAME`

Remove an environment. **By default it also drops the environment's PostgreSQL
database** (the `dropdb <db>` you'd otherwise run by hand) and its filestore,
after a confirmation prompt. The database dropped is the env's `database`
field (defaults to the env name). Irreversible — guard it with `--keep-db`
when you only want to declutter the registry.

If the env is a **managed worktree** (`WT=yes` in `list`), `rm` warns and also
removes its git worktrees so nothing is left orphaned — the same teardown as
`worktree rm`.

```
Usage: manageodoo rm [OPTIONS] NAME

Options:
  --keep-conf            Leave the generated .conf on disk.
  --drop-db / --keep-db  Drop / keep the PostgreSQL database (default: drop, with a prompt).
  -y, --yes              Skip the destructive confirmation prompt.
  --force-drop           Terminate active connections when dropping (dropdb --force).
  --force                For worktree envs: remove even if a worktree is dirty.
```

#### `clear`

Remove **all** registered environments at once — databases, filestores,
generated configs, and git worktrees included. It lists everything it is about
to remove (worktree envs tagged `[worktree]`) and asks once for confirmation;
a failure on one environment (e.g. a dirty worktree needing `--force`) is
reported but does not stop the rest.

```
Usage: manageodoo clear [OPTIONS]

Options:
  --keep-conf            Leave the generated .conf files on disk.
  --drop-db / --keep-db  Drop / keep each PostgreSQL database (default: drop).
  -y, --yes              Skip the confirmation prompt.
  --force-drop           Terminate active connections when dropping (dropdb --force).
  --force                Remove worktree envs even if a worktree is dirty.
```

## Common tasks

Two ways to set things: **per launch** with `run` flags (temporary, the
`.conf` is untouched) or **permanently** with `edit` (rewrites the `.conf`).

### Change the port

Each env gets its own auto-allocated `(http, gevent)` pair so several can run
at once. Check it with `manageodoo show <name>`, and open **that** port in the
browser (not always 8069).

```bash
manageodoo run o18 -p 9100              # just this run
manageodoo edit o18 --http-port 9100    # permanent (gevent recomputed to 9103)
```

### Developer mode (`--dev`)

`--dev all` enables auto-reload plus qweb/xml dev features — the usual dev
default. New envs get `dev = all`; change it any time.

```bash
manageodoo run o18 --dev all           # this run
manageodoo run o18 --dev reload,qweb   # a subset
manageodoo edit o18 --dev all          # make it the default
manageodoo edit o18 --dev ""           # turn it off permanently
```

### Demo data

New databases are created **without** demo data by default. Turn it on when
you want a populated playground.

```bash
manageodoo run o18 -d play --demo -i sale --stop-after-init  # demo db
manageodoo run o18 --no-demo                                  # force off
manageodoo edit o18 --demo                                    # default on
```

### Virtual environment (Python)

Each env runs under its own interpreter — that's how different Odoo/Python
versions coexist. If detection can't find the venv (e.g. it lives in a nested
copy), point at it explicitly:

```bash
manageodoo add o19 --root /home/odoo/odoo \
  --python /home/odoo/odoo/community/venv/bin/python
manageodoo edit o18 --python /path/to/venv/bin/python   # change it later
```

If no venv is found, `run` falls back to `python3`, which must have Odoo's
dependencies installed.

### Install / update modules

`-i` installs, `-u` updates. Both take a comma-separated list and need a
database. Pair with `--stop-after-init` to apply changes and exit without
starting the web server.

```bash
manageodoo run o18 -d shop -i sale,stock --stop-after-init   # install
manageodoo run o18 -d shop -u account --stop-after-init      # update
manageodoo run o18 -d shop -i website                         # install, then serve
```

Extra addons dirs beyond community/enterprise (custom modules, design themes)
are part of the environment, so those modules are installable by name:

```bash
manageodoo edit o18 --addons /path/to/custom_addons,/path/to/design-themes
```

### Run tests

`--test` starts Odoo with `--test-enable` and a `--test-tags` spec built from
what you give it. A bare `test_...` function name gets the leading `.` Odoo
expects; a full spec (`[-][tag][/module][:class][.method]`) passes through
untouched.

```bash
# one test function (runs every method with that name in the updated modules)
manageodoo run o18 -u sale --test test_name_search --stop-after-init

# several tests / full specs
manageodoo run o18 -u sale --test test_a,test_b --stop-after-init
manageodoo run o18 -u base --test /base:TestUsers.test_name_search --stop-after-init
```

**Odoo only executes tests for modules being installed or updated in that
session** — always pair `--test` with `-u` (or `-i`); `run` warns if you
forget. Drop `--stop-after-init` to keep the server running after the tests.

## Parallel branches with worktrees

The point of `worktree` is to work on several Odoo branches at once **without
stashing or committing** to switch. Each branch gets its own working directory
(a git worktree) sharing the origin repo's history, so switching is just
picking a different environment — your uncommitted changes on each branch stay
put.

Because Odoo is two repos that must stay on matching branches, `manageodoo`
drives **community and enterprise in lockstep**: it creates a worktree on the
same branch in each, then registers the pair as a new environment with its own
port and database.

```bash
# From an existing env's repos, branch off their current HEAD into a new worktree
manageodoo worktree add 17.0-fix --from o18

# List managed worktrees
manageodoo worktree list

# Run it like any environment (its own port, its own db)
manageodoo run 17.0-fix

# Tear it down: removes both git worktrees, drops its db, unregisters
manageodoo worktree rm 17.0-fix

# Or tear down every managed worktree at once
manageodoo worktree clear
```

Worktrees are created under `<parent>/<name>/{community,enterprise}`. The
parent defaults to `manageodoo/worktrees` **inside the source env's install
root** (beside the community/enterprise repos, not in them), e.g.
`/home/odoo/odoo18/manageodoo/worktrees/…` for an install at
`/home/odoo/odoo18`. Change it per-command with `--path`, or globally with
`manageodoo config set worktrees_dir …` (see [Global defaults](#global-defaults)).
If the branch already exists (locally or on a remote) it is checked out;
otherwise it is created off the source env's current HEAD (or `--start-point`).
The source env's Python/venv, dev/demo settings, and custom addons are reused.

Worktree environments show up in `manageodoo list` with `WT=yes`; removing one
(with `rm`, `worktree rm`, or a clear) also removes its git worktrees, so
delete with care. Removing the last worktree under a parent dir also cleans up
the now-empty scaffolding directories.

**Pick which repos** with `--repos`. The default `both` follows the source env:
it worktrees enterprise only if that env was registered with enterprise — so a
community-only env just gets a community worktree, no drift.

- `both` (default) — community + enterprise *if the env has enterprise*.
- `community` — community only (runnable on its own).
- `enterprise` — enterprise only; the source env's community is **reused** so
  it still runs. Only the enterprise repo gets a new branch.

```bash
manageodoo worktree add 17.0-fix --from o18                     # both
manageodoo worktree add web-poc  --from o18 --repos community   # community only
manageodoo worktree add ent-poc  --from o18 --repos enterprise  # enterprise only
manageodoo worktree add tmp      --from o18 --path /data/wt     # under /data/wt/tmp/
```

### `worktree add BRANCH --from ENV`

```
Usage: manageodoo worktree add [OPTIONS] BRANCH

Options:
  --from TEXT                      Source env whose repos to branch from.  [required]
  --repos [both|community|enterprise]   Which repos to worktree.  [default: both]
  --name TEXT                      Env/worktree name (default: slug of BRANCH).
  --path TEXT                      Custom parent dir; worktree goes in <path>/<name>/.
  --start-point TEXT               Start ref when creating a new branch (default: HEAD).
  --fetch                          Fetch remotes first.
  --http-port INTEGER              Fixed http port (default: auto-allocate).
```

The operation is **transactional**: if the enterprise worktree can't be
created, the community one is rolled back so the two repos never drift apart.

### `worktree list`

```
Usage: manageodoo worktree list [OPTIONS]

Options:
  --from TEXT  Show raw git worktrees for this env's repos instead.
```

### `worktree rm NAME`

Removes **both** git worktrees, drops the database by default (like `rm`), and
unregisters the environment. The branch git created is left in place (as git
itself does on `worktree remove`).

```
Usage: manageodoo worktree rm [OPTIONS] NAME

Options:
  --force                Remove even if a worktree is dirty.
  --keep-conf            Leave the generated .conf on disk.
  --drop-db / --keep-db  Drop / keep the PostgreSQL database (default: drop, with a prompt).
  -y, --yes              Skip the destructive confirmation prompt.
  --force-drop           Terminate active connections when dropping (dropdb --force).
```

### `worktree clear`

Remove **all** managed worktree environments — git worktrees and databases
included. Plain (non-worktree) environments are left untouched. Same
confirmation and failure handling as [`clear`](#clear).

```
Usage: manageodoo worktree clear [OPTIONS]

Options:
  --keep-conf            Leave the generated .conf files on disk.
  --drop-db / --keep-db  Drop / keep each PostgreSQL database (default: drop).
  -y, --yes              Skip the confirmation prompt.
  --force-drop           Terminate active connections when dropping (dropdb --force).
  --force                Remove even if a worktree is dirty.
```

## The generated `.conf` file

The `-c …/envs/<name>.conf` you see in `manageodoo show` is a standard Odoo
config file that manageodoo generates for the environment:

```ini
[options]
addons_path = /home/odoo/odoo18/enterprise,/home/odoo/odoo18/design-themes,/home/odoo/odoo18/community/addons
data_dir = /home/odoo/.local/share/Odoo
http_port = 8069
gevent_port = 8072
dbfilter = ^o18$
```

- It holds the **stable identity** of the environment: the long addons-path,
  the data-dir, the ports, and a `dbfilter` that isolates this env in a shared
  database pool.
- `odoo-bin` reads it via `-c`; your `run`/CLI flags then override it
  (precedence: **CLI > conf > defaults**). So `run o18 -p 9100` changes the
  port for one launch without editing the file.
- It is **generated, not hand-edited** — every `add`/`edit`/`run` rewrites it
  from the registry. Change settings with `manageodoo edit`, not by editing
  this file (manual edits get overwritten). The source of truth is
  `config.toml`; the `.conf` is the artifact Odoo actually consumes.
- Your global `~/.odoorc` is never touched.

## Global defaults

`manageodoo config` shows and sets the defaults new environments inherit —
port base/stride, `dev`, `demo`, `data_dir`, and the worktree parent dir.

```bash
manageodoo config show                              # list effective defaults
manageodoo config set worktrees_dir /data/odoo-wt   # absolute default parent
manageodoo config set worktrees_dir .worktrees      # relative → per-install root
manageodoo config set http_port_base 8100
manageodoo config unset worktrees_dir               # revert to built-in default
```

A **relative** `worktrees_dir` is resolved against each source env's install
root, so `.worktrees` puts each version's worktrees next to its own checkout.

## How it stores things

- Registry (source of truth): `~/.config/manageodoo/config.toml` (honors
  `$XDG_CONFIG_HOME`).
- Generated per-env Odoo config: `~/.config/manageodoo/envs/<name>.conf`.
- Managed worktrees: `<install-root>/manageodoo/worktrees/<name>/` by default
  (configurable via `worktrees_dir`).

## Roadmap

- `install` / `update` / `shell` / `db` convenience commands.
- `doctor` — flag port conflicts, community/enterprise branch drift, and stale
  paths.
- Shell completion and a short `mo` alias.

## License

MIT
