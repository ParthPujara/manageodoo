"""manageodoo — run and manage multiple Odoo environments from one CLI."""

from __future__ import annotations

import dataclasses
import json as _json
import os
import shlex
from pathlib import Path

import click

from . import config, gitutil, pg, ports
from . import worktree as wt
from .detect import DetectResult, detect
from .env import Environment
from .errors import EnvExists, EnvNotFound, ManageodooError
from .odooconf import write_conf
from .runner import build_argv, run_env


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _get_env(doc: dict, name: str) -> Environment:
    entry = doc.get("env", {}).get(name)
    if entry is None:
        raise EnvNotFound(f"No environment named '{name}'. Try 'manageodoo list'.")
    return Environment.from_dict(name, entry)


def _store_env(doc: dict, env: Environment) -> None:
    doc.setdefault("env", {})[env.name] = env.to_dict()
    config.save(doc)


def _make_env(
    name: str,
    res: DetectResult,
    doc: dict,
    *,
    database: str | None = None,
    http_port: int | None = None,
) -> Environment:
    """Turn a detection result into a registered Environment (ports, db, conf)."""
    defaults = config.defaults(doc)
    if http_port is not None:
        http = http_port
        gevent = ports.gevent_for(http, defaults)
    else:
        http, gevent = ports.allocate(doc, defaults)
    db = database or name
    return Environment(
        name=name,
        root=res.root,
        odoo_bin=res.odoo_bin,
        community=res.community,
        python=res.python,
        enterprise=res.enterprise,
        custom_addons=res.custom_addons,
        version=res.version,
        branch=res.branch,
        enterprise_branch=res.enterprise_branch,
        database=db,
        db_filter=f"^{db}$",
        http_port=http,
        gevent_port=gevent,
        data_dir=defaults["data_dir"],
        dev=defaults["dev"],
        demo=bool(defaults["demo"]),
        conf=str(config.conf_path(name)),
    )


def _resolve_addons(values: tuple[str, ...], root: str) -> list[str]:
    """Turn --addons values into absolute dirs. Each value may itself be
    comma-separated (like Odoo's --addons-path). Relative entries resolve
    against the cwd, then the install root. Missing dirs warn but are kept."""
    out: list[str] = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            p = Path(part).expanduser()
            if not p.is_absolute():
                cwd_c = Path.cwd() / p
                root_c = Path(root) / p
                p = cwd_c if cwd_c.is_dir() else (root_c if root_c.is_dir() else cwd_c)
            p = p.resolve()
            if not p.is_dir():
                click.secho(f"  ! addons dir does not exist: {p}", fg="yellow")
            out.append(str(p))
    return out


def _warn_missing_addons(env: Environment) -> None:
    for p in env.missing_addons():
        click.secho(f"  ! addons dir missing (dropped from --addons-path): {p}",
                    fg="yellow", err=True)


def _apply_enterprise_choice(res: DetectResult, explicit: str | None,
                             with_enterprise: bool) -> str:
    """Enterprise is opt-in at registration. Set res.enterprise to the chosen
    value and return the auto-detected path (for a hint when it was skipped)."""
    detected = res.enterprise
    if explicit:
        res.enterprise = explicit
    elif not with_enterprise:
        res.enterprise = ""
        res.enterprise_branch = ""
    return detected


def _hint_enterprise(env: Environment, detected: str) -> None:
    if not env.enterprise and detected:
        click.secho(
            f"  note: enterprise detected at {detected} but not registered "
            "(community-only by default). Re-add with --with-enterprise to include it.",
            fg="cyan")


def _apply_addons_choice(res: DetectResult, values: tuple[str, ...]) -> list[str]:
    """Custom addons are opt-in at registration, like enterprise: only what the
    user passes via --addons is registered. Returns the auto-detected dirs (for
    a hint when they were skipped)."""
    detected = list(res.custom_addons)
    res.custom_addons = _resolve_addons(values, res.root) if values else []
    return detected


