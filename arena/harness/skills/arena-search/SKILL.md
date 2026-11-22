---
name: arena-search
description: Look ahead before choosing a turn - a time-budgeted search over candidate turns against likely enemy replies, in plain Python. Read it before spending think time per tick.
---

# Searching ahead in Victris Arena

The strongest ladder bots think 200-400 ms a turn (`./replays scout` shows each one's `thinks ... ms`):
they simulate candidate turns before choosing. You can too: the ladder allows 1 s a turn and 300 s a
match (`obs["self"]["budget_ms_left"]` is what is left). `./eval` prints your mean and max per turn.

These lessons come from our Schness engine (a 4x4 chess variant with an alpha-beta search), adapted to
a game where both fighters move at once and you see only 8 tiles.

## The shape of a search

1. **Candidates.** Every turn is a movement (stay, one of `obs["moves"]`, or one of `obs["dashes"]`) plus
   an optional attack. Prune hard: only attacks whose weapon is ready and in range, only targets on or next
   to the enemy's likely tiles. 10-40 candidates is plenty.
2. **A forward model in Python.** You don't need the engine for the next few ticks:
   - incoming hits are already listed: `obs["danger"]` gives every tile and tick a visible projectile or
     frostburst area will hit (that is the expensive geometry, done for you);
   - your own shot: projectiles fly `speed` tiles a tick (rules.json), so a shot from distance d lands
     after about d/speed ticks: the enemy will have moved by then (lead it, or fire up close);
   - movement: one tile per `move_cooldown` ticks; dash `distance` tiles, invulnerable on its own tick;
   - the zone: `obs["zone"]` with `shrinks_in` and `next_radius`; outside it costs 5+ hp a tick;
   - repulsor: pushes adjacent fighters `push` tiles; a wall on the way is a wall slam (10 damage, 3 ticks stunned).
3. **The enemy's reply.** Moves are simultaneous, so for each of your candidates, score it against the
   enemy's plausible replies (its moves, a dash, its ready attacks) and keep the **worst** case
   (maximin), or a weighted average when you have a model of how it plays (`./replays scout` and the
   loss reports tell you its habits). Worst case is safe up close; average is better at range.
4. **Remember what the fog hides.** You see the enemy only within 8 tiles, and never its attack cooldowns.
   `skills/arena-search/tracker.py` (copy it into strategy.py) keeps its last known position (also guessed from
   shots and dashes that come out of the fog), how far it can have moved since, and when each of its abilities
   is ready again, from the attack events you saw. Checked over 40 games against the enemy's real state:
   frostburst/repulsor readiness right 100%, dash 98.7%, ranged 96.7%; while unseen, its true position was
   always inside the predicted reach, and within 2 tiles of the last known one 84% of the time. Use it to
   step in when its weapons are on cooldown, to keep cover when they are ready, and to search where it can be
   instead of patrolling blind.
5. **Evaluation** of the position a few ticks ahead: hp difference (yours minus theirs), damage about
   to land on each side, distance against the range you want, cover (walls between you and its shots),
   zone safety, cooldowns ready, pickups within reach, a wall behind the enemy (slam chances).
   Start with few terms; add one at a time and keep it only if `./eval` says so.

## Lessons that carried over from the Schness engine

- **Anytime, deadline-driven.** Search one tick ahead for every candidate, then two, then three, and stop
  when the time for this turn is up, keeping the best answer of the last *completed* depth. Never let a
  deeper pass that did not finish choose the move. Measure time with `time.perf_counter()`.
- **Spend time where it matters.** Most ticks need no search (enemy unseen, far away, nothing in flight).
  Search when the enemy is within ~5 tiles or something is about to hit; budget e.g. 300 ms there and
  near 0 elsewhere, and slow down when `budget_ms_left` gets low (600 ticks x 1 s is twice the budget).
- **Order candidates well.** Try last tick's best turn first, then dodges, then attacks: with a deadline,
  the good moves must be searched before time runs out.
- **Cache.** Positions repeat between ticks: a dict keyed on (your tile, enemy tile, cooldowns, hp) saves
  re-evaluating them. Clear it every tick or keep it small.
- **Prove a gain before keeping it.** In Schness, most clever ideas were slower or no stronger once
  measured. Change one thing, compare with `./eval` on the same games, keep it only if it wins more, and
  write the numbers in NOTES.md. A search that is slower but no stronger is worse: slower `./eval` runs
  leave you less time to improve.
