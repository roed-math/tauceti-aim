# Target lists

A target list tells the authors what to work on: the roadmap milestones on the path to one goal,
grouped by roadmap. The authors take only the items that are open and whose prerequisites have
landed, and the prompt names exactly those milestones. The file is re-read every round, so edits
take effect without a restart. `targets/` holds the lists in use; `fleet.targets` names the one a
fleet follows.

## Format

```markdown
# A goal
<!-- tauceti-targets:v1 -->
Free text until the first `## ` heading: the goal, the ordering rule, anything a reader needs.

## LocalFieldsRamification
- [ ] `ramification-index` — Layer 0, `e` and `f`: "Define …" (serves: B9; needs: `normalized-valuation`)
- [~] `unitfiltration` — Layer 1, the unit filtration (serves: B5; needs: none; in flight: #5500)
- [x] `normalized-valuation` — Layer 0, the normalized valuation (serves: B5; done: #5489)
> Note lines, and anything else that is not an item, are ignored.

## Gaps
Milestones no roadmap states yet. This section is never authored against.
```

- The marker comment is required. Without it the file is not a target list and the round stops
  rather than author against a misread file.
- Each `## ` heading is an **area**: the exact name of a roadmap directory in TauCetiRoadmap.
  `up` refuses a list naming an area that is not there. A drafted roadmap whose PR has not merged
  belongs under `## Gaps` until it does.
- An item is `- [ ]` open, `- [~]` in flight, or `- [x]` done. Its **slug** is the first backticked
  token, conventionally the milestone's main declaration in kebab-case. The text runs from ` — ` to
  the trailing parenthesis.
- The trailing parenthesis holds `key: value` clauses separated by `;`. `needs:` lists prerequisite
  slugs from any area, or `none`. `in flight: #N` names the PR working on the item. `lookahead: no —
  <reason>` keeps an item whose route is unsettled out of lookahead authoring, both as a target and
  as a supplier to stub (see [operating.md](operating.md#lookahead-authoring)); agents never see this
  clause. Anything else (`serves:`, `done:`) is kept verbatim for readers. No clause may contain a
  parenthesis: the parser would then read only the innermost pair, and the item would lose its
  `needs:`.
- An item is **eligible** when it is open and everything it needs is done; an in-flight prerequisite
  counts as not landed.

## Keeping it true

A list goes stale in two ways: a PR marked in flight is closed rather than merged, or someone else
lands the milestone. Either leaves the items that need it ineligible, and the authors idle.

The curator, the periodic `curate` round, fixes both, as described in
[operating.md](operating.md#the-periodic-rounds). `tauceti-aim targets` shows the same audit of
in-flight items by hand, and `--apply` writes the unambiguous changes.

Mark an item `[~]` with `in flight: #N` when you open a PR for it by hand. The fleet's own authors
record their claims themselves, and the live view counts an item done as soon as a merged PR carries
its marker.

## Sharing a list

A list can be shared in two ways, and the two combine.

- **Through its repository, between hosts.** When the list is tracked in git, the curator commits
  each change and pushes it to the list's `origin`. Before it reads the list it fetches and rebases
  onto the upstream, so it starts from what the other hosts curated, and a push rejected because
  someone pushed first is retried after the same sync.
- **As one file, between fleets on one host.** Every fleet's `fleet.targets` names the same file, in
  a clone their users share; [setup.md](setup.md#sharing-one-target-list) sets one up.

Everything that writes the list (the curate and decide rounds of every fleet, and
`tauceti-aim targets --apply`) takes the lock `.<name>.lock` beside it, then writes its edit merged
three ways with the list as it is at that moment. Edits to different items both land. Edits to the
same or adjacent lines are not written, and the next round starts from the new list. The list's
repository should ignore `.*.lock`, as this one does.

Two cases are left to you:

- A clone with uncommitted changes, such as a hand edit, is never rebased. Commit the edit; the
  next curation pushes it.
- A local commit that conflicts with the upstream is left in place, and `tauceti-aim attention`
  lists it as `targets-diverged`. In the clone, `git pull --rebase`, settle the list by hand, and
  `git push`.

## Writing a good list

- Take milestones from what the roadmap READMEs actually state, not from what you wish they said.
  An item no roadmap describes cannot be reviewed against anything; it belongs under `## Gaps`, and
  the fix is a roadmap PR.
- Keep items small enough for one PR each, and write `needs:` honestly: the order the authors take
  items in comes from it.
- Say in the preamble what the goal is and what must not be done on the way (for example, no
  special-case shortcuts that prove only the instance you need).
