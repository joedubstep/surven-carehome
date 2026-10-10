"""Campaign state on the `state` branch (9/29, cloud move). The branch is always ONE commit, replaced each save, so the
repo never grows from the 10-minute runs.

  pull          copy every state file from origin/state into data/carehome/ (GitHub Actions start, or the PC)
  save-cloud    (GitHub Actions end) publish the files the cloud run owns; files the PC owns are taken fresh from
                origin/state so a queue update pushed from the PC mid-run is never lost
  push-local F  (the PC) publish PC-owned files, e.g. `push-local queue.json build/prospects.json`; everything
                else is kept exactly as the cloud left it

carehome.db is cloud-owned. To change it from the PC: `gh workflow disable carehome`, wait for any running job,
`pull`, edit, `push-local carehome.db`, `gh workflow enable carehome`."""
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "data/carehome/"
CLOUD_OWNED = ["carehome.db", "senders.json", "scan_state.json", "last_run.json", "alerts_pending.jsonl", "alerts_inflight.jsonl",
               "alerts_sent.jsonl", "run.log", "task.log", "stats.log", "stats_task.log", "stats_notion_map.json",
               "build/notion_map.json"]
LOCAL_OWNED = ["queue.json", "sheet_map.json", "phones.json", "funnel_events.json", "lead_facts.json", "build/prospects.json",
               "build/notion_sync.py"]
LOGS = {"run.log", "task.log", "stats.log", "stats_task.log"}


# 10/10: the state lives in its own private repo, joedubstep/surven-carehome-state, so the code repo can be public
# (unlimited free Actions minutes). A git remote named "state" points at it (GitHub Actions adds it with the deploy
# key in STATE_DEPLOY_KEY; on the PC: git remote add state https://github.com/joedubstep/surven-carehome-state.git).
# With no "state" remote it falls back to origin, the old home.
def remote():
    r = subprocess.run(["git", "remote"], cwd=ROOT, capture_output=True)
    return "state" if "state" in r.stdout.decode().split() else "origin"


def git(*args, env=None, input=None):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, env={**os.environ, **(env or {})}, input=input)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {r.stderr.decode(errors='ignore')[:400]}")
    return r.stdout


def fetch():
    """-> sha of origin/state, or '' if the branch does not exist yet."""
    try:
        git("fetch", "--quiet", "--depth=1", remote(), "+refs/heads/state:refs/remotes/origin/state")
        return git("rev-parse", "refs/remotes/origin/state").decode().strip()
    except RuntimeError:
        return ""


def remote_files(sha):
    return [p for p in git("ls-tree", "-r", "--name-only", sha).decode().splitlines() if p] if sha else []


def pull():
    sha = fetch()
    for p in remote_files(sha):
        dest = os.path.join(ROOT, p)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(git("show", f"{sha}:{p}"))
    print("pulled", len(remote_files(sha)), "state files from", sha[:8] or "(none)")


def publish(base_sha, blobs, msg):
    """blobs: {repo path: bytes}. One parentless commit, force-pushed with a lease on base_sha."""
    idx = os.path.join(tempfile.gettempdir(), f"state-index-{os.getpid()}")
    env = {"GIT_INDEX_FILE": idx, "GIT_AUTHOR_NAME": "carehome-bot", "GIT_AUTHOR_EMAIL": "bot@surven.ai",
           "GIT_COMMITTER_NAME": "carehome-bot", "GIT_COMMITTER_EMAIL": "bot@surven.ai"}
    git("read-tree", "--empty", env=env)
    for path, data in sorted(blobs.items()):
        sha = git("hash-object", "-w", "--stdin", input=data).decode().strip()
        git("update-index", "--add", "--cacheinfo", f"100644,{sha},{path}", env=env)
    tree = git("write-tree", env=env).decode().strip()
    commit = git("commit-tree", tree, "-m", msg, env=env).decode().strip()
    git("push", "--quiet", f"--force-with-lease=refs/heads/state:{base_sha}", remote(), f"{commit}:refs/heads/state")
    os.remove(idx)
    return commit


def read_local(rel):
    p = os.path.join(ROOT, BASE, rel)
    if not os.path.exists(p):
        return None
    data = open(p, "rb").read()
    if rel in LOGS:  # keep logs from growing forever
        data = b"\n".join(data.splitlines()[-3000:]) + b"\n"
    return data


def save(owned, label):
    for attempt in range(6):
        base = fetch()
        blobs = {p: git("show", f"{base}:{p}") for p in remote_files(base)}
        for rel in owned:
            data = read_local(rel)
            if data is not None:
                blobs[BASE + rel] = data
        try:
            c = publish(base, blobs, f"{label} {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("state saved", c[:8], "files", len(blobs))
            return
        except RuntimeError as ex:  # someone else saved first: take their files and try again
            print("retry save:", str(ex)[:200])
            time.sleep(3 + attempt * 2)
    raise SystemExit("could not save state")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "pull":
        pull()
    elif cmd == "save-cloud":
        save(CLOUD_OWNED, "cloud run")
    elif cmd == "push-local":
        save(sys.argv[2:] or LOCAL_OWNED, "from Joey's PC")
    elif cmd == "init":  # first publish: everything from the PC
        save(CLOUD_OWNED + LOCAL_OWNED, "initial state from Joey's PC")
    else:
        print(__doc__)
