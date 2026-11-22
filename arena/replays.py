"""Ladder games from victrisarena.com: import them, study them, compare our bots with the best.

Every ladder match is public, with a replay; anyone may download each bot's newest 200 matches (30 days
back). A replay re-runs through the ladder's own match code (arena_sim.replay_trajectories), giving each
fighter's view tick by tick: what it saw and the turn it sent. From those we build scouting profiles.

    python -m arena.replays fetch --top 20 --limit 30     # the 20 highest-rated bots' recent matches (network)
    python -m arena.replays fetch --bot foot-soldier      # one bot's matches
    python -m arena.replays import ~/Downloads/*.jsonl.gz # replay files you downloaded from the site
    python -m arena.replays analyze                       # profiles.json + scouting.md from everything imported
    python -m arena.replays scout [BOT]                   # the scouting report (all bots, or one)
    python -m arena.replays list [--bot NAME]             # imported matches
    python -m arena.replays show MATCH [--fighter NAME] [--from T] [--to T]   # a match, tick by tick
    python -m arena.replays mine BOT.py [--vs ts-starter] [--games 20]        # our bot's profile, same measures

Only fetch and import write to arena/replays/; the rest only read (the lab's ./replays tool uses those).
"""
import argparse, collections, gzip, json, os, shutil, statistics, sys, time, warnings
from pathlib import Path

HERE = Path(os.environ.get('ARENA_REPLAYS_DIR') or Path(__file__).resolve().parent / 'replays')   # a bench points it at its frozen set
CACHE = HERE / 'cache'
PROFILES = HERE / 'profiles.json'
SCOUTING = HERE / 'scouting.md'
LADDER = 'https://victrisarena.com'
ATTACKS = ('ranged', 'frostburst', 'repulsor')


# ---------- import ----------

def _record_from_header(text, mid):
    h = json.loads(text.split('\n', 1)[0])
    stats = json.loads(text.rstrip('\n').rsplit('\n', 1)[-1]) if text.strip() else {}
    return dict(id=mid, map=(h.get('map_data') or {}).get('name'), seed=h.get('seed'), imported=True,
                bots=[dict(name=b['name'], version=b.get('version'), fighter=b['id']) for b in h.get('bots', [])],
                winner=stats.get('winner'), release=h.get('release'))


def fetch(top=0, bot=None, limit=30, min_rating=None):
    """Download ladder matches into the cache (only new ones); returns how many matches were added."""
    from arena_sim import download_replays
    CACHE.mkdir(parents=True, exist_ok=True)
    names = [bot] if bot else []
    if top and not bot:
        import urllib.request
        req = urllib.request.Request(f'{LADDER}/api/leaderboard', headers={'User-Agent': 'arena-sim'})
        board = json.loads(urllib.request.urlopen(req, timeout=60).read())
        (HERE / 'leaderboard.json').write_text(json.dumps(dict(fetched=time.strftime('%F %T'), bots=board), indent=0))
        names = [r['name'] for r in sorted(board, key=lambda r: -r['rating'])[:top]]
    added = 0
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        for name in names or [None]:
            kw = dict(bot=name, limit=limit, cache=str(CACHE))
            if min_rating is not None and not name:
                kw['min_rating'] = min_rating
            for match, _ in download_replays(LADDER, **kw):
                rec = CACHE / f"{match['id']}.json"
                if not rec.exists():
                    rec.write_text(json.dumps(match))
                    added += 1
    return added


def import_files(paths):
    CACHE.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in map(Path, paths):
        data = p.read_bytes()
        text = gzip.decompress(data).decode() if data[:2] == b'\x1f\x8b' else data.decode()
        mid = 'local-' + p.name.split('.')[0]
        (CACHE / f'{mid}.jsonl.gz').write_bytes(gzip.compress(text.encode()))
        (CACHE / f'{mid}.json').write_text(json.dumps(_record_from_header(text, mid)))
        n += 1
    return n


def matches(bot=None):
    out = []
    for f in sorted(CACHE.glob('*.json')):
        m = json.loads(f.read_text())
        if (CACHE / f"{m['id']}.jsonl.gz").exists() and (not bot or any(b['name'] == bot for b in m['bots'])):
            out.append(m)
    return sorted(out, key=lambda m: m.get('started') or '', reverse=True)


def winner_name(m):
    """Ladder records name the winner by bot id; imported files by fighter number."""
    w = m.get('winner')
    return next((b['name'] for b in m['bots'] if w is not None and w in (b.get('id'), b.get('fighter'))), 'draw')


