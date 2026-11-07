# Operating a fleet

What the fleet does by itself, and the few things that need you. Paths are relative to the fleet
home (`$TAUCETI_FLEET_HOME`, default `~/.tauceti-fleet`).

## The shape follows the backlog

After every round, the worker runs `tauceti-aim reconcile` through its round-done hook. The
reconcile reads the counts that round's survey left behind, so it costs no GitHub request, and
reshapes the fleet:

- **Authors** (`<name>-c*` pinned to Claude, `<name>-x*` running `auto`) are enabled only while the
  fleet's account has fewer than `authoring.max_open_prs` open PRs in scope (the worker's default is
  8). At that many or more the worker's own backpressure rule would make them decline every round, so
  they stay defined but disabled.
- **Fixers** (`<name>-fix*`) run fix, fix-ci and rebase, taking the PRs that serve the target list
  first: a PR whose marker names an item of the list, or one the list marks `in flight`. Only when
  none of those needs work do they turn to the account's other PRs. They are sized by the fix backlog: the
  account's PRs labelled `awaiting-author` or `ci-failed`. There is 1 below a backlog of
  `up.fixer_backlog_start` (default 6), 2 from there, and one more every `up.fixer_backlog_step`
  (default 2) PRs after that, up to `up.max_fixers` (default 3); while authoring is blocked there are
  `up.max_fixers`. Never more than there are PRs to fix. Odd-numbered fixers are pinned to Claude; even-numbered ones run `auto`.
- **Reviewers** (`<name>-rev*`) run `auto`: `up.max_reviewers` (default 2) once two or more authors
  and fixers are active, else 1.
  Reviewing other people's PRs is what the fleet owes the project for the reviews its own PRs get.
  When a reviewer does take one of the account's own PRs, it takes those serving the target list
  first; how often it reviews the account's own PRs rather than other people's is unchanged.

An `auto` worker uses Codex while Codex's usage window has room and Claude otherwise, so that half
of the fleet never parks when one subscription runs dry.

**Flexible workers (`up.flex = true`).** The fixed roles above suit a steady trickle of review. When
other fleets review a batch of the account's PRs at once, the backlog outgrows `up.max_fixers` while
the authors keep opening PRs. With `up.flex` every author and fixer slot (`up.claude` + `up.codex` +
`up.max_fixers` of them, same ids, same agents) runs fix, fix-ci, rebase and roadmap, and the worker
chooses at the start of each round. Its cascade takes the first of: a target-list PR that needs
work, any other PR of the account's that needs work, a new PR. A PR another worker holds is skipped
through the usual claims, so the slots spread over the backlog, and authoring resumes as soon as
fewer PRs need work than there are slots to take them. The reconcile turns no slot off for the
backlog or for the open-PR cap (at the cap a slot still fixes, and the worker itself refuses to
author). When every enabled slot's last round found nothing to fix and nothing to author, all but
the first are turned off and it probes, as idle authors do. Reviewers stay at `up.max_reviewers`.
These rounds read every open PR's review state, as fixers' rounds do, where an author-only round
reads just the PR list.

**Config changes wait for the round to end.** The manager restarts a worker whenever its entry in
`workers.toml` changes, which mid-round would throw away a build. The reconcile keeps a mid-round
worker's old entry and switches it at a later reconcile that finds it between rounds; the reconcile
line lists these as `deferred (mid-round)`. `tauceti-aim reconcile --dry-run` shows what a pass
would do.

## Waiting for host load

A `lake build` takes every core a container can see, and rounds that overlap slow each other until they
hit the round timeout. On one 32-core host over 8 days (2026-10-30 to 11-06), finished rounds per hour
peaked at load 32-64 (11.8) and fell to 5.3 above 192, while the share that failed rose from 4% to 14-21%.

With `up.max_load` set, a worker's loop holds before a build-heavy round while the host's 1- or 5-minute
load is at or above the limit. The view shows the worker as `waiting-load` and has a `host load` row. A held
worker reads no quota or GitHub state and spends no tokens, and the wait does not count against the round
timeout. Reviewers and the other light stages (`curate`, `decide`, `progress`, `overlap-scan`) do not wait,
nor does a one-off `tauceti-aim round`.

A new round's first build comes minutes after it starts, so starts are also spaced: a worker takes the slot
in `up.load_gate_dir` just before launching, and the next waits until `up.load_spacing` seconds have passed
(180 by default) or that round has ended, since a round that ended adds no load. Fleets on one host should
name the same directory, created by hand with group write and setgid:

