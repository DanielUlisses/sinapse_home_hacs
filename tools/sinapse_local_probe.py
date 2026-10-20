#!/usr/bin/env python3
"""Sinapse Home local control (LAN) probe.

Logs in to the Sinapse cloud to get each node's id and local-control POP,
finds the node on the LAN over mDNS (_esp_local_ctrl._tcp), runs the
Security1 handshake and dumps the `config` and `params` properties.

Usage:
    python3 sinapse_local_probe.py                       # prompts for e-mail and password
    SINAPSE_USER=... SINAPSE_PASS=... python3 sinapse_local_probe.py
    python3 sinapse_local_probe.py --host 192.168.1.50   # skip mDNS (port 8080 unless --port)
    python3 sinapse_local_probe.py --watch 120           # print local params changes for 120 s
    python3 sinapse_local_probe.py --set '{"Cromoterapia": {"Status": true}}'

Needs the standard library and `cryptography`. The protocol code is loaded
from custom_components/sinapse_home/local_protocol.py, so it is the same code
Home Assistant runs. Read-only unless --set is given. The POP is never printed.
"""
import argparse
import getpass
import importlib.util
import json
import os
import pathlib
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request
from http.client import HTTPConnection

BASE = "https://90h350n1tc.execute-api.sa-east-1.amazonaws.com/dev/v1"
SERVICE = "_esp_local_ctrl._tcp.local."
REDACTED = "**REDACTED**"

_PROTO = pathlib.Path(__file__).resolve().parent.parent / (
    "custom_components/sinapse_home/local_protocol.py"
)
_spec = importlib.util.spec_from_file_location("sinapse_local_protocol", _PROTO)
lp = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lp
_spec.loader.exec_module(lp)


# --- cloud -------------------------------------------------------------------


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


def cloud_nodes(user, pwd):
    token = call("POST", "login", body={"user_name": user, "password": pwd})["accesstoken"]
    nodes, start = [], None
    while True:
        q = {"node_details": "true"}
        if start:
            q["start_id"] = start
        resp = call("GET", "user/nodes", token, query=q)
        nodes += resp.get("node_details", [])
        start = resp.get("next_id")
        if not start:
            return token, nodes


def local_credentials(node):
    """(POP, security type) from the node's Local Control service, or None."""
    for svc in node.get("config", {}).get("services", []):
        if svc.get("type") != "esp.service.local_control":
            continue
        names = {p.get("type"): p.get("name") for p in svc.get("params", [])}
        values = node.get("params", {}).get(svc["name"], {})
        pop = values.get(names.get("esp.param.local_control_pop", "POP"))
        sec = values.get(names.get("esp.param.local_control_type", "Type"), 1)
        return str(pop or ""), int(sec)
    return None


