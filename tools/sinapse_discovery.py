#!/usr/bin/env python3
"""Sinapse Home (ESP RainMaker white-label) discovery.

Faz login na nuvem da Sinapse, lista os nodes (spas/aquecedores) e salva
config + params de cada um em sinapse_nodes.json, para mapear as entidades
do futuro custom component do Home Assistant.

Uso:
    python3 sinapse_discovery.py            # pede e-mail e senha
    SINAPSE_USER=... SINAPSE_PASS=... python3 sinapse_discovery.py

Só usa a biblioteca padrão. Nenhuma alteração é feita no equipamento.
Atenção: o JSON gerado pode conter o POP de controle local; não publique.
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
    user = os.environ.get("SINAPSE_USER") or input("E-mail Sinapse Home: ")
    pwd = os.environ.get("SINAPSE_PASS") or getpass.getpass("Senha: ")

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

    print(f"{len(nodes)} node(s) encontrado(s)\n")
    for n in nodes:
        cfg = n.get("config", {})
        info = cfg.get("info", {})
        online = n.get("status", {}).get("connectivity", {}).get("connected")
        print(f"- node_id={n['id']}  nome={info.get('name')}  "
              f"tipo={info.get('type')}  fw={info.get('fw_version')}  online={online}")
        for dev in cfg.get("devices", []):
            print(f"    device '{dev['name']}' ({dev.get('type')})")
            for p in dev.get("params", []):
                bounds = p.get("bounds", "")
                print(f"      {p['name']:<22} {p.get('type',''):<28} "
                      f"{p.get('data_type',''):<6} {','.join(p.get('properties', []))} {bounds}")
        for svc in cfg.get("services", []):
            print(f"    service '{svc['name']}' ({svc.get('type')})")
        print(f"    valores atuais: {json.dumps(n.get('params', {}), ensure_ascii=False)}\n")

    with open("sinapse_nodes.json", "w") as f:
        json.dump(nodes, f, indent=2, ensure_ascii=False)
    print("Detalhes completos salvos em sinapse_nodes.json")


if __name__ == "__main__":
    main()
