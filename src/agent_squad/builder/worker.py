"""Builder worker: stays up and pulls the CRM queue itself (builder_mode = worker). Each ticket runs in a NEW Claude
Code session (clean context), goes through the reviewer and becomes an approval request. Nobody has to call anybody.

  squad worker        # leave it running

Inside Maestri (MAESTRI_PIPE set), the site under construction shows in the "live" portal (`pnpm dev` on
[builder] live_port) and, at the end, the preview.
"""
import os
import shutil
import subprocess
import sys
import threading
import time

from .. import crm, db
from ..config import child_env, settings
from . import runner


def maestri(*args: str) -> None:
    exe = shutil.which("maestri")
    if exe and os.environ.get("MAESTRI_PIPE"):
        subprocess.run([exe, *args], capture_output=True, timeout=30)


def live(slug: str, done: threading.Event) -> None:
    """In Maestri: starts the site's dev server once it has dependencies and points the live portal at it."""
    pnpm = shutil.which("pnpm")
    if not os.environ.get("MAESTRI_PIPE") or not pnpm:
        return
    port = int(settings().section("builder").get("live_port", 3400))
    site = settings().workspace / "sites" / slug
    while not (site / "node_modules").exists():
        if done.wait(3):
            return
    p = subprocess.Popen([pnpm, "dev", "-p", str(port)], cwd=site, env=child_env(), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    if not done.wait(10):
        maestri("portal", "navigate", "live", f"http://localhost:{port}")
    done.wait()
    if os.name == "nt":  # pnpm -> next: kill the whole tree
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        p.terminate()


def run(t: dict) -> None:
    tid, slug = t["id"], t["slug"]
    print(f"\n=== ticket {tid} ({t['type']}) for {slug} ===", flush=True)
    print("  follow it with `squad logs claude` and the reviewer with `squad logs codex`", flush=True)
    done = threading.Event()
    viewer = threading.Thread(target=live, args=(slug, done), daemon=True)
    viewer.start()
    try:
        r = runner.execute(tid)
    finally:
        done.set()
        viewer.join(timeout=15)
    with db.connect() as c:
        url = c.execute("select preview_url from tickets where id=%s", (tid,)).fetchone()["preview_url"]
    if r["ok"] and url:
        maestri("portal", "navigate", "live", url)
    print(f"=== ticket {tid}: {'ready, approval requested' if r['ok'] else 'failed: ' + str(r['reason'])} "
          f"| review: {r['review'] or 'did not run'} | {r['minutes']} min ===\n", flush=True)


def main(argv: list[str] | None = None) -> None:
    os.environ["SQUAD_ROLE"] = "runner"
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("builder worker up: pulling the CRM queue (Ctrl+C to stop)", flush=True)
    warned = False
    try:
        while True:
            try:
                crm.dispatch()  # reopens runs that died (machine slept, process crashed)
                with db.connect() as c:
                    mode = crm.builder_mode(c)
                if mode != "worker":
                    if not warned:
                        print("builder_mode = spawn: the CRM starts runners itself; this worker stays idle.", flush=True)
                        warned = True
                    time.sleep(30)
                    continue
                warned = False
                t = crm.claim_next_ticket()
                if t.get("queue") == "empty":
                    time.sleep(10)
                    continue
                run(t)
            except KeyboardInterrupt:
                raise
            except Exception as e:  # database down, network: retry, never die
                print(f"worker error: {e!r}; retrying in 30 s", flush=True)
                time.sleep(30)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
