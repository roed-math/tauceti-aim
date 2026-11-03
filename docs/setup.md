# Setting up a fleet host

This is written for Ubuntu 26.04, the host the fleet has run on longest. The per-user half applies to
every account that runs a fleet. A note on macOS is at the end.

## Ground rules

- **The fleet acts as its own GitHub account, and only that account's credentials go on the host.**
  Automated activity at a fleet's volume is exactly what GitHub's abuse detection looks for, and an
  account can be suspended for it. Keep your personal account out of reach: no personal token, no
  personal SSH key, no second `gh` login on the fleet user.
- **Workers use HTTPS for git.** The gate admits pushes through the HTTPS credential helper; an SSH
  remote would be a push path the gate never sees. Pick HTTPS at `gh auth login`.
- **One GitHub account, one budget.** Every fleet acting as the same account must share one set of
  gate budgets. See "Several fleets on one host" below.

## 1. Host tools (once, as a sudoer)

```bash
sudo apt update && sudo apt install -y git curl build-essential python3 python3-venv nodejs npm util-linux-extra
```

`gh` from GitHub's own apt repository (the archive's is too old for the worker):

```bash
sudo mkdir -p -m 755 /etc/apt/keyrings && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
```

```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
```

```bash
sudo apt update && sudo apt install -y gh
```

Claude Code and Codex, system-wide. A root-owned install cannot update itself, so turn its updater
off and upgrade by re-running the install line:

```bash
sudo npm install -g @anthropic-ai/claude-code @openai/codex
```

```bash
echo 'DISABLE_AUTOUPDATER=1' | sudo tee -a /etc/environment
```

Incus 7 from the Zabbly stable repository (bubble needs Incus 7; the archive ships older). Follow
the Incus install guide for the repository, then:

```bash
sudo apt install -y incus qemu-system && sudo incus admin init --minimal
```

```bash
sudo ufw allow in on incusbr0 && sudo ufw route allow in on incusbr0 && sudo ufw route allow out on incusbr0
```

## 2. A fleet user (once per user, as a sudoer)

```bash
sudo adduser fleetuser
```

Give the user Incus through `incus-user`, the group `incus`, not `incus-admin`:

```bash
sudo adduser fleetuser incus
```

```bash
sudo loginctl enable-linger fleetuser
```

`incus-admin` is equivalent to root on the host: its members can start a privileged container with
the host's filesystem mounted. A fleet user should not be, because not every round runs in a
container. The curate, decide and progress rounds run their agent on the host, as this user, so an
agent misled by something it read could use `incus` to become root. Membership of `incus` instead
gives the user a project of its own, `user-<uid>`, which Incus creates the first time the user runs
`incus`. It is confined to the user's own uid, to disk paths under the user's home, and to a bridge
of its own, `incusbr-<uid>`, which is everything bubble needs: bubble's `fleet` branch finds that
bridge itself, and maps the user onto the container's user in place of the project's default
mapping. Privileged containers are refused, and the project cannot lift its own restrictions.
Separate bridges also keep each fleet's containers away from the others'.

The user's bridge exists once the user has run `incus` once. As the user:

```bash
incus project list
```

Then, as a sudoer, the firewall rules section 1 gave `incusbr0`, for that bridge (here uid 1007):

```bash
sudo ufw allow in on incusbr-1007 && sudo ufw route allow in on incusbr-1007 && sudo ufw route allow out on incusbr-1007
```

Without them a container on the bridge gets no address, and bubble waits on its network until the
round times out.

bubble maps the user's own uid and gid onto the container's user, so that what it mounts in (its
shared git store among it) belongs to that user inside. Incus allows that only for ids root has
delegated:

```bash
echo "root:$(id -u fleetuser):1" | sudo tee -a /etc/subuid && echo "root:$(id -g fleetuser):1" | sudo tee -a /etc/subgid && sudo systemctl restart incus
```

