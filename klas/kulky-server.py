#!/usr/bin/env python3
# © 2026 Юрій Падляк (Yuriy Padlyak). «Класні ігри».
# Ліцензія CC BY-NC-SA 4.0 (див. LICENSE). Продаж або платне поширення лише за окремою ліцензією автора.
"""Класний сервер для апки «Кулькосвіт» (блоковий світ з кульками, 0 клас).

Запуск на вчительському ноутбуці:
    python3 kulky-server.py            # порт 8000
    python3 kulky-server.py 8080       # інший порт
    python3 kulky-server.py --reset    # новий світ і новий клас

Діти відкривають   http://<IP ноутбука>:8000
Проєктор:          http://localhost:8000/board   (кнопки вчителя працюють лише тут)
Лише стандартна бібліотека Python, нічого не треба встановлювати.

Усі бачать один спільний світ: кульки, блоки й звіряток одне одного.
Фізика рахується тут, на сервері, а пристрої лише малюють.
"""
import json
import math
import os
import random
import socket
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "kulky.html")
DATA = os.path.join(HERE, "kulky-world.json")
CERT_DIR = os.path.join(HERE, ".house-cert")
tls_ctx = None

# ---------------------------------------------------------------- world
W, H = 48, 27
AIR = "."
UNBREAKABLE = set("BC")
PLACEABLE = set("GSWJAT")  # grass, stone, planks(wood), jelly, glass(A), tnt
REST = {"J": 0.92}  # jelly bounces
FRICTION = {"A": 0.995}
R = 0.42  # ball radius in tiles
GRAV = 26.0
VMAX = 11.5  # < R per tick at 30 Hz, so balls never tunnel through a block
TICK = 1 / 30
SUBSTEPS = 2
FRAME_EVERY = 3  # 10 frames per second to the devices
CAP_TOTAL = 360  # all balls in the world (about 12 each for 25 children)
PARTY_BALLS = 110
RAINBOW = 12  # ball colour of party balls
TEACHER_AV = 12


def _blank():
    g = [[AIR] * W for _ in range(H)]
    for x in range(W):
        g[H - 1][x] = "B"
    return g


def _ground(g, surface):
    """surface(x) -> top row of the ground in column x (or None for no ground)."""
    for x in range(W):
        top = surface(x)
        if top is None:
            continue
        for y in range(top, H - 1):
            g[y][x] = "G" if y == top else ("D" if y < top + 3 else "S")


def _chest(g, x0, x1, y0, y1):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            g[y][x] = "C"


