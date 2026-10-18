"""Synthesized soundtrack for the Hivemind video: 120 bpm, cuts on bar lines. Writes public/music.wav."""
import wave
import numpy as np

SR = 44100
DUR = 82.0
BEAT = 0.5
N = int(SR * DUR)
L = np.zeros(N); R = np.zeros(N)
rng = np.random.default_rng(7)

DROP = 12.0
CUTS = [20, 28, 34, 46, 50, 60, 68, 76]   # scene cuts (s) -> whooshes
BREAK = (50.0, 60.0)                  # lighter drums under the architecture diagram


def note(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def add(buf, start, sig):
    i = int(start * SR)
    if i >= N:
        return
    j = min(N, i + len(sig))
    buf[i:j] += sig[: j - i]


def env(n, a, r):
    e = np.ones(n)
    a = max(1, int(a * SR)); r = max(1, int(r * SR))
    e[:a] = np.linspace(0, 1, a)
    e[-r:] *= np.linspace(1, 0, r)
    return e


def lowpass(x, cutoff):
    # one-pole, cutoff may be an array
    c = np.broadcast_to(np.asarray(cutoff, float), x.shape)
    a = 1 - np.exp(-2 * np.pi * c / SR)
    y = np.empty_like(x); s = 0.0
    for k in range(len(x)):
        s += a[k] * (x[k] - s); y[k] = s
    return y


def saw(f, t):
    return 2 * ((f * t) % 1.0) - 1


# chords: Am F C G (two bars each = 4s)
PROG = [[57, 60, 64, 69], [53, 57, 60, 65], [48, 55, 60, 64], [55, 59, 62, 67]]
ROOTS = [45, 41, 48, 43]

# --- pad (whole track), filter opens toward drop
t = np.arange(N) / SR
pad = np.zeros(N)
for ci in range(int(DUR / 4) + 1):
    t0 = ci * 4.0
    n = int(4.4 * SR)
    tt = np.arange(n) / SR
    sig = np.zeros(n)
    for m in PROG[ci % 4]:
        for d in (-0.08, 0.0, 0.08):
            sig += saw(note(m) * 2 ** (d / 12), tt + rng.random())
    add(pad, t0, sig * env(n, 0.6, 0.8) / 12)
cut = np.interp(t, [0, DROP - 2, DROP, 48, 50, 60, 76, DUR], [300, 1400, 2600, 2600, 1200, 2600, 900, 400])
pad = lowpass(pad, cut) * 0.55
L += pad; R += pad

# --- arp (intro + breakdown), 16ths, ping-pong
for k in range(int(DUR / (BEAT / 2))):
    st = k * BEAT / 2
    if not (2 <= st < DROP or BREAK[0] <= st < BREAK[1] or 60 <= st < 76):
        continue
    ch = PROG[int(st // 4) % 4]
    m = ch[k % 4] + 12
    n = int(0.22 * SR); tt = np.arange(n) / SR
    s = (np.sign(np.sin(2 * np.pi * note(m) * tt)) * 0.5 + np.sin(2 * np.pi * note(m) * tt)) * np.exp(-tt * 14) * 0.07
    vol = min(1, (st - 2) / 10) if st < DROP else 0.8
    add(L if k % 2 else R, st, s * vol)
    add(R if k % 2 else L, st + 0.18, s * vol * 0.35)

# --- drums
def kick():
    n = int(0.45 * SR); tt = np.arange(n) / SR
    f = 50 + 120 * np.exp(-tt * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-tt * 7) * 0.9


def hat(open_=False):
    n = int((0.25 if open_ else 0.06) * SR); tt = np.arange(n) / SR
    x = rng.standard_normal(n); x = x - lowpass(x, 7000)
    return x * np.exp(-tt * (12 if open_ else 60)) * 0.12


def clap():
    n = int(0.3 * SR); tt = np.arange(n) / SR
    x = rng.standard_normal(n); x = lowpass(x, 3000) - lowpass(x, 800)
    e = np.exp(-tt * 18) + 0.6 * np.exp(-((tt - 0.012) * 300) ** 2) + 0.6 * np.exp(-((tt - 0.024) * 300) ** 2)
    return x * e * 0.5


K, HC, HO, CL = kick(), hat(), hat(True), clap()
b = DROP
while b < 76:
    brk = BREAK[0] <= b < BREAK[1]
    beat_i = int(round((b - DROP) / BEAT))
    if not brk or beat_i % 4 == 0:
        add(L, b, K); add(R, b, K)
    if beat_i % 2 == 1 and not brk:
        add(L, b, CL * 0.9); add(R, b, CL)
    h = HO if beat_i % 4 == 3 else HC
    add(L, b + BEAT / 2, h * 0.8); add(R, b + BEAT / 2, h)
    if not brk:
        add(R, b + BEAT / 4, HC * 0.4); add(L, b + 3 * BEAT / 4, HC * 0.4)
    b += BEAT

# --- bass (from drop), offbeat pump on root
for k in range(int((76 - DROP) / (BEAT / 2))):
    st = DROP + k * BEAT / 2
    if k % 2 == 0:
        continue
    m = ROOTS[int(st // 4) % 4]
    n = int(0.24 * SR); tt = np.arange(n) / SR
    s = (np.sin(2 * np.pi * note(m) * tt) + 0.35 * saw(note(m), tt)) * env(n, 0.005, 0.08) * 0.32
    if BREAK[0] <= st < BREAK[1]:
        s *= 0.5
    add(L, st, s); add(R, st, s)

# --- sidechain pump
duck = np.ones(N)
b = DROP
while b < 76:
    i = int(b * SR); n = int(0.25 * SR)
    duck[i:i + n] = np.minimum(duck[i:i + n], 1 - 0.55 * np.exp(-np.arange(n) / SR * 14))
    b += BEAT
L *= duck; R *= duck


# --- fx: riser into drop, whooshes on cuts, impacts
def noise_sweep(dur, f0, f1, rise=True):
    n = int(dur * SR); x = rng.standard_normal(n)
    c = np.geomspace(f0, f1, n)
    y = lowpass(x, c) - lowpass(x, c * 0.3)
    e = np.linspace(0, 1, n) ** 2 if rise else np.exp(-np.linspace(0, 6, n))
    return y * e


riser = noise_sweep(4.0, 300, 9000) * 0.5
add(L, DROP - 4, riser); add(R, DROP - 4, riser[::-1] * 0 + riser)


def impact():
    n = int(2.5 * SR); tt = np.arange(n) / SR
    boom = np.sin(2 * np.pi * np.cumsum(40 + 80 * np.exp(-tt * 10)) / SR) * np.exp(-tt * 2.2)
    return boom * 0.9 + noise_sweep(2.5, 6000, 200, rise=False) * 0.35


IMP = impact()
for s in (DROP, 76.0):
    add(L, s, IMP); add(R, s, IMP)
add(L, 8.0, IMP * 0.4); add(R, 8.0, IMP * 0.4)  # logo reveal starts

for c in CUTS:
    w = noise_sweep(0.6, 400, 6000) * 0.35
    add(L, c - 0.6, w); add(R, c - 0.55, w)

# --- outro tail fade, master
fade = np.ones(N)
fi = int(79.0 * SR); fade[fi:] = np.linspace(1, 0, N - fi)
fade[: int(0.3 * SR)] = np.linspace(0, 1, int(0.3 * SR))
L *= fade; R *= fade
peak = max(np.abs(L).max(), np.abs(R).max())
L = np.tanh(L / peak * 1.4) * 0.89; R = np.tanh(R / peak * 1.4) * 0.89
pcm = (np.stack([L, R], 1) * 32767).astype('<i2')
with wave.open('public/music.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
print('ok', DUR, 's')
