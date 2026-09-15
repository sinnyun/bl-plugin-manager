"""BlenderMCP 极简客户端：连 127.0.0.1:9876，发一条命令，打印响应。

用法:
    python mcp_client.py ping
    python mcp_client.py get_scene_info
    python mcp_client.py --code 'print(1+1)'
    python mcp_client.py --code-file path/to/code.py
"""

from __future__ import annotations

import json
import socket
import sys
import time

HOST = "127.0.0.1"
PORT = 9876


def send(command: dict, timeout: float = 120.0, retries: int = 1) -> dict:
    last = None
    for attempt in range(retries + 1):
        try:
            with socket.create_connection((HOST, PORT), timeout=timeout) as s:
                s.sendall(json.dumps(command).encode("utf-8"))
                buf = b""
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    try:
                        return json.loads(buf.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                if buf:
                    return json.loads(buf.decode("utf-8"))
                return {"status": "error", "message": "empty response"}
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(0.5)
    return {"status": "error", "message": f"connect failed: {last}"}


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    if args[0] == "--code":
        cmd = {"type": "execute_code", "params": {"code": args[1]}}
    elif args[0] == "--code-file":
        with open(args[1], "r", encoding="utf-8") as f:
            cmd = {"type": "execute_code", "params": {"code": f.read()}}
    elif args[0] in ("ping", "get_scene_info", "get_addon_info", "get_world_state_snapshot"):
        cmd = {"type": args[0], "params": {}}
    else:
        cmd = json.loads(args[0])

    resp = send(cmd)
    print(json.dumps(resp, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