def _pegs(g, rows, x0, x1, gap=4):
    for k, y in enumerate(rows):
        for x in range(x0 + (0 if k % 2 == 0 else gap // 2), x1, gap):
            if g[y][x] == AIR:
                g[y][x] = "S"


def _tree(g, tx):
    top = next(y for y in range(H) if g[y][tx] != AIR)
    for y in range(top - 4, top):
        g[y][tx] = "W"
    for y in range(top - 7, top - 3):
        for x in range(tx - 2, tx + 3):
            if 0 <= x < W and g[y][x] == AIR and not (y == top - 7 and abs(x - tx) == 2):
                g[y][x] = "L"


def _row(g, y, x0, x1, ch):
    for x in range(x0, x1 + 1):
        g[y][x] = ch


def _col(g, x, y0, y1, ch):
    for y in range(y0, y1 + 1):
        g[y][x] = ch


def level_valley():  # 1: wide chest in an open valley
    g = _blank()
    _ground(g, lambda x: None if abs(x - 23.5) < 2 else 25 - int(round((abs(x - 23.5) - 1.5) * 0.42)))
    _chest(g, 22, 25, 24, 25)
    _pegs(g, (9, 12, 15, 18), 13, 36)
    for x in (1, 2, 45, 46):
        g[9][x] = "J"
    _row(g, 6, 8, 11, "A")
    _row(g, 6, 36, 39, "A")
    _tree(g, 6)
    _tree(g, 41)
    return g


def level_mountain():  # 2: a mountain in the middle, the chest hides on the right
    g = _blank()

    def surf(x):
        if 33 <= x <= 35:
            return None
        if x < 33:
            hill = 13 + int(abs(x - 20) * 0.9)
            return min(25 - int((33 - x) * 0.12), hill) if 12 <= x <= 28 else 25 - int((33 - x) * 0.12)
        return 25 - int((x - 35) * 0.5)
    _ground(g, surf)
    _chest(g, 33, 35, 24, 25)
    _pegs(g, (8, 11, 14, 17), 29, 46, 4)
    _row(g, 10, 2, 6, "J")
    _tree(g, 20)
    _tree(g, 44)
    return g


def level_cave():  # 3: the chest sits in a cave, balls get in through two holes in the roof
    g = _blank()
    _ground(g, lambda x: None if abs(x - 23.5) < 1 else 25 - int(round((abs(x - 23.5) - 0.5) * 0.3)))
    _chest(g, 23, 24, 24, 25)
    for x in range(8, 40):
        if 13 <= x <= 16 or 31 <= x <= 34:
            continue  # the holes
        d = min(abs(x - 14.5), abs(x - 32.5))  # roof climbs away from each hole
        top = 17 - min(4, int(d * 0.45))
        for y in range(top, 18):
            g[y][x] = "G" if y == top else "S"
    _pegs(g, (5, 8), 6, 42, 4)
    _row(g, 12, 1, 3, "J")
    _row(g, 12, 44, 46, "J")
    return g


def level_island():  # 4: a small chest on a high pillar, glass funnel above it
    g = _blank()
    _ground(g, lambda x: 25 - int(abs(x - 23.5) * 0.25))
    for x in range(22, 26):
        _col(g, x, 15, 24, "S")
    _chest(g, 23, 24, 13, 14)
    g[14][22] = g[14][25] = "S"
    for k in range(2):  # a short glass funnel
        g[6 + k][16 + k] = "A"
        g[6 + k][31 - k] = "A"
    _pegs(g, (4,), 8, 40, 6)
    _row(g, 18, 4, 7, "J")
    _row(g, 18, 40, 43, "J")
    _tree(g, 10)
    _tree(g, 37)
    return g


def level_fortress():  # 5: a fortress with one narrow door on top
    g = _blank()
    _ground(g, lambda x: 25 - int(round(abs(x - 23.5) * 0.2)))
    for x in range(20, 28):
        for y in range(18, 25):
            g[y][x] = AIR
    _chest(g, 23, 24, 24, 25)
    _col(g, 20, 17, 25, "S")
    _col(g, 27, 17, 25, "S")
    _row(g, 17, 20, 27, "S")
    g[17][23] = g[17][24] = AIR
    _row(g, 25, 21, 22, "J")
    _row(g, 25, 25, 26, "J")
    _pegs(g, (5, 8, 11, 14), 8, 40, 3)
    return g


# goal = balls the collectors must put in the chest, time = seconds
LEVELS = [
    {"build": level_valley, "goal": 60, "time": 75},
    {"build": level_mountain, "goal": 100, "time": 90},
    {"build": level_cave, "goal": 100, "time": 120},
    {"build": level_island, "goal": 180, "time": 100},
    {"build": level_fortress, "goal": 150, "time": 150},
]
TOOLS = {0: set("bGWJd"), 1: set("SATd"), None: set("bGSWJATd")}  # collectors, troublemakers, free play


def build_level(i):
    return ["".join(r) for r in LEVELS[i]["build"]()]


def fresh_world():
    return build_level(level)


# balls are born only in the sky band at the top and fall from there,
# so a tap right on the chest is no shortcut and walls really matter
SKY = 3


lock = threading.Lock()
grid = []  # list of H strings, mutated via set_tile
grid_ver = 0
balls = []  # [x, y, vx, vy, id, colour, owner_n, sleep_ticks]
next_id = 1
fuses = {}  # (x, y) -> explode time
temp = {}  # (x, y) -> time a troublemaker's block crumbles
TEMP_LIFE = 15
players = {}  # pid -> {"n", "av", "seen", "x", "y", "t", "tokens", "last", "got"}
events = []  # cosmetic events for the next frame
chest = 0
parties = 0
level = 0
state = "ready"  # ready -> count -> play -> won/lost -> count (next or same level) ... -> done
state_until = 0.0
ends = 0.0
party_left = 0
party_until = 0.0
frozen = False
muted = False
session = ""
dirty = False

frame_cv = threading.Condition()
frame = {"seq": 0, "delta": b"", "full": b"", "grid": b"", "gv": -1}
last_sent = {}  # ball id -> (x, y) at the last published frame


def new_session():
    return os.urandom(6).hex()


def set_tile(x, y, ch):
    global grid_ver, dirty
    row = grid[y]
    grid[y] = row[:x] + ch + row[x + 1:]
    grid_ver += 1
    dirty = True
    for b in balls:  # something changed: let resting balls fall again
        b[7] = 0


def tile(x, y):
    if x < 0 or x >= W or y >= H:
        return "B"
    if y < 0:
        return AIR
    return grid[y][x]


def load():
    global grid, session, level
    try:
        with open(DATA, encoding="utf-8") as f:
            d = json.load(f)
        g = d["grid"]
        assert len(g) == H and all(len(r) == W for r in g)
        grid, session = list(g), d.get("session") or new_session()
        level = max(0, min(len(LEVELS) - 1, int(d.get("level", 0))))
    except (OSError, ValueError, TypeError, KeyError, AssertionError):
        grid, session = build_level(0), new_session()


def persist():
    global dirty
    dirty = False
    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"session": session, "grid": grid, "level": level}, f)
    os.replace(tmp, DATA)