def _hint_addons(env: Environment, detected: list[str]) -> None:
    if detected and not env.custom_addons:
        click.secho(
            f"  note: extra addons dir(s) detected ({', '.join(detected)}) but "
            "not registered. Include with --addons, or later via "
            f"'manageodoo edit {env.name} --addons DIR'.", fg="cyan")


def _maybe_drop_db(env: Environment, *, drop_db: bool | None, yes: bool,
                   force_drop: bool) -> None:
    """Drop the env's PostgreSQL database (and filestore) unless kept.
    drop_db: False = keep, True = drop, None = drop if it exists (default)."""
    if drop_db is False:
        return
    dbname = env.database or env.name
    if not pg.database_exists(dbname):
        if drop_db is None:
            click.echo(f"No PostgreSQL database named '{dbname}' — nothing to drop.")
        return
    if not yes and not click.confirm(
        click.style(
            f"Permanently DROP PostgreSQL database '{dbname}'? This cannot be undone",
            fg="red"),
        default=False,
    ):
        return
    pg.drop_database(dbname, force=force_drop)
    click.secho(f"Dropped database '{dbname}'.", fg="green")
    fs = Path(env.expanded_data_dir()) / "filestore" / dbname
    if fs.is_dir():
        import shutil
        shutil.rmtree(fs, ignore_errors=True)
        click.echo(f"Removed filestore {fs}")


def _teardown_env(doc: dict, env: Environment, *, drop_db: bool | None,
                  yes: bool, force_drop: bool, keep_conf: bool,
                  force: bool) -> None:
    """Fully remove one env: drop its database (unless kept), tear down its git
    worktrees if it is a managed worktree, unregister it, and delete its conf.
    Mutates ``doc`` in place but does NOT save — the caller controls when to
    persist (so a bulk clear can save once)."""
    _maybe_drop_db(env, drop_db=drop_db, yes=yes, force_drop=force_drop)
    if env.managed_worktree:
        wt.remove(env, force=force)
    doc.get("env", {}).pop(env.name, None)
    if not keep_conf:
        p = Path(env.conf).expanduser() if env.conf else config.conf_path(env.name)
        if p.exists():
            p.unlink()


def _print_detect(res: DetectResult, *, addons_are_optin: bool = False) -> None:
    # addons_are_optin: detect output shows found-but-not-registered dirs; show
    # output lists what the user actually registered, so no suffix there.
    addons = ", ".join(res.custom_addons)
    if addons and addons_are_optin:
        addons += "  (opt-in: --addons)"
    rows = [
        ("community", res.community),
        ("odoo-bin", res.odoo_bin),
        ("python", res.python or "(none — falls back to python3)"),
        ("enterprise", res.enterprise or "-"),
        ("custom addons", addons or "-"),
        ("version", res.version or "?"),
        ("branch", res.branch or "-"),
    ]
    if res.enterprise:
        rows.append(("enterprise branch", res.enterprise_branch or "-"))
    width = max(len(k) for k, _ in rows)
    for key, val in rows:
        click.echo(f"  {key.ljust(width)}  {val}")
    for warn in res.warnings:
        click.secho(f"  ! {warn}", fg="yellow")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