def trajectories(mid):
    from arena_sim import replay_trajectories
    return replay_trajectories(str(CACHE / f'{mid}.jsonl.gz'))


# ---------- measures ----------

def cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def measure(traj, me):
    """What fighter `me` did in one match, from its trajectory (its observations and turns)."""
    steps, end = traj['steps'], traj.get('end') or {}
    n = max(1, len(steps))
    seen = dists = 0
    shots = collections.Counter()
    hits = collections.Counter()
    dashes = dodge_dashes = in_danger = idle = threatened = stayed = 0
    ms = []
    first_pickup = None
    zone_out = 0
    for s in steps:
        o, t = s['observation'], s['turn'] or {}
        if 'ms' in t:
            ms.append(t['ms'])
        en = (o.get('enemies') or [None])[0]
        pos = o['self']['pos']
        if en:
            seen += 1
            dists += cheb(pos, en['pos'])
        danger_now = any(d['tile'] == pos and d.get('owner') != me for d in o.get('danger') or [])
        in_danger += danger_now
        soon = any(d['tile'] == pos and d.get('owner') != me and 0 <= d['tick'] - o['tick'] <= 2 for d in o.get('danger') or [])
        if soon and o['self'].get('state') == 'idle':
            threatened += 1
            stayed += not (t.get('move') or t.get('dash'))
        a = t.get('attack')
        if a:
            shots[a.get('name')] += 1
        if t.get('dash'):
            dashes += 1
            dodge_dashes += danger_now
        if not (a or t.get('move') or t.get('dash')):
            idle += 1
        z = o.get('zone') or {}
        if z.get('center') and cheb(pos, z['center']) > z.get('radius', 99):
            zone_out += 1
        for e in o.get('events') or []:
            if e.get('kind') == 'hit' and e.get('attacker') == me:
                hits[e.get('attack')] += 1
            if e.get('kind') == 'pickup' and e.get('fighter') == me and first_pickup is None:
                first_pickup = o['tick']
    st = {f['id']: f for f in ((end.get('stats') or {}).get('fighters') or [])}
    mine = st.get(me, {})
    return dict(result=end.get('result'), reason=end.get('reason'), ticks=len(steps), map=(end.get('stats') or {}).get('map'),
                visible=seen / n, mean_range=dists / seen if seen else None,
                per100={k: 100 * shots[k] / n for k in ATTACKS}, dash_per100=100 * dashes / n,
                ranged_accuracy=hits['ranged'] / shots['ranged'] if shots['ranged'] else None,
                frost_accuracy=hits['frostburst'] / shots['frostburst'] if shots['frostburst'] else None,
                dodge_share=dodge_dashes / dashes if dashes else None, danger_share=in_danger / n, idle_share=idle / n,
                stayed_under_fire=stayed / threatened if threatened else None,
                first_pickup=first_pickup, zone_out_share=zone_out / n, mean_ms=statistics.mean(ms) if ms else None,
                dealt=mine.get('damage_dealt'), taken=mine.get('damage_taken'),
                dealt_by=mine.get('dealt_by_source') or {}, taken_by=mine.get('taken_by_source') or {},
                pickups=mine.get('pickups') or {})


def aggregate(rows):
    """Average a bot's per-match measures into its profile."""
    def avg(key, sub=None):
        vals = [(r[key] or {}).get(sub) if sub else r[key] for r in rows]
        vals = [v for v in vals if v is not None]
        return round(statistics.mean(vals), 3) if vals else None
    def sum_src(key):
        c = collections.Counter()
        for r in rows:
            c.update(r[key] or {})
        tot = sum(c.values()) or 1
        return {k: round(v / tot, 2) for k, v in c.most_common()}
    res = collections.Counter(r['result'] for r in rows)
    return dict(matches=len(rows), win_rate=round(res['win'] / len(rows), 3), results=dict(res),
                reasons=dict(collections.Counter(r['reason'] for r in rows)),
                mean_range=avg('mean_range'), visible=avg('visible'),
                shots_per100={k: avg('per100', k) for k in ATTACKS}, dash_per100=avg('dash_per100'),
                ranged_accuracy=avg('ranged_accuracy'), frost_accuracy=avg('frost_accuracy'),
                dodge_share=avg('dodge_share'), danger_share=avg('danger_share'), idle_share=avg('idle_share'),
                stayed_under_fire=avg('stayed_under_fire'),
                first_pickup=avg('first_pickup'), zone_out_share=avg('zone_out_share'), mean_ms=avg('mean_ms'),
                dealt=avg('dealt'), taken=avg('taken'), dealt_mix=sum_src('dealt_by'), taken_mix=sum_src('taken_by'),
                win_rate_by_map={m: round(sum(r['result'] == 'win' for r in rows if r['map'] == m) / max(1, sum(r['map'] == m for r in rows)), 2)
                                 for m in sorted({r['map'] for r in rows if r['map']})})


