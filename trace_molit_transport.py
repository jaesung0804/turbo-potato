"""Trace the existing collector's DNS/TCP/TLS calls without changing transport."""
import functools
import json
import runpy
import socket
import ssl
import time

def trace(label, fn):
    @functools.wraps(fn)
    def measured(*args, **kwargs):
        started = time.monotonic()
        record = {"network_phase": label}
        print(json.dumps(dict(record, event="start")), flush=True)
        try:
            result = fn(*args, **kwargs)
            record["status"] = "success"
            return result
        except Exception as exc:
            record.update(status="failure", exception_type=type(exc).__name__,
                          errno=getattr(exc, "errno", None))
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            print(json.dumps(record), flush=True)
    return measured

if __name__ == "__main__":
    socket.getaddrinfo = trace("dns", socket.getaddrinfo)
    socket.create_connection = trace("tcp_including_dns", socket.create_connection)
    ssl.SSLContext.wrap_socket = trace("tls", ssl.SSLContext.wrap_socket)
    runpy.run_module("collect_molit_capital_csv", run_name="__main__")