```bash
mkdir /home/shared/fleet/load-gate
chmod 2775 /home/shared/fleet/load-gate
```

The gate only controls starts, so a round already running keeps its cores whatever the load does. To keep
other work on the host responsive during that time, raise its CPU weight above the containers', each of
which has the same weight as all of `user.slice`:

```bash
sudo systemctl set-property user.slice CPUWeight=1000
```

If the load cannot be read or the slot cannot be written, rounds start anyway: the worker's log says so
once, and the `host load` row names a directory the user cannot write.

## The GitHub gate

Every request the workers and the tool make as the fleet's account goes through one gate store
(`gate/`) with rolling-hour budgets: `gate.mutations_per_hour` for writes and `gate.reads_per_hour`
for reads. A worker that sees any identity other than `fleet.github_login`, or a credential error,
halts and stays halted until you look: `tauceti-aim clear-halt` shows the incident and clears it.

```bash
tauceti-aim gate status
```

```bash
tauceti-aim gate report --since 24h
```

The status line lists budgets used, pushes per repository, and publications (PR creations, pushes,
comments) still in progress or parked. A round that failed before publishing leaves a parked entry
with no remote effect; `tauceti-aim gate publication prune` archives those.

Budgets are staging controls, not guarantees. Raise them only after measuring a quiet week.

## The Claude login pool

Renewing a Claude access token revokes the previous one immediately, so workers that share one login
kill each other's rounds. The fleet keeps a **pool** of login chains, `logins/claude-1`,
`logins/claude-2`, … each an ordinary sign-in to the same subscription. Chains are named for the pool,
never for a worker: at every reconcile each Claude-capable worker, and the periodic runner, leases one
(`logins/leases.json`), keeps it while it exists, and releases it when it is disabled or renumbered.
Reshaping the fleet never asks for a new sign-in.

Nobody has to renew anything. A leased chain is renewed by its worker before each launch, early
enough to outlast the round, including while the worker waits out a usage limit. A free chain is
renewed by the tool, from every reconcile and every half hour from the live view. The only case that
needs you is a chain that can no longer be renewed: it lost its refresh token, or the server rejects
it (its token expired and every renewal since has failed). The live view's `credentials` row names
it, the attention list has a `login` item with the sign-in command, and the reconcile moves its
worker to a healthy free chain if there is one.

```bash
tauceti-aim logins
```

prints the chains, the leases, and the exact sign-in line for each chain still missing. Workers left
without a chain share `~/.claude`, and `logins` says so.

## Things that need you

The live view shows a `needs you` badge and panel; `tauceti-aim attention` prints them in full.

- **A round declined to act, and the decide stage handed it to you.** A fix or rebase round whose
  agent could not act files an incident with the agent's last words and the PRs it named. The decide
  stage (below) rules on it first, and only two rulings reach this list: `roadmap`, with a drafted
  roadmap change, and `escalate`, with its analysis, any recommendation and the evidence.
  `--file-roadmap PR` files a drafted change. An agent applies it to the roadmap's README on a
  branch, and you read the diff. On your yes the change is opened as a TauCetiRoadmap PR from the
  account's fork (`--open-roadmap PR` does this without asking; `--show-roadmap PR` shows it again). The agent may also edit the roadmap's `Suggested.lean`, which is then built as TauCetiRoadmap's CI builds it, together with every `Suggested.lean` that imports it. The TauCeti PR then waits for the roadmap PR.
  A proposal may name up to three roadmaps. That happens when a name one roadmap pins is used by another, for example a declaration `#check`ed by a consumer's `Suggested.lean`, which cannot be renamed in either alone without breaking the other's build. The change is then one PR touching each of them, and it may touch only the roadmaps the proposal names.
  `--lookup` resolves the named PRs; `--ack PR` or `--ack all` archives the incident once handled. A
  decline the stage has not ruled on within 2 hours is listed too. So are the stage's rulings from
  the last 24 hours, closes included, though they need nothing from you.
- **A review exchange hit its cap**, or a review errored three times without a verdict. The worker
  stops spending on that PR until you look.
- **A fixer spent its budget on a PR** (`budget-spent`): its fix, fix-ci or rebase attempts at the
  PR's head are used up, or three rounds at that head died before the agent started (bubble's
  pre-agent Mathlib setup failed; such rounds are not charged to the fix budget). The worker stops
  spending on that head. The item clears itself when the PR gets a new head or closes.
