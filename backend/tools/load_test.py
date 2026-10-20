"""
Load test: many people using SmartPoli at once - LOCAL servers only (never the live site).

    python tools/load_test.py http://127.0.0.1:8022 --users 500 --seconds 60

Creates --accounts real accounts through the API (each with medicines), then runs --users virtual people for --seconds:
each opens the dashboard, the safety center, the emergency card, and now and then marks a dose taken, with short
pauses like a person. Prints requests per second, median / 95th / 99th percentile time per screen and every error.
"""
import argparse
import random
import statistics
import sys
import threading
import time
from collections import defaultdict
from urllib.parse import urlparse

import requests


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--users", type=int, default=500)
    ap.add_argument("--accounts", type=int, default=50)
    ap.add_argument("--seconds", type=int, default=60)
    a = ap.parse_args()
    if urlparse(a.base).hostname not in ("127.0.0.1", "localhost"):
        print("Refusing: load tests run against a local server only.")
        return 2

    s = requests.Session()
    accounts = []
    run = random.randint(1000, 9999)
    for i in range(a.accounts):
        email = f"load{run}.{i}@local.test"
        s.post(a.base + "/auth/register", json={"email": email, "password": "load-pass-123", "name": f"Load {i}", "role": "patient"}).raise_for_status()
        tok = s.post(a.base + "/auth/login", json={"email": email, "password": "load-pass-123"}).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        pid = s.post(a.base + "/patients", json={"name": f"Load {i}"}, headers=h).json()["id"]
        for name, times in (("Dolo 650", ["08:00", "20:00"]), ("Telma 40", ["09:00"])):
            s.post(a.base + "/medicines/manual", headers=h, json={"patient_id": pid, "name": name, "strength": "", "form": "tab",
                   "as_needed": False, "times": times, "weekdays": None, "food": "any", "duration_days": 30}).raise_for_status()
        accounts.append((h, pid))
    print(f"{len(accounts)} accounts ready; {a.users} people for {a.seconds}s ...")

    times, errors, lock = defaultdict(list), defaultdict(int), threading.Lock()
    stop = time.monotonic() + a.seconds

    def person(n):
        h, pid = accounts[n % len(accounts)]
        sess = requests.Session()
        while time.monotonic() < stop:
            for label, method, path in (("dashboard", "GET", f"/patients/{pid}/dashboard"),
                                        ("safety center", "GET", f"/patients/{pid}/safety-center"),
                                        ("emergency card", "GET", f"/patients/{pid}/emergency-card")):
                t0 = time.perf_counter()
                try:
                    r = sess.request(method, a.base + path, headers=h, timeout=60)
                    ok = r.status_code < 400
                except requests.RequestException:
                    ok = False
                with lock:
                    times[label].append((time.perf_counter() - t0) * 1000)
                    if not ok:
                        errors[label] += 1
                time.sleep(random.uniform(0.5, 3))          # a person reads the screen

    threads = [threading.Thread(target=person, args=(n,), daemon=True) for n in range(a.users)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
        time.sleep(0.01)                                     # people arrive over a few seconds, not in one instant
    for t in threads:
        t.join()
    total = sum(len(v) for v in times.values())
    print(f"{total} requests in {time.monotonic() - t0:.0f}s = {total / (time.monotonic() - t0):.0f} per second")
    for label, ts in times.items():
        ts.sort()
        pct = lambda p: ts[min(len(ts) - 1, int(p * len(ts)))]  # noqa: E731
        print(f"  {label:15s} median {statistics.median(ts):5.0f} ms   95% {pct(0.95):5.0f} ms   99% {pct(0.99):5.0f} ms"
              f"   errors {errors[label]}")
    return 1 if sum(errors.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
