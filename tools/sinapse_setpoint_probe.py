#!/usr/bin/env python3
"""Probe how the heater setpoint behaves through the Sinapse cloud.

Prints the heater params every 5 s for 2 minutes. Optionally writes a new
setpoint first (exactly the payload the official app sends), so you can see
whether the device applies it and whether panel changes reach the cloud.

Usage:
    python3 sinapse_setpoint_probe.py            # watch only (change it on the panel meanwhile)
    python3 sinapse_setpoint_probe.py 37         # write setpoint 37, then watch
"""
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://90h350n1tc.execute-api.sa-east-1.amazonaws.com/dev/v1"
DEVICE = "Aquecedor"
SETPOINT = "Temperatura programada"


def call(method, path, token=None, body=None, query=None):
    url = f"{BASE}/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def main():
    user = os.environ.get("SINAPSE_USER") or input("Sinapse Home e-mail: ")
    pwd = os.environ.get("SINAPSE_PASS") or getpass.getpass("Password: ")
    _, login = call("POST", "login", body={"user_name": user, "password": pwd})
    token = login["accesstoken"]

    _, nodes = call("GET", "user/nodes", token, query={"node_details": "true"})
    node_id = nodes["node_details"][0]["id"]
    print(f"node {node_id}")

    if len(sys.argv) > 1:
        value = int(sys.argv[1])
        status, resp = call(
            "PUT", "user/nodes/params", token,
            body={DEVICE: {SETPOINT: value}}, query={"node_id": node_id},
        )
        print(f"PUT {SETPOINT}={value} -> HTTP {status} {resp}")

    last = None
    for _ in range(24):
        _, params = call("GET", "user/nodes/params", token, query={"node_id": node_id})
        heater = params.get(DEVICE, params) if isinstance(params, dict) else params
        snapshot = json.dumps(heater, ensure_ascii=False, sort_keys=True)
        marker = "  <-- changed" if last is not None and snapshot != last else ""
        print(time.strftime("%H:%M:%S"), snapshot, marker)
        last = snapshot
        time.sleep(5)


if __name__ == "__main__":
    main()
