"""Run Decky fixtures with a Python audit guard; child processes are not isolated."""

from pathlib import Path
import ipaddress
import socket
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
BLOCKED = frozenset({
    "socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
    "socket.gethostbyaddr", "socket.getnameinfo", "socket.sendto",
})


def loopback(value):
    try:
        return ipaddress.ip_address(value).is_loopback
    except (ValueError, TypeError):
        return False


def guard(event, args):
    if event in {"socket.connect", "socket.sendto"}:
        connection, address = args[0], args[-1]
        if connection.family == socket.AF_UNIX:
            return
        if isinstance(address, tuple) and loopback(address[0]):
            return
    if event in {"socket.getaddrinfo", "socket.gethostbyname"} and loopback(args[0]):
        return
    if event in BLOCKED:
        raise RuntimeError("Offline assessment check blocked network operation: " + event)


def reverse_lookup(address):
    if loopback(address):
        return "localhost", [], [address]
    raise RuntimeError("Offline assessment check blocked network operation: socket.gethostbyaddr")


def check_guard():
    probes = (
        lambda: socket.getaddrinfo("fixture.invalid", 443),
        lambda: socket.gethostbyname("fixture.invalid"),
        lambda: socket.gethostbyaddr("192.0.2.1"),
        lambda: socket.getnameinfo(("192.0.2.1", 443), 0),
    )
    with socket.socket() as stream, socket.socket(type=socket.SOCK_DGRAM) as datagram:
        probes += (
            lambda: stream.connect(("192.0.2.1", 9)),
            lambda: datagram.sendto(b"fixture", ("192.0.2.1", 9)),
        )
        for probe in probes:
            try:
                probe()
            except RuntimeError as error:
                if not str(error).startswith("Offline assessment check blocked network operation:"):
                    raise
            else:
                raise RuntimeError("Offline assessment guard failed its self-check")


def main():
    sys.addaudithook(guard)
    socket.gethostbyaddr = reverse_lookup
    check_guard()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_fy_decky_*.py")
    if suite.countTestCases() == 0:
        raise RuntimeError("No Decky assessment or source-port fixtures were discovered")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
