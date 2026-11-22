"""Enemy tracker for a Victris Arena bot: remember what the fog hides. Copy it into strategy.py (stdlib only).

The observation shows the enemy only within sight (8 tiles): its position, hp, dash cooldown, state and buffs,
never its attack cooldowns. This keeps, in match["enemy"]:
  pos, seen_tick     last known position and the tick it was seen (or guessed from a projectile it fired)
  exact              True when pos was seen this tick
  hp                 last known hp
  ready              tick each ability is ready again: "dash" from obs, attacks from the attack events we saw
                     (None = never seen used: assume ready)
Call track(obs, match) first thing in decide(). Then:
  reach(obs, match)          how many tiles the enemy may have moved since it was seen (Chebyshev radius)
  could_be_at(obs, match, p) whether tile p is within that reach of its last known position
  enemy_ready(obs, match, name, ticks_ahead=0)   whether it can use ranged/frostburst/repulsor/dash by then
Validated against the enemy's real state in benches (see arena/harness/skills/arena-search/SKILL.md).
"""


def _cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def track(obs, match):
    rules = match["rules"]
    me = obs["self"]["id"]
    e = match.setdefault("enemy", {"pos": None, "seen_tick": None, "exact": False, "hp": rules["stats"]["max_hp"],
                                   "ready": {"dash": None, "ranged": None, "frostburst": None, "repulsor": None}, "id": None})
    t = obs["tick"]
    e["exact"] = False
    if obs["enemies"]:
        en = obs["enemies"][0]
        e.update(pos=list(en["pos"]), seen_tick=t, exact=True, hp=en["hp"], id=en["id"])
        e["ready"]["dash"] = t + en["dash_cooldown"]
    cd = {k: v["cooldown"] for k, v in rules["attacks"].items()}
    for ev in obs.get("events") or []:
        who = ev.get("fighter", ev.get("owner"))
        if who is None or who == me:
            continue
        k = ev.get("kind")
        # events describe the tick that just resolved (t - 1): an ability used then is ready cooldown ticks later
        if k == "attack" and ev.get("attack") in cd:
            e["ready"][ev["attack"]] = t - 1 + cd[ev["attack"]]
        elif k == "dash":
            e["ready"]["dash"] = t - 1 + rules["dash"]["cooldown"]
            if not e["exact"] and ev.get("to"):
                e.update(pos=list(ev["to"]), seen_tick=t - 1)
        elif k == "projectile_spawn" and not e["exact"] and ev.get("pos"):
            # a shot appearing out of the fog starts next to its owner: a good guess of where it is
            e.update(pos=list(ev["pos"]), seen_tick=t - 1)
            if ev.get("attack") in cd:
                e["ready"][ev["attack"]] = t - 1 + cd[ev["attack"]]
        elif k == "area_spawn" and ev.get("attack") in cd:
            e["ready"][ev["attack"]] = t - 1 + cd[ev["attack"]]
        elif k == "repulse" and ev.get("attack", "repulsor") in cd:
            e["ready"]["repulsor"] = t - 1 + cd["repulsor"]
            if not e["exact"] and ev.get("pos"):
                e.update(pos=list(ev["pos"]), seen_tick=t - 1)
        elif k == "pickup" and not e["exact"] and ev.get("pos"):
            e.update(pos=list(ev["pos"]), seen_tick=t - 1)
    return e


def reach(obs, match):
    """Tiles the enemy may have covered since it was seen: a step per move_cooldown ticks, plus one dash if it had one."""
    e = match.get("enemy") or {}
    if e.get("seen_tick") is None:
        return None
    rules = match["rules"]
    dt = obs["tick"] - e["seen_tick"]
    steps = dt // max(1, rules["stats"]["move_cooldown"]) + (1 if dt else 0)
    dash_ready = e["ready"].get("dash")
    if dt and (dash_ready is None or dash_ready <= obs["tick"]):
        steps += rules["dash"]["distance"]
    return steps


def could_be_at(obs, match, p):
    r = reach(obs, match)
    e = match.get("enemy") or {}
    return r is None or _cheb(e["pos"], p) <= r


def enemy_ready(obs, match, name, ticks_ahead=0):
    """Can the enemy use `name` (ranged, frostburst, repulsor, dash) `ticks_ahead` ticks from now? Unknown = assume yes."""
    r = ((match.get("enemy") or {}).get("ready") or {}).get(name)
    return r is None or r <= obs["tick"] + ticks_ahead
