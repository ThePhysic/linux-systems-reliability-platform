"""Fixed-port lab-only collision helper, bounded independently of its runner."""

import os
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
        listener.bind(("127.0.0.1", 18765))
        listener.listen(4)
        print("lsrp collision helper owns 127.0.0.1:18765; expires in 15 seconds", flush=True)
        stop.wait(15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