Without it bubble only prints a warning, and every round fails later, in Lake ("Repository mismatch:
leanprover-community/mathlib4"). `up` checks for it. The restart rebuilds Incus's bridge, so do it
while no other fleet on the host is mid-round; bubble's auth proxy recovers by itself with
kim-em/bubble#344, and otherwise needs `systemctl --user restart bubble-auth-proxy.service` as each
fleet user.

Lingering keeps the user's systemd manager, which runs bubble's daemons, alive with nobody logged
in. That manager keeps the groups it started with: after changing a user's groups, restart it with
`sudo systemctl restart user@$(id -u fleetuser).service`.

If you install the user's `authorized_keys` as root, give `~/.ssh` back to the user afterwards
(`sudo chown fleetuser:fleetuser ~fleetuser/.ssh`): bubble writes its SSH configuration there each
time it creates a container.

## 3. The user's own setup (as the fleet user)

A `sudo -iu` shell has no systemd session variables. Add this to `~/.profile`, where it does nothing
under a real login:

```bash
if [ -z "${XDG_RUNTIME_DIR:-}" ] && [ -d "/run/user/$(id -u)" ]; then export XDG_RUNTIME_DIR="/run/user/$(id -u)"; fi
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "${XDG_RUNTIME_DIR:-/nonexistent}/bus" ]; then export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"; fi
```

Check with `systemctl --user status --no-pager | head -3`, which must show the user manager.

Tools under the user's home:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

uv installs into `~/.local/bin` and adds it to `~/.profile`. Log in again, or `. ~/.profile`, before
going on: the tool, `bubble` and the worker's preflight (which looks for `uvx`) all expect it on PATH.

```bash
curl -sS https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh | sh -s -- -y
```

bubble, from the branch that carries the fixes the fleet needs (see the README's Requirements), and
a local key it uses to reach its containers:

```bash
uv tool install git+https://github.com/roed-math/bubble.git@fleet
```

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "$USER bubble"
```

```bash
bubble doctor
```

The worker, a roadmap checkout, and this repository:

```bash
git clone -b feat/roadmap-targets-v2 https://github.com/roed-math/TauCetiWorker.git ~/TauCetiWorker-v2
```

```bash
git clone https://github.com/TauCetiProject/TauCetiRoadmap.git ~/TauCetiRoadmap
```

```bash
git clone https://github.com/roed-math/tauceti-fleet.git ~/tauceti-fleet
```

```bash
uv tool install git+https://github.com/kim-em/TauCetiWorker.git
```

```bash
ln -sf ~/tauceti-fleet/bin/tauceti-fleet ~/.local/bin/tauceti-fleet
```

The `uv tool install` of the upstream worker is only there to give the tool an interpreter with
`rich` for the live view; the fleet always runs the checkout in `~/TauCetiWorker-v2`.

A commit identity for the fleet. The agents commit inside their containers, which inherit it, and
without one every commit fails with "Committer identity unknown":

```bash
git config --global user.name "Fleet Bot" && git config --global user.email bot@example.org
```

Logins, once each, at the keyboard. `gh` as the fleet's GitHub account, over HTTPS, with the
`workflow` scope:

```bash
gh auth login -h github.com -s workflow -p https
```

Choose the web browser login and answer yes to authenticating Git. Over `ssh` it prints a code to
enter at `https://github.com/login/device`; sign in there as the fleet's account, not your own, since
whichever account the browser is signed in to is the one gh gets.

Codex is optional; without it the `auto` workers run on Claude:

```bash
codex login --device-auth
```

`--device-auth` prints a URL and a code to enter in any browser, so it works over `ssh`; plain
`codex login` waits for a browser on the host itself. One login per fleet user is enough: Codex is
not pooled, and the fleet renews it.

Claude signs in once per login chain, after `init`: `tauceti-fleet logins` prints the lines.

The curator commits the target list it edits and pushes it to the list's `origin`, so the fleet's
account must be able to push there. If it cannot, the commits stay local and nothing else breaks.

## 4. Create the fleet

```bash
tauceti-fleet init --name NAME --targets ~/tauceti-fleet/targets/YOUR_LIST.md --github-login YOUR-BOT
```

```bash
tauceti-fleet logins
```

Run each printed `CLAUDE_CONFIG_DIR=… claude auth login` line and sign in with the subscription the
workers spend. Each prints a URL to open in a browser and asks for the code it shows, so this needs
you at a terminal on the host (an `ssh` session is fine). `logins` asks for one chain per worker that
can run Claude, counting `up.max_fixers` fixers and `up.max_reviewers` reviewers, and one for the
periodic rounds: 11 with the default settings and a progress worker, fewer in pilot mode. Run it again
after changing the shape, since a bigger fleet needs more. The pool is
described in [operating.md](operating.md#the-claude-login-pool).

### Two sign-ins instead of a pool

Each pool login stops renewing about 28 days after its sign-in, so the pool asks for eleven
sign-ins a month. With a `claude setup-token` token, the fleet needs one sign-in a month instead.
Run `claude setup-token` as the fleet user, then paste the token it prints into the file:

```bash
mkdir -p ~/.config/tauceti && (umask 077; read -rsp "token: " t && echo "$t" > ~/.config/tauceti/claude-setup-token)
```

```toml
[claude]
token_file = "~/.config/tauceti/claude-setup-token"
```

Every worker's agents then run on that token, which lasts a year and never rotates, so the workers
can share it. Its scope cannot read the usage endpoint, so the pacer reads usage through one ordinary
login, which `tauceti-fleet logins` names (`CLAUDE_CONFIG_DIR=…/logins/usage claude auth login`).
That login needs a fresh sign-in about every 28 days. The attention list asks for it four days ahead,
and asks for a new token a month before the old one's year runs out.

Before the fleet, check the pieces offline, then run one supervised round:

```bash
bash ~/TauCetiWorker-v2/tests/run-all
```

```bash
tauceti-fleet up --dry-run
```

```bash
tauceti-fleet round
```

`round` ends with the gate report: every request accounted for, no halts, nothing parked. Read the
round's log and any pull request it opened as you would a colleague's. Then `tauceti-fleet up`.

## Several fleets on one host

Each fleet user needs everything in sections 2 to 4. Then a few things must differ between the
fleets, or be shared on purpose.

- **The fleet name.** Worker ids, and the Incus containers named after them
  (`tauceti-worker-<id>`), are prefixed with `fleet.name`, so choose names that are not prefixes of
  each other, such as `gq2` and `gqm`. Users in `incus` (section 2) each see only their own project,
  so their containers cannot collide. Users in `incus-admin` share the `default` project and can
  see and delete each other's containers, so there the names are all that keeps the fleets apart.
- **bubble's ports, for users who share `incusbr0`.** Each user's bubble runs a relay (port 7653),
  an auth proxy (7654) and an artifact cache (7655), listening on the address of the user's bridge.
  Users in `incus` have bridges of their own and need nothing here. Users in `incus-admin` all listen
  on `incusbr0`, so every one after the first moves all three, in `~/.bubble/config.toml`, before its
  first round. The file already has a `[relay]` section; change its port and add the other two:

  ```toml
  [relay]
  port = 7663

  [auth_proxy]
  port = 7664

  [artifact_cache]
  port = 7665
  ```

  A third user takes 7673, 7674 and 7675, and so on. Containers learn the ports from the endpoint
  files the daemons write, so nothing else changes.
- **The GitHub budget, if the fleets act as one account.** Each fleet's gate counts only its own
  requests, so two fleets as one account spend twice the budget you meant to allow. Split the
  account's budget between them in `gate.mutations_per_hour` and `gate.reads_per_hour` (40 writes
  and 1,500 reads an hour, say, as 30 + 1,100 and 10 + 400), or give each fleet its own account. The
  open-PR limits under `[authoring]` count the account's PRs, so they already hold for both together.
- **Progress reports.** Any of the fleets may run a progress-report worker. They take turns choosing
  and writing a report, each sees through only the reports it opened, and `progress.max_open`
  counts the account's open reports, so a second worker adds no load on TauCetiRoadmap's merge gate.
  What it adds is a reporter that keeps working while the other fleet is stopped or out of quota.
- **Claude logins.** Each user signs in its own pool. Two fleets may spend the same subscription,
  but they share its usage window.

The two fleets' workers never duplicate each other's work: they claim it in the project-wide claim
repository, as fleets on different hosts do.

### Sharing one target list

Fleets that work toward the same goal can follow one list file, so that a milestone one fleet's
curator marks done is done for both at once.

1. A directory every fleet user can write through a group they all belong to. The home of an
   account whose group they are members of works, if that group can enter it (`chmod 750` is
   enough). A sudoer adds each fleet user to the group once:

   ```bash
   sudo adduser fleetuser shared
   ```

   Then, as that account (here `shared`), the directory, owned by the group and handing the group on
   to everything created in it:

   ```bash
   umask 002 && mkdir -p ~/fleet && chgrp shared ~/fleet && chmod 2775 ~/fleet
   ```

2. A clone of the list's repository there, group-writable, with git told to keep it so:

   ```bash
   git clone https://github.com/YOU/tauceti-fleet.git /home/shared/fleet/tauceti-fleet
   ```

   ```bash
   cd /home/shared/fleet/tauceti-fleet && git config core.sharedRepository group && chmod -R g+rwX . && find . -type d -exec chmod g+s {} +
   ```

3. As each fleet user, tell git to trust the clone. git refuses a repository another user owns,
   and the curator could then neither commit nor sync the list:

   ```bash
   git config --global --add safe.directory /home/shared/fleet/tauceti-fleet
   ```

4. Point every fleet's `fleet.targets` at the list in that clone. `up` refuses to start a fleet that
   could not keep the list: refused by git, not group-shared, or not writable.

Whoever writes the list (the curate and decide rounds of any of the fleets, or
`tauceti-fleet targets --apply`) takes a lock beside it and merges its edit with whatever changed
since it read the list; [target-lists.md](target-lists.md#sharing-a-list) has the details.

Watch the first rounds of the second fleet: `tauceti-fleet gate status` in each, and `incus list`
to see both fleets' containers by name.

### Sharing the read cache

Fleets acting as one account can also share what they have read. Each survey reads the comments of
every open PR it has not seen at its current `updatedAt` within the last 3 hours, so a fleet whose
slot opens after days off rereads them all at once, about 900 reads in 10 minutes for 320 PRs.
Pointed at one directory, the incoming fleet reads on from the outgoing one's cache instead:

```toml
[fleet]
shared_cache = "/home/shared/fleet/cache"
```

The directory goes beside the shared target list, in the group directory made above. `up` creates
`review_state/` inside it, group-writable and setgid, and refuses to start when the other fleets
could not replace what this one writes there. Run each fleet's `up` (or `reconcile`) once after
setting it, so the workers get it.

### A login shared with another operator

When another operator's fleet runs the same GitHub login, each fleet should tend only the PRs it
wrote. Authors name their branches after the worker (`roadmap/<slug>-<name>-c1`), so:

```toml
[fleet]
own_branches_only = true
```

makes rebase, fix, fix-ci, the fixer count and the open-PR count that `authoring.max_open_prs`
caps follow only branches containing `-<name>-`, and no bot PRs. Reviews are unaffected.

## An API-billed fleet

With `claude.billing = "api"` the workers run Claude on an API key rather than on subscription
logins: no login pool, no usage windows to pace, no Codex. Every worker is pinned to Claude.

The key lives in a file only the fleet user can read, never in `fleet.toml`, a shell profile or the
environment, where every child process (and every agent tool call on the host) would inherit it.
As the fleet user, with nothing echoed and nothing left in the shell history:

```bash
umask 077 && mkdir -p ~/.config/tauceti && read -rsp 'Anthropic API key: ' k && printf '%s\n' "$k" > ~/.config/tauceti/anthropic.key && unset k && echo
```

```toml
[claude]
billing = "api"
api_key_file = "~/.config/tauceti/anthropic.key"
budget = true
```

`up` refuses a key file that is missing or readable by anyone else. The worker hands the key to a
round as a read-only file, so an agent in its container can read it, as it could read a
subscription token; root on the host, and any `incus-admin` member, can read it too. A key of its
own for the fleet, with a spending limit set in the Anthropic Console, bounds what a leaked key
costs.

`budget = true` admits each Claude session only while the local ledger has the money for it.
The ledger starts empty, so nothing runs until you grant some:

```bash
tauceti-fleet budget --grant 100
```

```bash
tauceti-fleet budget --grant-rate 20
```

The first adds $100 once; the second adds $20 an hour from now on, also while the fleet is
stopped. `tauceti-fleet budget` shows the balance, and the view's `usage` row shows it too. The
ledger is local accounting from the costs Claude reports, not the Console's bill.

A fleet that should run all the time sets `schedule.active = "always"` and installs the timer
(`tauceti-fleet schedule --install`): it starts the fleet, and starts it again after a reboot,
unless `down` stopped it.

## macOS

The tool runs on macOS too, with `up.sandbox = "host"` (bubble needs Incus). Claude credentials live
in the Keychain there, one item per login chain, and an expiring token is renewed by a short `claude`
run rather than by rewriting a file. New checkouts are APFS clones of an existing built checkout, so
a new worker costs seconds rather than a 12 GB Mathlib unpack. Host mode leaves the gate as a
convention rather than a control: the agent could reach GitHub around it. Prefer a Linux host with
bubble for anything unattended.
