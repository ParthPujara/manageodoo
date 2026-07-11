# manageodoo

Run and manage multiple Odoo development environments from one CLI.

Odoo development means juggling several source trees, each on a different
version and branch, with a matching community/enterprise pair, a specific
Python/venv, a hand-assembled `--addons-path`, unique ports, and a pile of
`odoo-bin` flags. `manageodoo` turns each install into a **named environment**
so launching Odoo becomes `manageodoo run <name>` instead of a memorized command
line.

## What it does

- **Detects** an install by scanning its layout — finds `odoo-bin`, the venv,
  the enterprise dir, custom addons, the git branch, and the **real version**
  read from `odoo/release.py` (folder names often lie about the version).
- **Registers** each install as a named environment in a small TOML registry
  (`~/.config/manageodoo/config.toml`).
- **Runs** any environment with the right interpreter, a generated per-env
  config file, the correct `--addons-path` (enterprise before community), and an
  auto-allocated, collision-free port pair.

It never imports Odoo (it shells out to the environment's own Python), and it
never touches your global `~/.odoorc` — each environment gets its own generated
`-c` config file instead.

## Installation

```bash
pip install manageodoo
```

Or from a local checkout:

```bash
pip install -e .
```

## Quick start

```bash
# Detect an install and save it as an environment
manageodoo detect /home/odoo/odoo18 --name o18 --register

# See what's registered
manageodoo list

# Inspect one, including the exact command it will run
manageodoo show o18

# Launch it
manageodoo run o18

# Install a module into a fresh database, non-interactively
manageodoo run o18 -d demo18 -i sale --stop-after-init
```

## Commands

Run `manageodoo <command> --help` for the full option list of any command.

### `detect PATH`

Scan `PATH` and print the resolved Odoo layout (community root, odoo-bin,
python, enterprise, custom addons, version, branch). Add `--register` to save it.

```
Usage: manageodoo detect [OPTIONS] PATH

Options:
  --name TEXT        Register under this name (default: folder name).
  --register         Persist the detected environment.
  --with-enterprise  Include the enterprise repo (off by default: community only).
  --json             Machine-readable output.
```

### `add NAME --root PATH`

Register an environment, auto-detecting from `--root` and applying explicit
overrides (useful when detection is ambiguous — e.g. a venv it can't locate).

**Enterprise is opt-in.** By default only the community repo is registered
(avoids community/enterprise branch-drift headaches). Add enterprise with
`--with-enterprise` (use the auto-detected dir) or `--enterprise PATH` (explicit).
Worktrees then follow the registry: an env with enterprise worktrees both repos,
one without does community only.

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

### `edit NAME`

Change a registered environment's configuration after the fact and regenerate
its `.conf`. Only the options you pass are changed; everything else stays. This
is how you permanently change a port, the venv, dev mode, demo data, or the
addons — as opposed to `run`'s flags, which only affect a single launch.

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

### `list`

List registered environments as a table (name, version, branch, db, ports,
enterprise).

```
Usage: manageodoo list [OPTIONS]

Options:
  --json  Machine-readable output.
```

### `show NAME`

Show an environment's full config, its assembled `--addons-path`, and the exact
command line it would run. See [The generated `.conf` file](#the-generated-conf-file)
below for what the `-c` file in that command holds.

```
Usage: manageodoo show [OPTIONS] NAME

Options:
  --json  Machine-readable output.
```

### `run NAME`

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
  --stop-after-init        Init then exit.
  --log-level TEXT         Odoo log level.
```

### `rm NAME`

Remove an environment. **By default it also drops the environment's PostgreSQL
database** (the `dropdb <db>` you'd otherwise run by hand) and its filestore,
after a confirmation prompt. The database dropped is the env's `database` field
(defaults to the env name). Irreversible — guard it with `--keep-db` when you
only want to declutter the registry.

If the env is a **managed worktree**, `rm` also removes its git worktrees (so it
is never left orphaned) — the same teardown as `worktree rm`.

```
Usage: manageodoo rm [OPTIONS] NAME

Options:
  --keep-conf            Leave the generated .conf on disk.
  --drop-db / --keep-db  Drop / keep the PostgreSQL database (default: drop, with a prompt).
  -y, --yes              Skip the destructive confirmation prompt.
  --force-drop           Terminate active connections when dropping (dropdb --force).
  --force                For worktree envs: remove even if a worktree is dirty.
```

## Common tasks

Two ways to set things: **per launch** with `run` flags (temporary, the `.conf`
is untouched) or **permanently** with `edit` (rewrites the `.conf`).

### Change the port

Each env gets its own auto-allocated `(http, gevent)` pair so several can run at
once. Check it with `manageodoo show <name>`, and open **that** port in the
browser (not always 8069).

```bash
manageodoo run o18 -p 9100          # just this run
manageodoo edit o18 --http-port 9100   # permanent (gevent recomputed to 9103)
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

New databases are created **without** demo data by default. Turn it on when you
want a populated playground.

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

Extra addons dirs beyond community/enterprise (custom modules, `design-themes`)
are part of the environment, so those modules are installable by name:

```bash
manageodoo edit o18 --addons /path/to/custom_addons,/path/to/design-themes
```

## Parallel branches with worktrees

The point of `worktree` is to work on several Odoo branches at once **without
stashing or committing** to switch. Each branch gets its own working directory
(a git worktree) sharing the origin repo's history, so switching is just picking
a different environment — your uncommitted changes on each branch stay put.

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
```

Worktrees are created under `<parent>/<name>/{community,enterprise}`. The parent
defaults to `manageodoo/worktrees` **inside the source env's install root** (beside
the community/enterprise repos, not in them), e.g.
`/home/odoo/odoo18/manageodoo/worktrees/…` for an install at `/home/odoo/odoo18`.
Change it per-command with `--path`, or globally with
`manageodoo config set worktrees_dir …` (see [Global defaults](#global-defaults)).
If the branch already exists (locally or on a remote) it is checked out; otherwise
it is created off the source env's current HEAD (or `--start-point`). The source
env's Python/venv, dev/demo settings, and custom addons are reused.

**Pick which repos** with `--repos`. The default `both` follows the source env:
it worktrees enterprise only if that env was registered with enterprise — so a
community-only env just gets a community worktree, no drift.
- `both` (default) — community + enterprise *if the env has enterprise*.
- `community` — community only (runnable on its own).
- `enterprise` — enterprise only; the source env's community is **reused** so it
  still runs. Only the enterprise repo gets a new branch.

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

The operation is **transactional**: if the enterprise worktree can't be created,
the community one is rolled back so the two repos never drift apart.

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

## The generated `.conf` file

The `-c …/envs/<name>.conf` you see in `manageodoo show` is a standard Odoo
config file that manageodoo generates for the environment:

```ini
[options]
addons_path = /home/odoo/odoo18/enterprise,/home/odoo/odoo18/design-themes,/home/odoo/odoo18/community/addons
data_dir = /home/odoo/.local/share/Odoo
http_port = 8069
gevent_port = 8072
db_filter = ^o18$
```

- It holds the **stable identity** of the environment: the long addons-path, the
  data-dir, the ports, and a `db_filter` that isolates this env in a shared
  database pool.
- `odoo-bin` reads it via `-c`; your `run`/CLI flags then override it
  (precedence: **CLI > conf > defaults**). So `run o18 -p 9100` changes the port
  for one launch without editing the file.
- It is **generated, not hand-edited** — every `add`/`edit`/`run` rewrites it
  from the registry. Change settings with `manageodoo edit`, not by editing this
  file (manual edits get overwritten). The source of truth is `config.toml`; the
  `.conf` is the artifact Odoo actually consumes.
- Your global `~/.odoorc` is never touched.

## Global defaults

`manageodoo config` shows and sets the defaults new environments inherit — port
base/stride, `dev`, `demo`, `data_dir`, and the worktree parent dir.

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

- `install` / `update` / `shell` / `test` / `db` convenience commands.
- `doctor` — flag port conflicts, community/enterprise branch drift, and stale
  paths.