class SectionedGroup(click.Group):
    """A Group whose --help lists commands under section headings instead of
    one flat ``Commands:`` block. ``sections`` is an ordered list of
    ``(title, (command names…))``; any command not claimed by a section still
    shows up under 'Other commands', so nothing can silently disappear from
    the help output."""

    def __init__(self, *args, sections=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.sections = tuple(sections)

    def format_commands(self, ctx: click.Context,
                        formatter: click.HelpFormatter) -> None:
        all_names = self.list_commands(ctx)
        if not all_names:
            return
        limit = formatter.width - 6 - max(len(n) for n in all_names)
        claimed = {n for _, names in self.sections for n in names}
        blocks = list(self.sections)
        blocks.append(
            ("Other commands", tuple(n for n in all_names if n not in claimed)))
        for title, names in blocks:
            rows = []
            for n in names:
                cmd = self.get_command(ctx, n)
                if cmd is None or cmd.hidden:
                    continue
                rows.append((n, cmd.get_short_help_str(limit)))
            if rows:
                with formatter.section(title):
                    formatter.write_dl(rows)


_SECTIONS = (
    ("Set up environments", ("detect", "add", "edit")),
    ("Inspect", ("list", "show")),
    ("Run", ("run",)),
    ("Clean up", ("rm", "clear")),
    ("Parallel branches", ("worktree",)),
    ("Global defaults", ("config",)),
)


@click.group(cls=SectionedGroup, sections=_SECTIONS,
             epilog="Run 'manageodoo COMMAND --help' for a command's full "
                    "option list.")
@click.version_option(package_name="manageodoo", message="%(version)s")
def cli() -> None:
    """Manage and run multiple Odoo development environments.

    Register each local Odoo install once (detect/add), then launch it by
    name with 'manageodoo run NAME' — right venv, addons-path, ports, and
    config file every time."""


@cli.command("detect")
@click.argument("path")
@click.option("--name", help="Register under this name (default: folder name).")
@click.option("--register", is_flag=True, help="Persist the detected environment.")
@click.option("--with-enterprise", is_flag=True,
              help="Include the enterprise repo (off by default: community only).")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def detect_cmd(path: str, name: str | None, register: bool,
               with_enterprise: bool, as_json: bool) -> None:
    """Scan PATH and show the resolved Odoo layout."""
    res = detect(path)
    if as_json:
        click.echo(_json.dumps(dataclasses.asdict(res), indent=2))
    else:
        click.secho(f"Detected Odoo install at {res.root}", bold=True)
        _print_detect(res, addons_are_optin=True)
    if register:
        env_name = name or os.path.basename(res.root)
        doc = config.load()
        if env_name in doc.get("env", {}):
            raise EnvExists(f"Environment '{env_name}' already exists.")
        detected_ent = _apply_enterprise_choice(res, None, with_enterprise)
        detected_addons = _apply_addons_choice(res, ())
        env = _make_env(env_name, res, doc)
        write_conf(env)
        _store_env(doc, env)
        click.secho(
            f"Registered '{env_name}'  (http {env.http_port}, gevent {env.gevent_port})",
            fg="green",
        )
        _hint_enterprise(env, detected_ent)
        _hint_addons(env, detected_addons)


@cli.command(short_help="Register an environment from a path, with overrides.")
@click.argument("name")
@click.option("--root", required=True, help="Container path of the Odoo install.")
@click.option("--odoo-bin", help="Override the odoo-bin path.")
@click.option("--python", help="Override the interpreter path.")
@click.option("--enterprise", help="Include this enterprise addons dir (opt-in).")
@click.option("--with-enterprise", is_flag=True,
              help="Include the auto-detected enterprise repo (off by default).")
@click.option("--addons", "addons", multiple=True,
              help="Extra addons dir (comma-separated or repeatable).")
@click.option("--db", help="Default database (default: NAME).")
@click.option("--http-port", type=int, help="Fixed http port (default: auto-allocate).")
def add(name, root, odoo_bin, python, enterprise, with_enterprise, addons, db,
        http_port) -> None:
    """Register an environment, auto-detecting from ROOT and applying overrides.

    Registers the community repo only by default; pass --with-enterprise (or an
    explicit --enterprise PATH) to include enterprise."""
    doc = config.load()
    if name in doc.get("env", {}):
        raise EnvExists(f"Environment '{name}' already exists.")
    res = detect(root)
    if odoo_bin:
        res.odoo_bin = odoo_bin
    if python:
        res.python = python
    detected_ent = _apply_enterprise_choice(res, enterprise, with_enterprise)
    detected_addons = _apply_addons_choice(res, addons)
    env = _make_env(name, res, doc, database=db, http_port=http_port)
    write_conf(env)
    _store_env(doc, env)
    click.secho(
        f"Added '{name}'  version={env.version or '?'}  db={env.database}  "
        f"http={env.http_port}  enterprise={'yes' if env.enterprise else 'no'}",
        fg="green",
    )
    _hint_enterprise(env, detected_ent)
    _hint_addons(env, detected_addons)
    for warn in res.warnings:
        # Skip enterprise-specific warnings (e.g. branch drift) when we didn't
        # register enterprise — they don't apply to a community-only env.
        if not env.enterprise and "enterprise" in warn.lower():
            continue
        click.secho(f"  ! {warn}", fg="yellow")


@cli.command(name="list")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def list_cmd(as_json: bool) -> None:
    """List registered environments."""
    doc = config.load()
    envs = [Environment.from_dict(n, e) for n, e in doc.get("env", {}).items()]
    if as_json:
        click.echo(_json.dumps({e.name: e.to_dict() for e in envs}, indent=2))
        return
    if not envs:
        click.echo("No environments yet. Register one with 'manageodoo detect PATH --register'.")
        return
    headers = ("NAME", "VERSION", "BRANCH", "DB", "HTTP", "GEVENT", "ENT", "WT")
    rows = [
        (e.name, e.version or "?", e.branch or "-", e.database or "-",
         str(e.http_port), str(e.gevent_port), "yes" if e.enterprise else "-",
         "yes" if e.managed_worktree else "-")
        for e in envs
    ]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    click.secho("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)), bold=True)
    for row in rows:
        click.echo("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    if any(e.managed_worktree for e in envs):
        click.secho(
            "\nWT=yes: managed worktree — 'rm' also removes its git worktrees.",
            fg="cyan")


@cli.command(short_help="Show full config and the exact command it runs.")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def show(name: str, as_json: bool) -> None:
    """Show an environment's full config and the exact command it would run."""
    doc = config.load()
    env = _get_env(doc, name)
    conf = env.conf or str(config.conf_path(name))
    argv = build_argv(env, conf, stop_after_init=False)
    if as_json:
        click.echo(_json.dumps(
            {"env": env.to_dict(), "addons_path": env.addons_path(), "command": argv},
            indent=2,
        ))
        return
    click.secho(f"Environment '{name}'", bold=True)
    if env.managed_worktree:
        click.secho(
            "  managed worktree — 'rm' also removes its git worktrees "
            "(community/enterprise). Delete with care.", fg="cyan")
    _print_detect(DetectResult(
        root=env.root, community=env.community, odoo_bin=env.odoo_bin,
        python=env.python, enterprise=env.enterprise, custom_addons=env.custom_addons,
        version=env.version, branch=env.branch, enterprise_branch=env.enterprise_branch,
    ))
    click.echo(f"  database          {env.database}   ports http={env.http_port} "
               f"gevent={env.gevent_port}")
    click.echo(f"  addons-path       {','.join(env.addons_path()) or '-'}")
    _warn_missing_addons(env)
    click.secho("\ncommand:", bold=True)
    click.echo("  " + shlex.join(argv))


@cli.command(short_help="Remove an environment (drops its database by default).")
@click.argument("name")
@click.option("--keep-conf", is_flag=True, help="Leave the generated .conf on disk.")
@click.option("--drop-db/--keep-db", "drop_db", default=None,
              help="Drop / keep the PostgreSQL database (default: drop, with a prompt).")
@click.option("-y", "--yes", is_flag=True, help="Skip the destructive confirmation prompt.")
@click.option("--force-drop", is_flag=True,
              help="Terminate active connections when dropping (dropdb --force).")
@click.option("--force", is_flag=True,
              help="For worktree envs: remove even if a worktree is dirty.")
def rm(name: str, keep_conf: bool, drop_db: bool | None, yes: bool,
       force_drop: bool, force: bool) -> None:
    """Remove an environment. By default also drops its PostgreSQL database
    (after confirmation). Use --keep-db to keep it, -y to skip the prompt.

    If the env is a managed worktree, its git worktrees are removed too (so it
    is never left orphaned)."""
    doc = config.load()
    if name not in doc.get("env", {}):
        raise EnvNotFound(f"No environment named '{name}'.")
    env = _get_env(doc, name)

    # Warn loudly when this env owns git worktrees — removing it tears them down
    # too, so the user knows this row is not just a registry entry.
    if env.managed_worktree:
        click.secho(
            f"'{name}' is a managed worktree — its git worktrees "
            "(community/enterprise) will be removed too.", fg="yellow")

    # _teardown_env drops the db first (its own confirm prompt), then removes the
    # git worktrees, unregisters, and deletes the conf.
    _teardown_env(doc, env, drop_db=drop_db, yes=yes, force_drop=force_drop,
                  keep_conf=keep_conf, force=force)
    config.save(doc)
    tail = " (and its git worktrees)" if env.managed_worktree else ""
    click.secho(f"Removed '{name}'{tail} from the registry.", fg="green")


@cli.command(short_help="Remove ALL environments (databases and worktrees too).")
@click.option("--keep-conf", is_flag=True, help="Leave the generated .conf files on disk.")
@click.option("--drop-db/--keep-db", "drop_db", default=None,
              help="Drop / keep each PostgreSQL database (default: drop).")
@click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option("--force-drop", is_flag=True,
              help="Terminate active connections when dropping (dropdb --force).")
@click.option("--force", is_flag=True,
              help="Remove worktree envs even if a worktree is dirty.")
def clear(keep_conf: bool, drop_db: bool | None, yes: bool, force_drop: bool,
          force: bool) -> None:
    """Remove ALL registered environments at once — databases and git worktrees
    included. Destructive; asks once for confirmation unless -y."""
    doc = config.load()
    names = list(doc.get("env", {}))
    if not names:
        click.echo("No environments to clear.")
        return
    n_wt = sum(1 for n in names if doc["env"][n].get("managed_worktree"))
    keep = drop_db is False
    click.secho(
        f"About to remove ALL {len(names)} environment(s)"
        + (f", including {n_wt} managed worktree(s)" if n_wt else "")
        + ("" if keep else " and their PostgreSQL databases") + ":",
        fg="red", bold=True)
    for n in names:
        tag = "  [worktree]" if doc["env"][n].get("managed_worktree") else ""
        click.echo(f"  - {n}{tag}")
    if not yes and not click.confirm(
        click.style("Proceed? This cannot be undone", fg="red"), default=False):
        click.echo("Aborted.")
        return
    # One confirmation covers the batch, so skip each env's own db prompt. A
    # failure on one env (e.g. a dirty worktree needing --force) is collected and
    # the rest still get cleared.
    failed: list[tuple[str, str]] = []
    for n in names:
        try:
            _teardown_env(doc, _get_env(doc, n), drop_db=drop_db, yes=True,
                          force_drop=force_drop, keep_conf=keep_conf, force=force)
        except ManageodooError as exc:
            failed.append((n, str(exc)))
    config.save(doc)
    click.secho(f"Cleared {len(names) - len(failed)} environment(s).", fg="green")
    for n, msg in failed:
        click.secho(f"  ! kept '{n}': {msg}", fg="yellow")


@cli.command(short_help="Change a registered environment's settings.")
@click.argument("name")
@click.option("--rename", help="Rename the environment (also renames its conf).")
@click.option("--python", help="Set the interpreter path.")
@click.option("--odoo-bin", help="Set the odoo-bin path.")
@click.option("--enterprise", help="Set the enterprise addons dir.")
@click.option("--addons", "addons", multiple=True,
              help="Replace custom addons dirs (comma-separated or repeatable).")
@click.option("--db", help="Set the default database (updates db_filter too).")
@click.option("--http-port", type=int, help="Set http port (gevent recomputed).")
@click.option("--gevent-port", type=int, help="Set the gevent port explicitly.")
@click.option("--data-dir", help="Set the Odoo data-dir.")
@click.option("--dev", help="Set the --dev features (empty string clears).")
@click.option("--demo/--no-demo", "demo", default=None, help="Set demo data on/off.")
def edit(name, rename, python, odoo_bin, enterprise, addons, db, http_port,
         gevent_port, data_dir, dev, demo) -> None:
    """Update a registered environment's configuration and regenerate its conf.

    Only the options you pass are changed; everything else is left as-is."""
    doc = config.load()
    env = _get_env(doc, name)
    defaults = config.defaults(doc)

    if python is not None:
        env.python = python
    if odoo_bin is not None:
        env.odoo_bin = odoo_bin
    if enterprise is not None:
        env.enterprise = enterprise
    if addons:
        env.custom_addons = _resolve_addons(addons, env.root)
    if db is not None:
        env.database = db
        env.db_filter = f"^{db}$"
    if http_port is not None:
        env.http_port = http_port
        env.gevent_port = ports.gevent_for(http_port, defaults)
    if gevent_port is not None:
        env.gevent_port = gevent_port
    if data_dir is not None:
        env.data_dir = data_dir
    if dev is not None:
        env.dev = dev
    if demo is not None:
        env.demo = demo

    # Rename last: move the registry key and the conf file path.
    if rename and rename != name:
        if rename in doc.get("env", {}):
            raise EnvExists(f"Environment '{rename}' already exists.")
        old_conf = Path(env.conf).expanduser() if env.conf else config.conf_path(name)
        env.name = rename
        env.conf = str(config.conf_path(rename))
        del doc["env"][name]
        if old_conf.exists():
            old_conf.unlink()

    write_conf(env)
    _store_env(doc, env)
    _warn_missing_addons(env)
    click.secho(f"Updated '{env.name}'.", fg="green")


@cli.command(context_settings=dict(ignore_unknown_options=True),
             short_help="Launch Odoo for NAME (flags override one launch).")
@click.argument("name")
@click.option("-d", "--database", help="Database (default: env's).")
@click.option("-p", "--http-port", type=int, help="Override http port.")
@click.option("-i", "--init", help="Install module(s), comma-separated.")
@click.option("-u", "--update", help="Update module(s), comma-separated.")
@click.option("--dev", help="Odoo --dev features (e.g. all).")
@click.option("--demo/--no-demo", "demo", default=None, help="Force demo data on/off.")
@click.option("--test", "test", multiple=True,
              help="Run test(s): a bare test_ function name (the '.' Odoo needs "
                   "is added for you) or a full --test-tags spec "
                   "([-][tag][/module][:class][.method]). Comma-separated or "
                   "repeatable. Implies --test-enable.")
@click.option("--stop-after-init", is_flag=True, help="Init then exit.")
@click.option("--log-level", help="Odoo log level.")
@click.argument("raw", nargs=-1, type=click.UNPROCESSED)
def run(name, database, http_port, init, update, dev, demo, test,
        stop_after_init, log_level, raw) -> None:
    """Launch Odoo for NAME. Args after -- are passed straight to odoo-bin.

    With --test, Odoo starts with --test-enable and --test-tags built from the
    given names. Note: Odoo only executes tests for modules being installed or
    updated in that session, so pair --test with -u (or -i) MODULE."""
    doc = config.load()
    env = _get_env(doc, name)
    _warn_missing_addons(env)
    if test and not (init or update):
        click.secho(
            "  ! --test without -i/-u: Odoo only runs tests for modules being "
            "installed/updated. Add e.g. -u <module> or nothing will execute.",
            fg="yellow", err=True)
    code = run_env(
        env,
        database=database,
        http_port=http_port,
        init=init,
        update=update,
        dev=dev,
        demo=demo,
        test=test,
        stop_after_init=stop_after_init,
        log_level=log_level,
        raw=raw,
    )
    raise SystemExit(code)


@cli.group(short_help="Work on several branches at once via git worktrees.")
def worktree() -> None:
    """Manage parallel-branch worktrees (community + enterprise in lockstep).

    Each branch gets its own working directory sharing the origin repo's
    history — switch branches by picking an environment, no stash/commit."""


@worktree.command("add",
                  short_help="Create worktrees for BRANCH and register an env.")
@click.argument("branch")
@click.option("--from", "source_name", required=True,
              help="Source environment whose repos to branch from.")
@click.option("--repos", type=click.Choice(["both", "community", "enterprise"]),
              default="both", show_default=True,
              help="Which repos to create a worktree of.")
@click.option("--name", help="Env/worktree name (default: slug of BRANCH).")
@click.option("--path", help="Custom parent dir; worktree goes in <path>/<name>/.")
@click.option("--start-point", help="Start ref when creating a new branch (default: HEAD).")
@click.option("--fetch", "do_fetch", is_flag=True, help="Fetch remotes first.")
@click.option("--http-port", type=int, help="Fixed http port (default: auto-allocate).")
def worktree_add(branch, source_name, repos, name, path, start_point, do_fetch,
                 http_port):
    """Create worktrees for BRANCH in the source env's repos and register them
    as a new environment. --repos picks community, enterprise, or both."""
    doc = config.load()
    source = _get_env(doc, source_name)
    env = wt.add(doc, source, branch, name=name, path=path,
                 start_point=start_point, fetch=do_fetch, http_port=http_port,
                 repos=repos)
    write_conf(env)
    _store_env(doc, env)
    click.secho(f"Created worktree env '{env.name}'  branch={branch}  "
                f"version={env.version or '?'}  http={env.http_port}", fg="green")
    if env.wt_community_origin:
        click.echo(f"  community (worktree)  {env.community}")
    else:
        click.echo(f"  community (reused)    {env.community}")
    if env.enterprise:
        tag = "worktree" if env.wt_enterprise_origin else "reused"
        click.echo(f"  enterprise ({tag})    {env.enterprise}")
    click.echo(f"Run it with:  manageodoo run {env.name}")


@worktree.command("list", short_help="List managed worktree environments.")
@click.option("--from", "source_name",
              help="Show raw git worktrees for this env's repos instead.")
def worktree_list(source_name):
    """List managed worktree environments (or a source env's git worktrees)."""
    doc = config.load()
    if source_name:
        source = _get_env(doc, source_name)
        for label, repo in (("community", source.community),
                            ("enterprise", source.enterprise)):
            if not repo:
                continue
            click.secho(f"{label}: {repo}", bold=True)
            for w in gitutil.worktree_list(repo):
                click.echo(f"  {w.get('branch') or '(detached)':40}  {w['path']}")
        return
    managed = [Environment.from_dict(n, e) for n, e in doc.get("env", {}).items()
               if e.get("managed_worktree")]
    if not managed:
        click.echo("No managed worktrees. Create one with 'manageodoo worktree add'.")
        return
    headers = ("NAME", "BRANCH", "VERSION", "DB", "HTTP", "PATH")
    rows = [(e.name, e.branch or "-", e.version or "?", e.database or "-",
             str(e.http_port), e.root) for e in managed]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    click.secho("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)), bold=True)
    for row in rows:
        click.echo("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


@worktree.command("rm",
                  short_help="Remove a worktree env (git worktrees + db).")
@click.argument("name")
@click.option("--force", is_flag=True, help="Remove even if a worktree is dirty.")
@click.option("--keep-conf", is_flag=True, help="Leave the generated .conf on disk.")
@click.option("--drop-db/--keep-db", "drop_db", default=None,
              help="Drop / keep the PostgreSQL database (default: drop, with a prompt).")
@click.option("-y", "--yes", is_flag=True, help="Skip the destructive confirmation prompt.")
@click.option("--force-drop", is_flag=True,
              help="Terminate active connections when dropping (dropdb --force).")
def worktree_rm(name, force, keep_conf, drop_db, yes, force_drop):
    """Remove a worktree environment: delete both git worktrees, drop its
    database (by default), and unregister it."""
    doc = config.load()
    env = _get_env(doc, name)
    if not env.managed_worktree:
        raise ManageodooError(
            f"'{name}' is not a managed worktree; use 'manageodoo rm' instead.")
    _teardown_env(doc, env, drop_db=drop_db, yes=yes, force_drop=force_drop,
                  keep_conf=keep_conf, force=force)
    config.save(doc)
    click.secho(f"Removed worktree '{name}' (both repos) and unregistered it.",
                fg="green")


@worktree.command("clear",
                  short_help="Remove ALL managed worktree environments.")
@click.option("--keep-conf", is_flag=True, help="Leave the generated .conf files on disk.")
@click.option("--drop-db/--keep-db", "drop_db", default=None,
              help="Drop / keep each PostgreSQL database (default: drop).")
@click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option("--force-drop", is_flag=True,
              help="Terminate active connections when dropping (dropdb --force).")
@click.option("--force", is_flag=True, help="Remove even if a worktree is dirty.")
def worktree_clear(keep_conf, drop_db, yes, force_drop, force):
    """Remove ALL managed worktree environments — git worktrees and databases
    included. Plain (non-worktree) envs are left untouched. Destructive; asks
    once unless -y."""
    doc = config.load()
    managed = [n for n, e in doc.get("env", {}).items()
               if e.get("managed_worktree")]
    if not managed:
        click.echo("No managed worktrees to clear.")
        return
    keep = drop_db is False
    click.secho(
        f"About to remove ALL {len(managed)} managed worktree(s)"
        + ("" if keep else " and their PostgreSQL databases") + ":",
        fg="red", bold=True)
    for n in managed:
        click.echo(f"  - {n}  ({doc['env'][n].get('branch') or '-'})")
    if not yes and not click.confirm(
        click.style("Proceed? This cannot be undone", fg="red"), default=False):
        click.echo("Aborted.")
        return
    failed: list[tuple[str, str]] = []
    for n in managed:
        try:
            _teardown_env(doc, _get_env(doc, n), drop_db=drop_db, yes=True,
                          force_drop=force_drop, keep_conf=keep_conf, force=force)
        except ManageodooError as exc:
            failed.append((n, str(exc)))
    config.save(doc)
    click.secho(f"Cleared {len(managed) - len(failed)} managed worktree(s).",
                fg="green")
    for n, msg in failed:
        click.secho(f"  ! kept '{n}': {msg}", fg="yellow")


_INT_DEFAULTS = {"http_port_base", "port_stride", "gevent_offset"}
_BOOL_DEFAULTS = {"demo"}


@cli.group("config")
def config_group() -> None:
    """View and set global defaults (ports, dev, demo, worktrees_dir)."""


@config_group.command("show")
def config_show() -> None:
    """Print effective defaults (built-in, overlaid by your overrides)."""
    doc = config.load()
    overrides = doc.get("defaults", {})
    merged = config.defaults(doc)
    width = max(len(k) for k in merged)
    for key, val in merged.items():
        marker = "*" if key in overrides else " "
        click.echo(f" {marker} {key.ljust(width)}  {val!r}")
    click.echo("\n(* = overridden in config.toml)")


@config_group.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a default. KEY is one of the keys shown by 'config show'."""
    known = config.DEFAULTS
    if key not in known:
        raise ManageodooError(
            f"Unknown default '{key}'. Known: {', '.join(sorted(known))}.")
    if key in _INT_DEFAULTS:
        parsed: object = int(value)
    elif key in _BOOL_DEFAULTS:
        parsed = value.strip().lower() in ("1", "true", "yes", "on")
    else:
        parsed = value
    doc = config.load()
    doc.setdefault("defaults", {})[key] = parsed
    config.save(doc)
    click.secho(f"Set {key} = {parsed!r}", fg="green")


@config_group.command("unset")
@click.argument("key")
def config_unset(key: str) -> None:
    """Remove an override, reverting KEY to its built-in default."""
    doc = config.load()
    if doc.get("defaults", {}).pop(key, None) is None:
        click.echo(f"'{key}' was not overridden; nothing to do.")
        return
    config.save(doc)
    click.secho(f"Unset {key} (reverted to built-in default).", fg="green")


def main() -> None:
    try:
        cli()
    except ManageodooError as exc:
        click.secho(f"error: {exc}", fg="red", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