- **A target-list item whose PR closed without a recorded verdict.** The curator cannot tell whether
  the work landed elsewhere, so it asks: mark it `[ ]` to re-author or `[x]` if done.
- **A lookahead branch that did not work as planned** (`lookahead`): see
  [Lookahead authoring](#lookahead-authoring) for the kinds and what each means.
- **A target list that diverged from its repository** (`targets-diverged`): a curation committed
  here conflicts with one pushed elsewhere. See
  [target-lists.md](target-lists.md#sharing-a-list).
- **A slot that did not start** (`schedule-start`): the schedule's `up` failed, with the reason it
  gave. The timer retries every minute while the slot lasts, so fixing the cause is enough. The
  item stays after the slot ends, so a lost slot is seen, and clears at the next successful `up`
  or with `--ack schedule`.

A worker in a long backoff can be restarted between rounds with `tauceti-aim restart ID`, or
`--when-idle` to queue it for the end of the current round.

## Lookahead authoring

With `authoring.lookahead = true`, an author that finds nothing it can take on the target list
first looks for a blocked item it can prove ahead: an open item whose unmet needs are all in flight
or eligible, settled, and themselves unblocked (a stub never stands in for another stub). It proves
the item against `sorry`'d stubs of those suppliers' pinned statements on the branch
`lookahead/<area>/<slug>` of the account's TauCeti fork, writes the plan of pull requests into the
branch's `LOOKAHEAD.md`, and pushes once. The branch is never opened: the push wrapper pushes nothing
else in that round and the PR wrapper refuses. Only when no candidate can be claimed does it author
outside the list; the number of live branches is not capped. The claim is `lookahead/<area>/<slug>`,
separate from the item's author claim.

When the suppliers land, the item becomes eligible and an author ports the branch: it opens the
plan's next split as a pull request, the stubs replaced by the landed declarations. One PR per round,
but while a split that does not wait on another is still unopened, the item stays available to the
next round, so independent splits are opened without waiting for each other to merge. Every port PR
carries a `tauceti-lookahead-port:v1` marker, which is how the next round knows what is open.
A complete branch that stubs nothing (everything the item uses has landed, though a listed need is
not complete) is ported at once, without waiting for that need.

All of this state is on GitHub, so it does not matter which fleet is active. The curator's round
deletes the branch of an item that is done, and a branch whose every planned split has merged
(spent: it has nothing left to port, even if its item still lacks something), and lists stale branches (built on a `main` more than a
week old) and branches whose item is not on the list; it never deletes those. With
`authoring.lookahead = false` a fleet is passive: it proves and ports nothing, but sweeps branches,
and leaves an eligible item with a fresh branch for six hours (`TAUCETI_LOOKAHEAD_HOLD_HOURS`)
before authoring it fresh. Without the setting a fleet does none of this and never lists the
branches. Items marked `lookahead: no` in the target list are skipped.

`tauceti-aim lookahead SLUG` runs one such round on that item now, setting or no setting: a session
while the item is blocked, its port once it is eligible. It runs as the periodic worker under the
periodic lock, as `review` does, so curate and decide wait for it.

The live view's `lookahead` row shows the branches and the last week's sessions, ports and deletions.
Every outcome that is not the plan working leaves a `lookahead` incident in the attention list:
`failed` (a session pushed nothing; the item is not offered again for three days), `held` and
`skipped` (a passive fleet held the item, then authored it fresh), `mismatch` (a port round could
not use the branch), `unreadable`, `stale`, `orphan` and `abandoned` (deleted unused).

## Pacing the budget

With `authoring.fallback_max_open = "auto"` the cap on authoring outside the target list follows
the account's weekly Claude budget, toward two goals: target work never runs short because the
budget went outside the list, and nothing is left over when the slot ends.

Every 15 minutes while the fleet runs, the controller reads the weekly usage and the burn rate over
the last three hours. The usage starts wherever your other use of the account left it, and that use
counts in the burn. It splits the fleet's own Claude spend into target, outside and shared work,
from the cost each round reports. It then holds back what everything except outside work will need
until the slot ends (or the weekly reset, if sooner), with `budget_reserve_margin` (0.25) and 5% on
top. If the reserve takes everything, the cap drops to `fallback_auto_min` and outside work stops.
Otherwise the cap moves a step, about 10%, toward the rate that spends the rest by the end. A fleet
running ahead of its slot, after an early switch, plans to the end of the coming slot. The first
hour of each weekly window holds the cap while the burn rate is measured.

The workers read the cap from `<gate>/fallback-cap.json` each round, so changes need no restart. A
file older than 30 minutes falls back to `fallback_auto_min`. The view's `budget` row shows the last
step: budget left, hours to the end, the reserve and surplus, the burn rate and its outside share,
and the cap with its reason. Each change is a line in `watchdog.log`. When the work that is not
outside the list would alone use up the budget before the slot ends, the attention list gets a
`budget-short` item: switch fleets early or lower the load.

The controller reads the same usage responses the workers cache, a worker's own or, in token mode,
the one beside the usage login. A reading over 30 minutes old moves nothing and the view marks it
`stale`. After two hours of that on a running fleet, the attention list gets a `budget-blind` item.

## Watching all fleets

Each fleet can publish its live view as `<fleet.status_dir>/<name>.json`, after every round and on
every schedule tick (each minute), with `tauceti-aim publish` to force one. A user with no fleet of
its own, such as the shared `agents` account, then runs `tauceti-aim watch` and sees all of them:

- a strip with one line per fleet: running, draining, or off, and when its slot starts or ends, in
  red when a slot's start failed;
- one needs-you panel for every fleet, each item tagged with its fleet;
- the full view of the fleet that is running (else the one draining, else the one in its slot),
  which follows the rotation by itself.

The fleets publish rather than open their homes to the viewer: a fleet home holds its Claude login
chains, and the workers' private homes hold access-token copies the sandbox reads. The view needs
only Python with `rich` (the system `python3-rich` will do) and the script, for example
`/home/agents/fleet-shared/tauceti-fleet/bin/tauceti-aim watch`. A snapshot older than 15 minutes
shows its age in red. `--fleets DIR` reads another directory.

## The progress-report worker

`<name>-prog1`, off until `enabled = true` under `[progress]`, writes roadmaps' STATUS.md and
PROGRESS.md reports through TauCetiProgress, as pull requests to TauCetiRoadmap that its merge gate
lands without a human; each landing is announced on Zulip. It is a worker, not a periodic round: it
looks for work all the time and writes a report as soon as a roadmap qualifies. With N the PRs merged
into a roadmap's window and T the days since its last report landed, a roadmap qualifies when N > 0
and either it has been declared complete (archived under `Completed/`) or N + T > 10
(`progress.threshold`). A roadmap never reported qualifies with its first PR. The merge gate allows
one report per roadmap per 6 hours, and the planner respects that. Among the qualifying roadmaps the
one with the most PRs is written. Another operator's open report holds its roadmap for 8 hours, so
the worker does not duplicate it; after that it is treated as stuck.

This needs a TauCetiProgress build with the `threshold` strategy: set `progress.repo` and
`progress.ref` to one (roed-math/TauCetiProgress, branch `feat/threshold-strategy`, until it is
upstream).

Between reports the worker only polls, cheaply: it plans again when the published documentation, or
TauCetiRoadmap's `main`, has moved, or when the last plan's "next qualifies at" has passed, and at
least hourly. The live view's `reports` line shows what it is landing and its last assessment.

It sees each report of its own through to landing, following the rules a hand-run lander learned
landing 29 reports on 2026-09-27. The gate lands a report by compare-and-swap, so each landing leaves
every other open report behind `main`. The worker brings a report up to date, but only while `main`
builds. It asks the gate again, at most twice, when the gate has been quiet for 20 minutes after a
green build. It ignores the gate's first-pass "build not completed" and any refusal about an earlier
head. It files a `progress-stuck` item under `attention` for anything that needs you: a build that
fails on an up-to-date report while `main` builds, or a refusal that survives the re-asks. At most
`progress.max_open` (2) of the account's reports are open at once.

Several fleets acting as one account may each run a progress worker. They never write the same
report: choosing and writing happen under a claim each worker holds in turn. Each sees through only
the reports it opened, so the re-ask and escalation rules above apply once per report; a report no
worker has landed for 8 hours, opened by hand or left by a stopped fleet, is adopted by whichever
worker looks next.

Before a report is opened, the writing model is given the library's source at the window's end to
check each layer's state against. It must run `tauceti-progress check` and fix what that reports:
the prompt's word limits and headings, documentation links copied from the supplied material and
still resolving, and no layer the previous report assessed turned `unassessed`. A report that still
fails after one repair pass is not opened.

## The periodic rounds

Two stages are not about one of the fleet's own PRs and run on a cadence rather than in a loop, as
one-shot rounds under the id `<name>-periodic`, started by the reconcile hook or the live view when
due. One runs at a time.

- **curate**, every 6 hours, keeps the target list true. It checks each in-flight item's PR: merged
  marks it done, closed with a recorded subsumption verdict marks it done and names the PR that
  subsumed it, closed without one is left for you. For the items the authors would take next it also
  looks for the milestone's declarations on main, and asks the model; only an explicit verdict with
  named evidence marks an item "landed elsewhere". When the list is tracked in git it pulls before
  reading it, commits the change, and pushes it when the fleet's account can push to that
  repository, so several fleets or hosts can share one list
  ([target-lists.md](target-lists.md#sharing-a-list)).
- **decide**, on unless `decide.enabled = false`, rules on declined rounds. It runs within about 10
  minutes of a new decline, and every 6 hours while a ruling waits on something. Without a model it
  settles declines that events have overtaken: the PR merged, closed or has a new head. It notes an
  authoring decline with no target. It turns a `wait` into a `retry` once what it waited on has
  merged or reached an open PR. It puts the rest to the model (the curator's model), with the PR, its
  scoreboard and threads, the fixer's account, the open PRs, the target list and the roadmap and
  rubric checkouts. The model rules one of these, and the worker's code checks each ruling before
  acting on it:
  - `retry`: a fixer tries again, and the ruling's note tells it what is new. Allowed once per head.
  - `wait`: on open PRs or items already in the target list.
  - `prerequisite`: the missing roadmap milestone is added at the top of its area in the target
    list, so the authors write it next, and the PR waits for it. The scope rubric accepts a
    prerequisite stage that is in an open PR.
  - `close`, when `decide.close = true`: closes the PR as subsumed and posts the evidence as a
    comment. The PR must be the account's own and carry no hold label. Every merged PR it names must
    have merged, and every declaration it names must be on main. At most `decide.max_closes_per_day`
    (default 3) close each day. If any check fails, the ruling becomes an escalation.
  - `roadmap`: a drafted change, in `<fleet home>/gate/decisions/`. You file it with `attention
    --file-roadmap`, since agents never open TauCetiRoadmap PRs on their own. Once that roadmap PR
    merges, the TauCeti PR's fixer is told to cite it on the scope finding.
  - `escalate`: anything else, including a recommendation to close the PR.

  Besides a close, the stage writes only the fleet's own records and the target list.

```bash
tauceti-aim periodic
```

```bash
tauceti-aim periodic --now curate
```

## The Mathlib cache pool

Each worker hard-links Mathlib's compiled files from a shared pool in `~/.cache/mathlib` before a
round, and bubble mounts them into the container, so a round never downloads Mathlib itself. The
reconcile notices which Mathlib revisions main and the fleet's open PRs pin, and fetches any missing
revision in the background (`warm-pool.log`), so a Mathlib bump needs no action. The live view's
`mathlib pool` row shows needed and warm counts.

## bubble's cache proxy

The artifact-cache daemon on port 7655 serves every container's downloads. Without kim-em/bubble#342
it wedges after 256 connections: rounds hang in `lake cache get` while `bubble cache status` still
says running. The reconcile restarts a daemon that looks wedged and records it in `watchdog.log`; with
the fix installed that should never fire.

## Stopping without losing work

`tauceti-aim down` stops the manager and every worker at once, which throws away any round in
flight. To stop for maintenance instead:

```bash
tauceti-aim down --drain
```

Every worker between rounds stops at once; a worker mid-round finishes its round and stops when it
ends; once none is left, the manager stops. No periodic round starts meanwhile, and no login chain
changes hands. A round can last as long as the round timeout, so a drain can take that long; the
live view shows `DRAINING` until it is done. `tauceti-aim up` ends a drain and starts the fleet
again.

## Logs

Every worker writes durable logs under `state/logs/<id>/`; the periodic rounds under
`state/logs/<name>-periodic/`. `tauceti-aim logs ID -f` follows one. The live view shows health,
the phase and target of each round, the last log line, subscription usage left, the gate, and the
pool; its `recent events` panel fills the rest of the screen.