def analyze():
    """Every imported match, measured for both fighters; profiles per bot, strongest first."""
    rows = collections.defaultdict(list)
    ratings, skipped = {}, 0
    for m in matches():
        try:
            tr = trajectories(m['id'])
        except ValueError:
            skipped += 1   # another engine release than this simulator's
            continue
        for b in m['bots']:
            if b['fighter'] in tr:
                rows[b['name']].append(measure(tr[b['fighter']], b['fighter']))
                if b.get('rating_before'):
                    ratings[b['name']] = b['rating_before']
    board = {}
    if (HERE / 'leaderboard.json').exists():
        board = {r['name']: r for r in json.loads((HERE / 'leaderboard.json').read_text())['bots']}
    prof = {}
    for name, rs in rows.items():
        p = aggregate(rs)
        p['rating'] = (board.get(name) or {}).get('rating') or ratings.get(name)
        p['house'] = (board.get(name) or {}).get('house')
        prof[name] = p
    prof = dict(sorted(prof.items(), key=lambda kv: -(kv[1]['rating'] or 0)))
    out = dict(updated=time.strftime('%F %T'), matches=len(matches()), skipped=skipped, bots=prof)
    PROFILES.write_text(json.dumps(out, indent=1))
    SCOUTING.write_text(scouting_md(out))
    return out


def _pct(x):
    return '–' if x is None else f'{100 * x:.0f}%'


def describe(name, p):
    s = p['shots_per100']
    return (f"### {name}" + (f" (rating {p['rating']})" if p.get('rating') else '') + "\n"
            f"- {p['matches']} matches, {_pct(p['win_rate'])} won; ends: {p['reasons']}; by map: {p['win_rate_by_map']}\n"
            f"- range: keeps the enemy {p['mean_range'] or 0:.1f} tiles away on average, sees it {_pct(p['visible'])} of the time\n"
            f"- per 100 ticks: {s['ranged'] or 0:.1f} shots, {s['frostburst'] or 0:.1f} frostbursts, {s['repulsor'] or 0:.1f} repulsors, {p['dash_per100'] or 0:.1f} dashes\n"
            f"- accuracy: shots {_pct(p['ranged_accuracy'])}, frostbursts {_pct(p['frost_accuracy'])}; dashes used to dodge: {_pct(p['dodge_share'])}\n"
            f"- stands on a tile about to be hit {_pct(p['danger_share'])} of ticks; when a hit is 0-2 ticks away and it is free to act, it stays put {_pct(p.get('stayed_under_fire'))} of the time\n"
            f"- idle {_pct(p['idle_share'])} of ticks; outside the zone {_pct(p['zone_out_share'])}\n"
            f"- damage per match: deals {p['dealt'] or 0:.0f} ({p['dealt_mix']}), takes {p['taken'] or 0:.0f} ({p['taken_mix']})\n"
            f"- first pickup at tick {p['first_pickup'] or '–'}; thinks {p['mean_ms'] or 0:.0f} ms a turn on average\n")


def scouting_md(out, only=None):
    bots = out['bots'] if not only else {k: v for k, v in out['bots'].items() if k == only}
    head = (f"# Scouting report: Victris Arena ladder\n\nFrom {out['matches']} imported ladder matches ({out['updated']}). "
            "Each bot's measures are averaged over its matches; strongest first. Compare with your own bot: "
            "`./replays mine` prints the same measures for strategy.py.\n\n")
    return head + '\n'.join(describe(n, p) for n, p in bots.items())


def show(mid, fighter=None, t0=0, t1=None):
    """A match tick by tick from one fighter's view: positions, HP, what it did, what happened."""
    m = json.loads((CACHE / f'{mid}.json').read_text())
    tr = trajectories(mid)
    names = {b['fighter']: b['name'] for b in m['bots']}
    me = next((b['fighter'] for b in m['bots'] if fighter in (b['name'], str(b['fighter']))), m['bots'][0]['fighter'])
    head = f"match {mid} on {m.get('map')}: {' vs '.join(f'{names[f]} (fighter {f})' for f in sorted(names))}; view of {names[me]}"
    return head + '\n' + show_trajectory(tr, me, names, t0, t1)


