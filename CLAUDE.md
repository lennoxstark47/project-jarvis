## Jarvis project workflow

This project is planned in `docs/` (`00-IMPLEMENTATION_TRACKER.md` through
`04-CREDENTIALS_AND_SECURITY.md`) — read `README.md` for the full index.

One phase = one session. The user will open a fresh session per phase and say
something like **"start phase 2"** or **"continue phase 2"** — when that happens,
follow the protocol below immediately, without asking the user to re-explain
context that's already written down here.

### When told to start/continue a phase

1. Read `docs/00-IMPLEMENTATION_TRACKER.md` in full — this is the source of truth
   for what's done, in progress, or untouched.
2. If asked to start phase N, confirm phase N-1's Status is ✅ Done. If it isn't,
   don't silently skip ahead — tell the user what's still open in it and ask
   whether to finish that first or proceed anyway.
3. If asked to *continue* a phase already 🔶 In progress, read that phase's Notes
   in the tracker — they say exactly where the last session left off.
4. Read that phase's section in `docs/01-PHASE_PLAN.md` for full scope and its
   "Definition of done". Skim whichever of `02-SYSTEM_DESIGN.md` /
   `03-AGENTS_AND_MODELS.md` / `04-CREDENTIALS_AND_SECURITY.md` cover the
   components that phase's tracker tasks touch.
5. Before writing any code, set that phase's Status in the tracker to
   🔶 In progress and update the "Current focus" line at the top.
6. Work only inside that phase's task list. If something would clearly help but
   belongs to a later phase, flag it to the user rather than quietly doing it —
   phases are scoped that way on purpose (see `01-PHASE_PLAN.md`'s ground rule).

### When ending a session (phase finished or not)

1. Check off every task actually completed in the tracker.
2. **Phase fully done:** flip its Status to ✅ Done, update "Current focus" to the
   next phase, and tell the user the exact next command ("start phase N+1").
3. **Phase not finished:** leave Status at 🔶 In progress and add a dated note
   describing precisely what's left / where you stopped — specific enough that a
   future session can resume cold from that note alone, whatever phrasing the
   user uses next time.
4. Add a dated note for any deviation from `01-PHASE_PLAN.md` (scope changed, a
   task split, something harder/easier than planned), and update that doc too if
   the plan itself needs to change — don't let the tracker and the plan drift
   apart.
