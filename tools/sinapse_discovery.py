#!/usr/bin/env python3
"""Sinapse Home (white-label ESP RainMaker) discovery.

Logs in to the Sinapse cloud, lists the nodes (spas/heaters) and saves the
config and params of each one to sinapse_nodes.json, to map the entities
of the Home Assistant integration.

Usage:
    python3 sinapse_discovery.py            # prompts for e-mail and password
    SINAPSE_USER=... SINAPSE_PASS=... python3 sinapse_discovery.py

Standard library only. Nothing is changed on the device.
Warning: the generated JSON may contain the local-control POP; don't publish it.
"""
import getpass
import json
import os
import urllib.parse
import urllib.request

BASE = "https://90h350n1tc.execute-api.sa-east-1.amazonaws.com/dev/v1"


def call(method, path, token=None, body=None, query=None):
    url = f"{BASE}/{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", token)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or b"{}")


def main():
    user = os.environ.get("SINAPSE_USER") or input("Sinapse Home e-mail: ")
    pwd = os.environ.get("SINAPSE_PASS") or getpass.getpass("Password: ")

    login = call("POST", "login", body={"user_name": user, "password": pwd})
    token = login["accesstoken"]
    print("Login OK")

    nodes, start = [], None
    while True:
        q = {"node_details": "true"}
        if start:
            q["start_id"] = start
        resp = call("GET", "user/nodes", token, query=q)
        nodes += resp.get("node_details", [])
        start = resp.get("next_id")
        if not start:
            break

    print(f"{len(nodes)} node(s) found\n")
    for n in nodes:
        cfg = n.get("config", {})
        info = cfg.get("info", {})
        online = n.get("status", {}).get("connectivity", {}).get("connected")
        print(f"- node_id={n['id']}  name={info.get('name')}  "
              f"type={info.get('type')}  fw={info.get('fw_version')}  online={online}")
        for dev in cfg.get("devices", []):
            print(f"    device '{dev['name']}' ({dev.get('type')})")
            for p in dev.get("params", []):
                bounds = p.get("bounds", "")
                print(f"      {p['name']:<22} {p.get('type',''):<28} "
                      f"{p.get('data_type',''):<6} {','.join(p.get('properties', []))} {bounds}")
        for svc in cfg.get("services", []):
            print(f"    service '{svc['name']}' ({svc.get('type')})")
        print(f"    current values: {json.dumps(n.get('params', {}), ensure_ascii=False)}\n")

    with open("sinapse_nodes.json", "w") as f:
        json.dump(nodes, f, indent=2, ensure_ascii=False)
    print("Full details saved to sinapse_nodes.json")


if __name__ == "__main__":
    main()
