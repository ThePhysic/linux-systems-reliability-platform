"""Fixed-port lab-only collision helper, bounded independently of its runner."""

import os
import json
import signal
import socket
import threading


def main() -> int:
    if os.geteuid() == 0:
        raise RuntimeError("collision helper must run unprivileged")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        # Previous accepted connections can remain in TIME_WAIT after labsvc stops.
        # Both listeners use SO_REUSEADDR so the owned helper can bind immediately.
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", 18765))
        except OSError as exc:
            print(json.dumps({"event": "collision_bind_failed", "error": str(exc)}), flush=True)
            return 1
        listener.listen(4)
        print("lsrp collision helper owns 127.0.0.1:18765; expires in 15 seconds", flush=True)
        stop.wait(15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