def add_ball(x, y, vx, vy, colour, owner):
    global next_id
    balls.append([x, y, vx, vy, next_id, colour, owner, 0, False])
    next_id += 1


def quota():
    active = sum(1 for p in players.values() if time.time() - p["seen"] < 60) or 1
    return max(10, min(40, (CAP_TOTAL - 60) // active))


def trim(owner):
    """A child's new balls push out only that child's own oldest balls."""
    mine = [b for b in balls if b[6] == owner] if owner != -1 else []
    extra = len(mine) - quota()
    if extra > 0:
        drop = {id(b) for b in mine[:extra]}
        balls[:] = [b for b in balls if id(b) not in drop]
    if len(balls) > CAP_TOTAL:  # still too many (party balls first, then oldest)
        party = [b for b in balls if b[5] == RAINBOW]
        over = len(balls) - CAP_TOTAL
        drop = {id(b) for b in (party + balls)[:over]}
        balls[:] = [b for b in balls if id(b) not in drop]


def explode(cx, cy):
    events.append(["x", cx, cy])
    for y in range(cy - 2, cy + 3):
        for x in range(cx - 2, cx + 3):
            if 0 <= x < W and 0 <= y < H and (x - cx) ** 2 + (y - cy) ** 2 <= 5.5:
                t = grid[y][x]
                if t != AIR and t not in UNBREAKABLE:
                    if t == "T" and (x, y) != (cx, cy):
                        fuses.setdefault((x, y), time.time() + 0.25)  # chain reaction
                        continue
                    fuses.pop((x, y), None)
                    set_tile(x, y, AIR)
    px, py = cx + 0.5, cy + 0.5
    for b in balls:
        dx, dy = b[0] - px, b[1] - py
        d = math.hypot(dx, dy)
        if d < 6:
            k = 14 / (d + 0.6)
            b[2] += dx / (d + 1e-6) * k
            b[3] += dy / (d + 1e-6) * k - 3
            b[7] = 0


def step():
    """One physics tick. Called with the lock held."""
    global chest, parties, party_left, party_until
    now = time.time()
    flow(now)
    for pos, t in list(temp.items()):  # troublemakers' blocks do not last forever
        if now >= t:
            del temp[pos]
            ch = tile(*pos)
            if ch in "SA":
                set_tile(pos[0], pos[1], AIR)
                events.append(["d", pos[0], pos[1], ch])
    for pos, t in list(fuses.items()):
        if now >= t and tile(*pos) == "T":
            del fuses[pos]
            explode(*pos)
        elif tile(*pos) != "T":
            del fuses[pos]
    if party_left > 0:
        for _ in range(min(party_left, 4)):
            add_ball(random.uniform(1, W - 1), -0.5, random.uniform(-2, 2), random.uniform(0, 3), RAINBOW, -1)
            party_left -= 1
        trim(-1)

    collected = []
    for _ in range(SUBSTEPS):
        substep(TICK / SUBSTEPS, collected)
        balls[:] = [b for b in balls if b[1] < 1e8]
    after_collect(collected)


def substep(dt, collected):
    r = R
    rr = r * r
    gr = grid
    # balls: move, then collide with blocks
    for b in balls:
        if b[7] > 20:
            continue  # asleep
        x, y, vx, vy = b[0], b[1], b[2], b[3] + GRAV * dt
        sp = vx * vx + vy * vy
        if sp > VMAX * VMAX:
            k = VMAX / math.sqrt(sp)
            vx *= k
            vy *= k
        x += vx * dt
        y += vy * dt
        touched = False
        gone = False
        for ty in range(int(y - r), int(y + r) + 1):
            for tx in range(int(math.floor(x - r)), int(x + r) + 1):
                if tx < 0 or tx >= W or ty >= H:
                    t = "B"
                elif ty < 0:
                    continue  # open sky above the world, but the side walls go up forever
                else:
                    t = gr[ty][tx]
                    if t == ".":
                        continue
                cx = tx if x < tx else (tx + 1 if x > tx + 1 else x)
                cy = ty if y < ty else (ty + 1 if y > ty + 1 else y)
                dx, dy = x - cx, y - cy
                d2 = dx * dx + dy * dy
                if d2 >= rr:
                    continue
                if t == "C":
                    gone = True
                    break
                if d2 > 1e-9:
                    d = math.sqrt(d2)
                    nx, ny, pen = dx / d, dy / d, r - d
                else:  # centre inside a block (one was placed on it): pop out on top
                    nx, ny, pen = 0.0, -1.0, y - ty + r
                x += nx * pen
                y += ny * pen
                vn = vx * nx + vy * ny
                if vn < 0:
                    e = REST.get(t, 0.32)
                    vx -= (1 + e) * vn * nx
                    vy -= (1 + e) * vn * ny
                    f = FRICTION.get(t, 0.985)
                    tvx, tvy = -ny, nx
                    vt = vx * tvx + vy * tvy
                    vx -= vt * (1 - f) * tvx
                    vy -= vt * (1 - f) * tvy
                    if ny < -0.9 and vn < -1:
                        vx += random.uniform(-0.6, 0.6)  # nobody balances on a peg forever
                    if t == "J" and ny < -0.5 and vy > -9:
                        vy = -min(VMAX, max(9.0, -vy))  # jelly always gives a big jump
                touched = True
            if gone:
                break
        if gone:
            collected.append(b)
            b[1] = 1e9
            continue
        if y > H + 2 or x < -2 or x > W + 2:
            b[1] = 1e9
            continue
        b[0], b[1], b[2], b[3] = x, y, vx, vy
        b[8] = touched

    # balls against each other (spatial hash, 1 tile cells)
    cells = {}
    for b in balls:
        cells.setdefault(int(b[0]) + int(b[1]) * 64, []).append(b)
    d_min = 2 * r
    dmin2 = d_min * d_min
    for _ in range(3):  # a few passes so piles do not squash
        for key, lst in cells.items():
            for nb in (key, key + 1, key + 63, key + 64, key + 65):
                other = cells.get(nb)
                if not other:
                    continue
                same = nb == key
                for i, a in enumerate(lst):
                    for c in (other[i + 1:] if same else other):
                        sa, sc = a[7] > 20, c[7] > 20
                        if sa and sc:
                            continue
                        dx, dy = c[0] - a[0], c[1] - a[1]
                        d2 = dx * dx + dy * dy
                        if d2 >= dmin2 or d2 < 1e-12:
                            continue
                        d = math.sqrt(d2)
                        nx, ny = dx / d, dy / d
                        if ny > 0.3:
                            a[8] = True  # resting on the ball below counts as support
                        elif ny < -0.3:
                            c[8] = True
                        vn = (c[2] - a[2]) * nx + (c[3] - a[3]) * ny
                        if (sa or sc) and vn < -2.5:
                            a[7] = c[7] = 0  # a hard hit wakes the sleeping ball
                            sa = sc = False
                        # a sleeping ball is a solid support: only the awake one moves
                        ka, kc = (0.0, 1.0) if sa else ((1.0, 0.0) if sc else (0.5, 0.5))
                        pen = d_min - d
                        a[0] -= nx * pen * ka
                        a[1] -= ny * pen * ka
                        c[0] += nx * pen * kc
                        c[1] += ny * pen * kc
                        if vn < 0:
                            j = -1.3 * vn
                            a[2] -= j * nx * ka
                            a[3] -= j * ny * ka
                            c[2] += j * nx * kc
                            c[3] += j * ny * kc

    # a ball that is held up and barely moving falls asleep (saves cpu and wifi)
    for b in balls:
        if b[7] > 20:
            continue
        if b[8] and b[2] * b[2] + b[3] * b[3] < 0.6:
            b[7] += 1
            b[2] *= 0.9
            b[3] *= 0.9
        else:
            b[7] = 0


def after_collect(collected):
    global chest
    if not collected:
        return
    by_n = {p["n"]: p for p in players.values()}
    for b in collected:
        p = by_n.get(b[6])
        counts = state == "play" and p is not None and p.get("team") == 0
        if counts:
            chest += 1
            p["got"] += 1
        events.append(["c", b[6], b[5], 1 if counts else 0])


def active_players():
    now = time.time()
    return [p for p in players.values() if now - p["seen"] < 90 and p["av"] != TEACHER_AV]


def shuffle_teams():
    ps = active_players()
    random.shuffle(ps)
    for p in players.values():
        p["team"] = None
    for i, p in enumerate(ps):
        p["team"] = i % 2


def give_team(p):
    """Late arrival: join the smaller team (a coin toss when equal)."""
    if p["av"] == TEACHER_AV or state in ("ready", "done"):
        p["team"] = None
        return
    sizes = [0, 0]
    for q in active_players():
        if q is not p and q.get("team") in (0, 1):
            sizes[q["team"]] += 1
    p["team"] = random.choice((0, 1)) if sizes[0] == sizes[1] else (0 if sizes[0] < sizes[1] else 1)


def start_level(i):
    global level, state, state_until, chest, balls, party_left
    level = i
    grid[:] = build_level(i)
    set_tile(0, 0, grid[0][0])  # bump the version so every device redraws
    balls, party_left, chest = [], 0, 0
    fuses.clear()
    temp.clear()
    for p in players.values():
        p["got"] = 0
    shuffle_teams()
    state, state_until = "count", time.time() + 4
    events.append(["L", i])
    persist()


def flow(now):
    """Level timer and what happens when it is won or lost."""
    global state, state_until, ends, party_left, party_until
    if state == "count" and now >= state_until:
        state, ends = "play", now + LEVELS[level]["time"]
    elif state == "play":
        if frozen:
            ends += TICK  # «руки вгору» pauses the clock
        if chest >= LEVELS[level]["goal"]:
            state, state_until = "won", now + 7
            party_left += PARTY_BALLS
            party_until = now + 6
            events.append(["W", 0])
        elif now >= ends:
            state, state_until = "lost", now + 7
            events.append(["W", 1])
    elif state in ("won", "lost") and now >= state_until:
        if state == "won" and level + 1 >= len(LEVELS):
            state = "done"
            party_left += PARTY_BALLS * 2
            party_until = now + 10
            events.append(["D"])
            for p in players.values():
                p["team"] = None
        else:
            start_level(level + 1 if state == "won" else level)


def publish():
    """Encode the frame once; every device thread sends the same bytes."""
    global last_sent
    cur = {}
    delta, full = [], []
    for b in balls:
        x, y = int(b[0] * 100), int(b[1] * 100)
        cur[b[4]] = (x, y)
        full += (b[4], x, y, b[5])
        old = last_sent.get(b[4])
        if old is None or abs(old[0] - x) + abs(old[1] - y) > 3:
            delta += (b[4], x, y, b[5])
        else:
            cur[b[4]] = old
    removed = [i for i in last_sent if i not in cur]
    last_sent = cur
    now = time.time()
    act_ = active_players()
    who = [[p["n"], p["av"], round(p["x"], 2), round(p["y"], 2)]
           for p in players.values() if now - p["t"] < 4]
    rank = sorted(([p["av"], p["got"]] for p in act_ if p["got"]),
                  key=lambda a: -a[1])[:24]
    teams = [[p["n"], p["av"], p.get("team")] for p in act_]
    left = int(max(0, ends - now) * 1000) if state == "play" else None
    common = {"k": chest, "goal": LEVELS[level]["goal"], "lv": level, "nl": len(LEVELS), "st": state,
              "tl": left, "tt": LEVELS[level]["time"] * 1000,
              "cd": int(max(0, state_until - now) * 1000) if state == "count" else None, "tm": teams, "pt": parties, "fz": frozen, "mu": muted, "ss": session,
              "gv": grid_ver, "p": who, "e": events[:], "party": party_until > now,
              "fuse": [[x, y] for (x, y) in fuses], "rk": rank, "n": len(balls), "q": quota()}
    events.clear()
    seq = frame["seq"] + 1
    d = dict(common, s=seq, f=0, b=delta, r=removed)
    f = dict(common, s=seq, f=1, b=full, r=[])
    g = {"g": grid, "gv": grid_ver}
    with frame_cv:
        frame.update(seq=seq,
                     delta=b"data: " + json.dumps(d, separators=(",", ":")).encode() + b"\n\n",
                     full=b"data: " + json.dumps(f, separators=(",", ":")).encode() + b"\n\n",
                     grid=b"data: " + json.dumps(g, separators=(",", ":")).encode() + b"\n\n",
                     gv=grid_ver)
        frame_cv.notify_all()


stats = {"ticks": 0, "worst": 0.0, "sum": 0.0}


def sim_loop():
    nxt = time.perf_counter()
    k = 0
    last_save = time.time()
    while True:
        t0 = time.perf_counter()
        with lock:
            try:
                step()
                k += 1
                if k % FRAME_EVERY == 0:
                    publish()
            except Exception:  # never let one bad tick stop the whole world
                import traceback
                traceback.print_exc()
            if dirty and time.time() - last_save > 5:
                last_save = time.time()
                persist()
        el = time.perf_counter() - t0
        stats["ticks"] += 1
        stats["sum"] += el
        stats["worst"] = max(stats["worst"], el)
        nxt += TICK
        pause = nxt - time.perf_counter()
        if pause > 0:
            time.sleep(pause)
        else:
            nxt = time.perf_counter()  # fell behind: do not try to catch up


# ---------------------------------------------------------------- actions
def act(p, a):
    """One input from a device: ["b", x, y] balls, ["s", x, y, ch] block, ["d", x, y] dig."""
    if not isinstance(a, list) or len(a) < 3:
        return
    kind = a[0]
    try:
        x, y = float(a[1]), float(a[2])
    except (TypeError, ValueError):
        return
    if not (0 <= x < W and 0 <= y < H):
        return
    now = time.time()
    p["tokens"] = min(14.0, p["tokens"] + (now - p["last"]) * 9)
    p["last"] = now
    p["x"], p["y"], p["t"] = x, y, now
    allowed = TOOLS[None] if p["av"] == TEACHER_AV else TOOLS[p.get("team")]
    if state in ("won", "lost", "count"):
        return
    if (kind if kind != "s" else (a[3] if len(a) > 3 else "")) not in allowed:
        return
    if kind == "b":
        if a[3:4] != [1]:
            return  # only real taps; old pages that still "pour" while held are ignored
        burst = 5
        burst = min(burst, int(p["tokens"]))
        if burst <= 0:
            return
        p["tokens"] -= burst
        for i in range(burst):
            add_ball(x + random.uniform(-0.6, 0.6), min(y, SKY - 0.6) + random.uniform(-0.5, 0.2),
                     random.uniform(-3, 3), random.uniform(-4, 0), p["colour"], p["n"])
        trim(p["n"])
        return
    tx, ty = int(x), int(y)
    if p["tokens"] < 0.5:
        return
    p["tokens"] -= 0.5
    cur = grid[ty][tx]
    if now - p.get("last_block", 0) < 0.25:
        return  # one block per tap, no painting by dragging
    p["last_block"] = now
    if kind == "s" and len(a) > 3 and a[3] in PLACEABLE and cur == AIR:
        if a[3] == "T":
            if p["tokens"] < 3 or sum(1 for _ in fuses) >= 6:
                return
            p["tokens"] -= 3
        if p.get("team") == 1:
            if p["tokens"] < 1.5:
                return
            p["tokens"] -= 1.5
            if a[3] in "SA":
                temp[(tx, ty)] = now + TEMP_LIFE
        set_tile(tx, ty, a[3])
        events.append(["s", tx, ty, p["n"]])
        if a[3] == "T":
            fuses[(tx, ty)] = now + 2.2
    elif kind == "d" and cur != AIR and cur not in UNBREAKABLE:
        set_tile(tx, ty, AIR)
        fuses.pop((tx, ty), None)
        temp.pop((tx, ty), None)
        events.append(["d", tx, ty, cur])


def teacher(action):
    global frozen, muted, balls, party_left, party_until, session, state, chest
    if action == "freeze":
        frozen = not frozen
    elif action == "mute":
        muted = not muted
    elif action == "clear":
        balls = []
        party_left = 0
    elif action == "party":
        party_left += PARTY_BALLS
        party_until = time.time() + 6
        events.append(["p"])
    elif action == "start":  # start (or restart) the current level with new random teams
        start_level(0 if state == "done" else level)
    elif action == "next":
        start_level((level + 1) % len(LEVELS))
    elif action == "shuffle":
        shuffle_teams()
    elif action == "newclass":
        players.clear()
        frozen, session = False, new_session()
        start_level(0)
        state, chest = "ready", 0
        for p in players.values():
            p["team"] = None


# ---------------------------------------------------------------- http
def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def find_cert(ip):
    """Reuse a certificate the other lesson apps made, so old https:// links in tablets still open."""
    crt, key = os.path.join(CERT_DIR, f"cert-{ip}.pem"), os.path.join(CERT_DIR, f"key-{ip}.pem")
    return (crt, key) if os.path.exists(crt) and os.path.exists(key) else None


class DualServer(ThreadingHTTPServer):
    """One port for both http:// and https://: peek at the first byte of each connection."""
    daemon_threads = True
    request_queue_size = 128  # a whole class connects in the same second

    def finish_request(self, request, client_address):
        if tls_ctx is not None:
            try:
                request.settimeout(10)
                if request.recv(1, socket.MSG_PEEK) == b"\x16":
                    request = tls_ctx.wrap_socket(request, server_side=True)
                request.settimeout(None)
            except (OSError, ssl.SSLError):
                return
        try:
            super().finish_request(request, client_address)
        except (ssl.SSLError, ConnectionError, OSError):
            pass


class H_(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def is_local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def body(self):
        try:
            n = max(0, min(16384, int(self.headers.get("Content-Length") or 0)))
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/board"):
            with open(APP, encoding="utf-8") as f:
                html = f.read()
            mode = "board" if path == "/board" else "kid"
            html = html.replace("/*MODE*/", f'window.MODE="{mode}";window.TEACHER={str(self.is_local()).lower()};'
                                f'window.JOIN="http://{lan_ip()}:{PORT}";')
            self.send(200, html.encode(), "text/html; charset=utf-8")
        elif path == "/three.module.min.js":
            with open(os.path.join(HERE, "vendor", "three.module.min.js"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)
        elif path in ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
            self.send(204, b"", "image/png")
        elif path == "/api/ping":
            self.json({"ok": True, "app": "kulky", "session": session})
        elif path == "/api/stats":
            t = stats["ticks"] or 1
            self.json({"ticks": t, "avg_ms": stats["sum"] / t * 1000, "worst_ms": stats["worst"] * 1000,
                       "balls": len(balls), "streams": streams[0], "seq": frame["seq"]})
        elif path == "/api/stream":
            self.stream()
        else:
            self.send(404, b"not found", "text/plain")

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.connection.settimeout(5)  # a sleeping tablet must not hold a thread forever
        with lock:
            streams[0] += 1
        sent_seq, sent_gv = -1, -1
        try:
            self.wfile.write(b"retry: 1000\n\n")
            while True:
                with frame_cv:
                    if frame["seq"] == sent_seq:
                        frame_cv.wait(2)
                    seq, delta, full, g, gv = frame["seq"], frame["delta"], frame["full"], frame["grid"], frame["gv"]
                if seq == sent_seq:
                    self.wfile.write(b": ka\n\n")
                    continue
                out = b""
                if gv != sent_gv:
                    out += g
                    sent_gv = gv
                out += delta if seq == sent_seq + 1 else full  # missed a frame: send everything
                self.wfile.write(out)
                self.wfile.flush()
                sent_seq = seq
        except (OSError, ConnectionError, ssl.SSLError):
            pass
        finally:
            with lock:
                streams[0] -= 1

    def do_POST(self):
        path = self.path.split("?")[0]
        d = self.body()
        if path == "/api/teacher":
            if not self.is_local():
                return self.json({"error": "only from the teacher laptop"}, 403)
            with lock:
                teacher(str(d.get("action")))
                return self.json({"ok": True, "session": session})
        if path not in ("/api/join", "/api/act"):
            return self.send(404, b"not found", "text/plain")
        pid = str(d.get("pid", ""))[:24]
        if len(pid) < 6:
            return self.json({"error": "pid"}, 400)
        with lock:
            if d.get("session") != session and path == "/api/act":
                return self.json({"ok": False, "stale": True, "session": session})
            p = players.get(pid)
            if path == "/api/join" or p is None:
                av = d.get("av")
                av = av if isinstance(av, int) and 0 <= av <= TEACHER_AV else 0
                if av == TEACHER_AV and not self.is_local():
                    av = 0
                if p is None:
                    p = players[pid] = {"n": len(players) + 1, "seen": time.time(), "x": 0, "y": 0, "t": 0,
                                        "tokens": 14.0, "last": time.time(), "got": 0}
                p["av"], p["colour"] = av, av
                p.setdefault("team", None)
            p["seen"] = time.time()
            if p.get("team") is None:
                give_team(p)
            if path == "/api/act" and not frozen:
                for a in (d.get("a") or [])[:40]:
                    act(p, a)
            return self.json({"ok": True, "n": p["n"], "team": p["team"], "session": session, "frozen": frozen})


streams = [0]

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    PORT = int(args[0]) if args else 8000
    if "--quick" in sys.argv:  # for trying the level flow quickly
        for lv in LEVELS:
            lv["goal"], lv["time"] = 5, 20
    load()
    if "--reset" in sys.argv:
        level, session = 0, new_session()
        grid = build_level(0)
    persist()
    ip = lan_ip()
    cert = find_cert(ip)
    if cert:
        try:
            tls_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls_ctx.load_cert_chain(*cert)
        except (OSError, ssl.SSLError):
            tls_ctx = None
    threading.Thread(target=sim_loop, daemon=True).start()
    srv = DualServer(("0.0.0.0", PORT), H_)
    print(f"\n  Дітям:     http://{ip}:{PORT}")
    print(f"  Проєктор:  http://localhost:{PORT}/board")
    print("  Зупинити: Ctrl+C\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
