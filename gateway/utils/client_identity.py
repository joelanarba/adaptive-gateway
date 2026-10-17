"""Derive client identity only through explicitly trusted proxy hops."""

from ipaddress import ip_address, ip_network

from starlette.requests import Request

from config import settings


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    networks = [ip_network(net) for net in settings.trusted_proxy_networks]

    def trusted(value: str) -> bool:
        try:
            address = ip_address(value)
            return any(address in net for net in networks)
        except ValueError:
            return False

    # Walk right-to-left; stop at the first untrusted hop. A direct client
    # cannot assert its identity by supplying X-Forwarded-For.
    if not trusted(peer):
        return peer
    chain = request.headers.get("x-forwarded-for", "").split(",")
    if len(chain) > 32:
        return peer
    current = peer
    for hop in reversed(chain):
        if not trusted(current):
            break
        try:
            current = str(ip_address(hop.strip()))
        except ValueError:
            return peer
    return current
