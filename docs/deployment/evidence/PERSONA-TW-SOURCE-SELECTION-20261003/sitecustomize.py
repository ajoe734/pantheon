"""Task-only offline guard; inherited by Python subprocesses via PYTHONPATH."""
import ipaddress
import socket
import sys


def audit(event, args):
    if event == "socket.getaddrinfo":
        host = args[0]
    elif event in {"socket.connect", "socket.sendto"}:
        if args[0].family not in {socket.AF_INET, socket.AF_INET6}:
            return
        host = args[1][0] if event == "socket.connect" else args[-1][0]
    else:
        return
    if host == "localhost":
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise PermissionError("TASK_OFFLINE_DENIED: non-loopback network")


sys.addaudithook(audit)
