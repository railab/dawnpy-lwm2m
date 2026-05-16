import asyncio
import socket

import aiocoap
import aiocoap.resource as resource
from aiocoap.numbers.contentformat import ContentFormat

from dawnpy_lwm2m.server import Lwm2mBootstrapServer, Lwm2mTestServer


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def test_aiocoap_registration_round_trip():
    async def _register(port: int):
        context = await aiocoap.Context.create_client_context()
        try:
            request = aiocoap.Message(
                code=aiocoap.POST,
                payload=b"</3/0>,</3303/0>",
                uri=f"coap://127.0.0.1:{port}/rd?ep=dawn",
            )
            request.opt.content_format = ContentFormat.LINKFORMAT
            return await context.request(request).response
        finally:
            await context.shutdown()

    port = _free_udp_port()
    with Lwm2mTestServer(host="127.0.0.1", port=port, timeout=2.0) as server:
        response = asyncio.run(_register(port))
        registration = server.wait_for_registration(endpoint="dawn", timeout=2)

    assert response.code == aiocoap.CREATED
    assert tuple(response.opt.location_path) == ("rd", "4")
    assert registration.endpoint == "dawn"
    assert registration.links == "</3/0>,</3303/0>"
    assert registration.address[0] == "127.0.0.1"


class _BootstrapClientResource(resource.Resource):
    def __init__(self, events: list[tuple[str, bytes, int | None]]) -> None:
        super().__init__()
        self.events = events

    async def render_delete(self, request: aiocoap.Message) -> aiocoap.Message:
        self.events.append(("delete-security", request.payload, None))
        return aiocoap.Message(code=aiocoap.DELETED)

    async def render_put(self, request: aiocoap.Message) -> aiocoap.Message:
        content_format = request.opt.content_format
        self.events.append(
            (
                "put",
                request.payload,
                int(content_format) if content_format is not None else None,
            )
        )
        return aiocoap.Message(code=aiocoap.CHANGED)


class _BootstrapFinishResource(resource.Resource):
    def __init__(self, event: asyncio.Event) -> None:
        super().__init__()
        self.event = event

    async def render_post(self, request: aiocoap.Message) -> aiocoap.Message:
        self.event.set()
        return aiocoap.Message(code=aiocoap.CHANGED)


def test_aiocoap_bootstrap_round_trip():
    async def _request_bootstrap(bootstrap_port: int, client_port: int):
        events: list[tuple[str, bytes, int | None]] = []
        finished = asyncio.Event()
        site = resource.Site()
        site.add_resource(["0"], _BootstrapClientResource(events))
        site.add_resource(["0", "1"], _BootstrapClientResource(events))
        site.add_resource(["1", "1"], _BootstrapClientResource(events))
        site.add_resource(["bs"], _BootstrapFinishResource(finished))
        context = await aiocoap.Context.create_server_context(
            site,
            bind=("127.0.0.1", client_port),
        )
        try:
            request = aiocoap.Message(
                code=aiocoap.POST,
                uri=f"coap://127.0.0.1:{bootstrap_port}/bs?ep=dawn",
            )
            response = await context.request(request).response
            await asyncio.wait_for(finished.wait(), timeout=2.0)
            return response, events
        finally:
            await context.shutdown()

    bootstrap_port = _free_udp_port()
    client_port = _free_udp_port()
    with Lwm2mBootstrapServer(
        host="127.0.0.1",
        port=bootstrap_port,
        final_host="127.0.0.1",
        final_port=5683,
        timeout=2.0,
    ) as server:
        response, events = asyncio.run(
            _request_bootstrap(bootstrap_port, client_port)
        )
        request = server.wait_for_bootstrap(endpoint="dawn", timeout=2.0)

    assert response.code == aiocoap.CHANGED
    assert request.endpoint == "dawn"
    assert request.address[0] == "127.0.0.1"
    assert events[0] == ("delete-security", b"", None)
    assert events[1][0] == "put"
    assert events[1][2] == 11542
    assert b"coap://127.0.0.1:5683" in events[1][1]
    assert events[2][0] == "put"
    assert events[2][2] == 11542
    assert events[2][1].endswith(b"\xc1\x07U")