def show_trajectory(tr, me, names, t0=0, t1=None):
    lines = []
    for s in tr[me]['steps']:
        o, t = s['observation'], s['turn'] or {}
        if o['tick'] < t0 or (t1 is not None and o['tick'] > t1):
            continue
        en = (o.get('enemies') or [None])[0]
        did = ', '.join(x for x in [t.get('move') and 'move ' + t['move'], t.get('dash') and 'DASH ' + t['dash'],
                                    t.get('attack') and f"{t['attack']['name']}->{t['attack'].get('target', '')}"] if x) or 'idle'
        ev = '; '.join(f"{e['kind']}" + (f" {names.get(e.get('attacker'), '')}->{names.get(e.get('target'), '')} {e.get('attack', '')} {e.get('damage', '')}" if e['kind'] == 'hit'
                                          else f" {names.get(e.get('attacker'), '')}->{names.get(e.get('target'), '')} {e.get('damage', '')}" if e['kind'] == 'wall_slam'
                                          else f" {names.get(e.get('fighter'), '')} {e.get('pickup') or e.get('ticks') or ''}" if e['kind'] in ('pickup', 'stunned', 'death') else '')
                       for e in o.get('events') or [] if e['kind'] in ('hit', 'pickup', 'wall_slam', 'stunned', 'death', 'zone_shrink'))
        lines.append(f"t{o['tick']:>3} me {tuple(o['self']['pos'])} hp {o['self']['hp']:>3}" +
                     (f" | foe {tuple(en['pos'])} hp {en['hp']:>3} d={cheb(o['self']['pos'], en['pos'])}" if en else ' | foe unseen') +
                     f" | {did}" + (f" | {ev}" if ev else ''))
    end = tr[me].get('end') or {}
    lines.append(f"end: {end.get('result')} ({end.get('reason')}) at tick {end.get('final_tick')}")
    return '\n'.join(lines)


def mine(bot, vs='ts-starter', games=20, seed0=3000):
    """Our bot's profile, measured the same way, from offline games against `vs`."""
    from arena_sim import replay_trajectories
    from arena import runner, league
    pool = league.pool()
    res = runner.play(bot, pool.get(vs, vs), games, seed0, replays=True)
    rows = []
    for r in res['replays']:
        tr = replay_trajectories(r['text'])
        rows.append(measure(tr[r['me']], r['me']))
    return aggregate(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('fetch'); f.add_argument('--top', type=int, default=0); f.add_argument('--bot'); f.add_argument('--limit', type=int, default=30)
    f.add_argument('--min-rating', type=float)
    i = sub.add_parser('import'); i.add_argument('files', nargs='+')
    sub.add_parser('analyze')
    s = sub.add_parser('scout'); s.add_argument('bot', nargs='?')
    l = sub.add_parser('list'); l.add_argument('--bot')
    sh = sub.add_parser('show'); sh.add_argument('match'); sh.add_argument('--fighter'); sh.add_argument('--from', dest='t0', type=int, default=0)
    sh.add_argument('--to', dest='t1', type=int)
    mi = sub.add_parser('mine'); mi.add_argument('bot'); mi.add_argument('--vs', default='ts-starter'); mi.add_argument('--games', type=int, default=20)
    a = p.parse_args()
    if a.cmd == 'fetch':
        print(f'{fetch(a.top, a.bot, a.limit, a.min_rating)} new matches; {len(matches())} in all. Run analyze next.')
    elif a.cmd == 'import':
        print(f'{import_files(a.files)} replays imported. Run analyze next.')
    elif a.cmd == 'analyze':
        out = analyze()
        print(f"{len(out['bots'])} bots profiled from {out['matches']} matches ({out['skipped']} from another engine release skipped): {SCOUTING}")
    elif a.cmd == 'scout':
        if not PROFILES.exists():
            sys.exit('no profiles yet: the lab imports ladder matches and runs analyze')
        print(scouting_md(json.loads(PROFILES.read_text()), a.bot))
    elif a.cmd == 'list':
        for m in matches(a.bot):
            bots = ' vs '.join(f"{b['name']}" + (f" ({b['rating_before']})" if b.get('rating_before') else '') for b in m['bots'])
            win = winner_name(m)
            print(f"{m['id']}  {m.get('map', ''):<11} {bots}; winner {win}")
    elif a.cmd == 'show':
        print(show(a.match, a.fighter, a.t0, a.t1))
    elif a.cmd == 'mine':
        print(describe(f'your bot vs {a.vs}', dict(mine(a.bot, a.vs, a.games), rating=None)))


if __name__ == '__main__':
    sys.exit(main())