def redact(obj):
    """Copy of a params/config dict with the POP hidden."""
    if isinstance(obj, dict):
        return {k: REDACTED if k in ("POP", "pop") else redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


# --- mDNS (one-shot legacy unicast query, RFC 6762 section 6.7) ---------------


def _name(buf, pos):
    labels, jumped, end = [], False, pos
    while True:
        length = buf[pos]
        if length & 0xC0 == 0xC0:
            if not jumped:
                end = pos + 2
            pos, jumped = struct.unpack_from("!H", buf, pos)[0] & 0x3FFF, True
            continue
        pos += 1
        if length == 0:
            break
        labels.append(buf[pos : pos + length].decode(errors="replace"))
        pos += length
    return ".".join(labels) + ".", (end if jumped else pos)


def _records(buf):
    qd, an, ns, ar = struct.unpack_from("!4H", buf, 4)
    pos = 12
    for _ in range(qd):
        _, pos = _name(buf, pos)
        pos += 4
    for _ in range(an + ns + ar):
        name, pos = _name(buf, pos)
        rtype, _, _, rdlen = struct.unpack_from("!HHIH", buf, pos)
        pos += 10
        yield name, rtype, buf, pos, rdlen
        pos += rdlen


def mdns_browse(timeout=3.0):
    """{node_id: (ip, port)} for every _esp_local_ctrl service that answers."""
    qname = b"".join(bytes([len(p)]) + p.encode() for p in SERVICE.strip(".").split(".")) + b"\0"
    query = struct.pack("!6H", 0, 0, 1, 0, 0, 0) + qname + struct.pack("!HH", 12, 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.settimeout(0.5)
    sock.sendto(query, ("224.0.0.251", 5353))
    ptr, srv, txt, addr = set(), {}, {}, {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            buf, src = sock.recvfrom(9000)
        except socket.timeout:
            continue
        try:
            for name, rtype, b, pos, rdlen in _records(buf):
                if rtype == 12 and name.lower() == SERVICE.lower():
                    ptr.add(_name(b, pos)[0])
                elif rtype == 33:
                    port = struct.unpack_from("!H", b, pos + 4)[0]
                    srv[name] = (_name(b, pos + 6)[0], port)
                elif rtype == 16:
                    items, p = {}, pos
                    while p < pos + rdlen:
                        n = b[p]
                        k, _, v = b[p + 1 : p + 1 + n].decode(errors="replace").partition("=")
                        items[k] = v
                        p += 1 + n
                    txt[name] = items
                elif rtype == 1 and rdlen == 4:
                    addr[name] = socket.inet_ntoa(b[pos : pos + 4])
            for name in ptr:
                addr.setdefault(srv.get(name, ("", 0))[0], src[0])
        except (IndexError, struct.error):
            continue
    sock.close()
    found = {}
    for name in ptr:
        target, port = srv.get(name, (None, None))
        node_id = txt.get(name, {}).get("node_id")
        if node_id and target:
            found[node_id] = (addr.get(target), port)
    return found


# --- local control -------------------------------------------------------------


class Device:
    """Blocking esp_local_ctrl client, same protocol as custom_components/.../local.py."""

    def __init__(self, host, port, pop, sec_type):
        self.conn = HTTPConnection(host, port, timeout=5)
        self.cookie = None
        self.sec = lp.make_security(sec_type, pop)
        self.indices = {}

    def post(self, endpoint, body):
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/plain"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        self.conn.request("POST", f"/esp_local_ctrl/{endpoint}", body, headers)
        resp = self.conn.getresponse()
        data = resp.read()
        if cookie := resp.getheader("Set-Cookie"):
            self.cookie = cookie.split(";")[0]
        if resp.status != 200:
            raise lp.SinapseLocalError(f"{endpoint}: HTTP {resp.status}")
        return data

    def control(self, message):
        return self.sec.decrypt(self.post("control", self.sec.encrypt(message)))

    def handshake(self):
        response = None
        while (request := self.sec.next_request(response)) is not None:
            response = self.post("session", request)
        count = lp.decode_property_count(self.control(lp.encode_get_property_count()))
        props = lp.decode_property_values(
            self.control(lp.encode_get_property_values(list(range(count))))
        )
        self.indices = {p.name: i for i, p in enumerate(props)}
        return {p.name: json.loads(p.value) for p in props}

    def params(self):
        (prop,) = lp.decode_property_values(
            self.control(lp.encode_get_property_values([self.indices["params"]]))
        )
        return json.loads(prop.value)

    def set_params(self, payload):
        value = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        lp.decode_set_property_values(
            self.control(lp.encode_set_property_values({self.indices["params"]: value}))
        )


def diff(old, new, prefix=""):
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            yield from diff(a, b, f"{prefix}{key}.")
        elif a != b:
            yield f"{prefix}{key}: {redact({key: a})[key]!r} -> {redact({key: b})[key]!r}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="device IP (skip mDNS)")
    ap.add_argument("--port", type=int, help="device port (default: from mDNS, else 8080)")
    ap.add_argument("--node", help="node id, if the account has several")
    ap.add_argument("--set", dest="payload", help='JSON payload, e.g. \'{"Cromoterapia": {"Status": true}}\'')
    ap.add_argument("--watch", type=float, default=0, help="seconds to poll params and print changes")
    ap.add_argument("--interval", type=float, default=1.0, help="poll interval for --watch")
    args = ap.parse_args()

    user = os.environ.get("SINAPSE_USER") or input("Sinapse Home e-mail: ")
    pwd = os.environ.get("SINAPSE_PASS") or getpass.getpass("Password: ")
    _, nodes = cloud_nodes(user, pwd)
    nodes = [n for n in nodes if not args.node or n["id"] == args.node]
    if not nodes:
        sys.exit("No node found on the account")
    node = nodes[0]
    creds = local_credentials(node)
    if creds is None:
        sys.exit(f"Node {node['id']} has no Local Control service")
    pop, sec_type = creds
    print(f"Cloud: node_id={node['id']} security={sec_type} pop={'set' if pop else 'empty'}")

    t = time.monotonic()
    found = mdns_browse()
    print(f"mDNS ({time.monotonic() - t:.1f}s): "
          + (", ".join(f"{k}={v[0]}:{v[1]}" for k, v in found.items()) or "nothing answered"))
    host, port = args.host, args.port
    if not host:
        if node["id"] not in found:
            sys.exit("Node not found over mDNS; pass --host (and --port)")
        host, port = found[node["id"]][0], port or found[node["id"]][1]
    port = port or found.get(node["id"], (None, 8080))[1] or 8080
    print(f"Device: {host}:{port}")

    dev = Device(host, port, pop, sec_type)
    t = time.monotonic()
    props = dev.handshake()
    print(f"Handshake + property read: {(time.monotonic() - t) * 1000:.0f} ms, properties {list(dev.indices)}")
    local_id = props.get("config", {}).get("node_id")
    if local_id != node["id"]:
        sys.exit(f"{host}:{port} answers as node {local_id}, not {node['id']}")
    for name, value in props.items():
        print(f"\n== {name}\n{json.dumps(redact(value), indent=2, ensure_ascii=False)}")

    t = time.monotonic()
    current = dev.params()
    print(f"\nparams read: {(time.monotonic() - t) * 1000:.0f} ms")

    if args.payload:
        payload = json.loads(args.payload)
        t = time.monotonic()
        dev.set_params(payload)
        print(f"set {args.payload}: {(time.monotonic() - t) * 1000:.0f} ms")

    if args.watch:
        print(f"\nWatching params for {args.watch:.0f} s (every {args.interval} s)...")
        start = time.monotonic()
        while time.monotonic() - start < args.watch:
            time.sleep(args.interval)
            new = dev.params()
            for line in diff(current, new):
                print(f"[{time.strftime('%H:%M:%S')} +{time.monotonic() - start:6.1f}s] {line}")
            current = new


if __name__ == "__main__":
    main()
