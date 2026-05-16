"""LwM2M server helpers built on aiocoap."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, cast

import aiocoap  # type: ignore[import-untyped]
import aiocoap.resource as resource  # type: ignore[import-untyped]
from aiocoap.numbers.contentformat import (  # type: ignore[import-untyped]
    ContentFormat,
)


@dataclass
class Lwm2mRegistration:
    """One client registration accepted by the test server."""

    endpoint: str
    address: tuple[str, int]
    location: str
    links: str
    hostinfo: str


@dataclass
class Lwm2mBootstrapRequest:
    """One bootstrap request accepted by the test bootstrap server."""

    endpoint: str
    address: tuple[str, int]
    hostinfo: str


class Lwm2mObservation:
    """Synchronous handle for one active LwM2M Observe request."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        protocol_request: Any,
    ) -> None:
        """Bind the observation to the aiocoap request lifecycle."""
        self._loop = loop
        self._protocol_request = protocol_request
        self._updates: queue.Queue[bytes | BaseException] = queue.Queue()

    def next_payload(self, timeout: float | None = None) -> bytes:
        """Wait for the next observed payload."""
        try:
            item = self._updates.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(
                "timed out waiting for LwM2M notification"
            ) from exc
        if isinstance(item, BaseException):
            raise item
        return item

    def cancel(self) -> None:
        """Cancel the remote observation."""
        self._loop.call_soon_threadsafe(
            self._protocol_request.observation.cancel
        )

    def _add_response(self, response: aiocoap.Message) -> None:
        if response.code != aiocoap.CONTENT:
            self._updates.put(
                RuntimeError(f"observe failed with CoAP code {response.code}")
            )
            return
        self._updates.put(cast(bytes, response.payload))


class Lwm2mTestServer:
    """Small LwM2M server for registration and read/write checks."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5683,
        timeout: float = 2.0,
    ) -> None:
        """Initialize server configuration."""
        self.host = host
        self.port = port
        self.timeout = timeout
        self.registration: Lwm2mRegistration | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._context: aiocoap.Context | None = None
        self._expected_endpoint: str | None = None
        self._ready: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)
        self._registrations: queue.Queue[Lwm2mRegistration] = queue.Queue()

    def __enter__(self) -> "Lwm2mTestServer":
        """Start the server for use as a context manager."""
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        """Close the server when leaving a context manager."""
        self.close()

    def start(self) -> None:
        """Start the aiocoap server context."""
        if self._loop is not None:
            return

        loop = asyncio.new_event_loop()
        self._loop = loop
        self._thread = threading.Thread(
            target=self._run_loop,
            name="dawnpy-lwm2m",
            daemon=True,
        )
        self._thread.start()
        ready = self._ready.get(timeout=self.timeout)
        if ready is not None:
            self.close()
            raise RuntimeError("failed to start LwM2M server") from ready

    def close(self) -> None:
        """Close the aiocoap server context."""
        loop = self._loop
        thread = self._thread
        if loop is None:
            return

        future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
        try:
            future.result(timeout=self.timeout)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=self.timeout)
            self._loop = None
            self._thread = None

    @property
    def bound_port(self) -> int:
        """Return the configured UDP port."""
        return self.port

    def wait_for_registration(
        self,
        endpoint: str | None = None,
        timeout: float = 10.0,
    ) -> Lwm2mRegistration:
        """Wait until a client registers on /rd."""
        self.start()
        self._expected_endpoint = endpoint
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for LwM2M registration")
            try:
                registration = self._registrations.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(
                    "timed out waiting for LwM2M registration"
                ) from exc
            if endpoint is None or registration.endpoint == endpoint:
                self.registration = registration
                return registration

    def read_path(self, path: str, timeout: float | None = None) -> bytes:
        """Read an LwM2M resource path from the registered client."""
        response = self._request(
            aiocoap.GET,
            path,
            timeout=timeout,
            accept=ContentFormat.TEXT,
        )
        if response.code != aiocoap.CONTENT:
            raise RuntimeError(
                f"read {path} failed with CoAP code {response.code}"
            )
        return cast(bytes, response.payload)

    def discover_path(self, path: str, timeout: float | None = None) -> bytes:
        """Discover an LwM2M object, instance, or resource path."""
        response = self._request(
            aiocoap.GET,
            path,
            timeout=timeout,
            accept=ContentFormat.LINKFORMAT,
        )
        if response.code != aiocoap.CONTENT:
            raise RuntimeError(
                f"discover {path} failed with CoAP code {response.code}"
            )
        return cast(bytes, response.payload)

    def write_path(
        self,
        path: str,
        payload: bytes,
        timeout: float | None = None,
    ) -> None:
        """Write an LwM2M resource path on the registered client."""
        response = self._request(aiocoap.PUT, path, payload, timeout=timeout)
        if response.code not in (aiocoap.CHANGED, aiocoap.VALID):
            raise RuntimeError(
                f"write {path} failed with CoAP code {response.code}"
            )

    def execute_path(
        self,
        path: str,
        payload: bytes = b"",
        timeout: float | None = None,
    ) -> None:
        """Execute an LwM2M resource path on the registered client."""
        response = self._request(aiocoap.POST, path, payload, timeout=timeout)
        if response.code != aiocoap.CHANGED:
            raise RuntimeError(
                f"execute {path} failed with CoAP code {response.code}"
            )

    def observe_path(
        self,
        path: str,
        timeout: float | None = None,
    ) -> Lwm2mObservation:
        """Start observing an LwM2M resource path."""
        if self.registration is None:
            raise RuntimeError("no LwM2M client has registered")
        if self._loop is None:
            raise RuntimeError("server is not started")
        future = asyncio.run_coroutine_threadsafe(
            self._observe_async(path),
            self._loop,
        )
        return future.result(timeout=timeout or self.timeout)

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start_async())
        except Exception as exc:  # pragma: no cover
            self._ready.put(exc)
            return
        self._ready.put(None)
        self._loop.run_forever()
        self._loop.close()

    async def _start_async(self) -> None:
        site = resource.Site()
        site.add_resource(["rd"], _RegistrationResource(self))
        self._context = await aiocoap.Context.create_server_context(
            site,
            bind=(self.host, self.port),
        )

    async def _shutdown(self) -> None:
        if self._context is not None:
            await self._context.shutdown()
            self._context = None

    def _request(
        self,
        code: aiocoap.Code,
        path: str,
        payload: bytes = b"",
        timeout: float | None = None,
        accept: ContentFormat | None = None,
    ) -> aiocoap.Message:
        if self.registration is None:
            raise RuntimeError("no LwM2M client has registered")
        if self._loop is None:
            raise RuntimeError("server is not started")
        future = asyncio.run_coroutine_threadsafe(
            self._request_async(code, path, payload, accept),
            self._loop,
        )
        return future.result(timeout=timeout or self.timeout)

    async def _request_async(
        self,
        code: aiocoap.Code,
        path: str,
        payload: bytes,
        accept: ContentFormat | None,
    ) -> aiocoap.Message:
        if self._context is None:
            raise RuntimeError("server is not started")
        assert self.registration is not None
        request = aiocoap.Message(
            code=code,
            payload=payload,
            uri=f"coap://{self.registration.hostinfo}{path}",
        )
        if code == aiocoap.PUT or payload:
            request.opt.content_format = ContentFormat.TEXT
        if accept is not None:
            request.opt.accept = accept
        return await self._context.request(request).response

    async def _observe_async(self, path: str) -> Lwm2mObservation:
        if self._context is None:
            raise RuntimeError("server is not started")
        assert self._loop is not None
        assert self.registration is not None
        request = aiocoap.Message(
            code=aiocoap.GET,
            uri=f"coap://{self.registration.hostinfo}{path}",
        )
        request.opt.observe = 0
        request.opt.accept = ContentFormat.TEXT

        protocol_request = self._context.request(request)
        observation = Lwm2mObservation(self._loop, protocol_request)
        response = await protocol_request.response
        observation._add_response(response)
        if response.code != aiocoap.CONTENT:
            observation.cancel()
            raise RuntimeError(
                f"observe {path} failed with CoAP code {response.code}"
            )
        protocol_request.observation.register_callback(
            observation._add_response
        )
        return observation


class Lwm2mBootstrapServer:
    """Small LwM2M bootstrap server for no-security test provisioning."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5685,
        final_host: str = "127.0.0.1",
        final_port: int = 5683,
        short_server_id: int = 123,
        lifetime: int = 60,
        security_instance: int = 1,
        server_instance: int = 1,
        timeout: float = 2.0,
    ) -> None:
        """Initialize bootstrap server configuration."""
        self.host = host
        self.port = port
        self.final_host = final_host
        self.final_port = final_port
        self.short_server_id = short_server_id
        self.lifetime = lifetime
        self.security_instance = security_instance
        self.server_instance = server_instance
        self.timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._context: aiocoap.Context | None = None
        self._ready: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)
        self._requests: queue.Queue[Lwm2mBootstrapRequest] = queue.Queue()
        self._completed: queue.Queue[BaseException | None] = queue.Queue()

    def __enter__(self) -> "Lwm2mBootstrapServer":
        """Start the server for use as a context manager."""
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        """Close the server when leaving a context manager."""
        self.close()

    def start(self) -> None:
        """Start the aiocoap server context."""
        if self._loop is not None:
            return

        loop = asyncio.new_event_loop()
        self._loop = loop
        self._thread = threading.Thread(
            target=self._run_loop,
            name="dawnpy-lwm2m-bootstrap",
            daemon=True,
        )
        self._thread.start()
        ready = self._ready.get(timeout=self.timeout)
        if ready is not None:
            self.close()
            raise RuntimeError(
                "failed to start LwM2M bootstrap server"
            ) from ready

    def close(self) -> None:
        """Close the aiocoap server context."""
        loop = self._loop
        thread = self._thread
        if loop is None:
            return

        future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
        try:
            future.result(timeout=self.timeout)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=self.timeout)
            self._loop = None
            self._thread = None

    def wait_for_bootstrap(
        self,
        endpoint: str | None = None,
        timeout: float = 10.0,
    ) -> Lwm2mBootstrapRequest:
        """Wait until bootstrap request and provisioning complete."""
        self.start()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for LwM2M bootstrap")
            try:
                request = self._requests.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(
                    "timed out waiting for LwM2M bootstrap"
                ) from exc
            if endpoint is None or request.endpoint == endpoint:
                break

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for LwM2M bootstrap")
        try:
            result = self._completed.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError(
                "timed out waiting for LwM2M bootstrap"
            ) from exc
        if result is not None:
            raise result
        return request

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start_async())
        except Exception as exc:  # pragma: no cover
            self._ready.put(exc)
            return
        self._ready.put(None)
        self._loop.run_forever()
        self._loop.close()

    async def _start_async(self) -> None:
        site = resource.Site()
        site.add_resource(["bs"], _BootstrapResource(self))
        self._context = await aiocoap.Context.create_server_context(
            site,
            bind=(self.host, self.port),
        )

    async def _shutdown(self) -> None:
        if self._context is not None:
            await self._context.shutdown()
            self._context = None

    async def _provision_client(self, hostinfo: str) -> None:
        try:
            await self._request(aiocoap.DELETE, hostinfo, "/0")
            await self._request(
                aiocoap.PUT,
                hostinfo,
                f"/0/{self.security_instance}",
                _security_tlv(
                    self.final_host,
                    self.final_port,
                    self.short_server_id,
                ),
                content_format=ContentFormat(11542),
            )
            await self._request(
                aiocoap.PUT,
                hostinfo,
                f"/1/{self.server_instance}",
                _server_tlv(self.short_server_id, self.lifetime),
                content_format=ContentFormat(11542),
            )
            await self._request(aiocoap.POST, hostinfo, "/bs")
        except Exception as exc:
            self._completed.put(exc)
        else:
            self._completed.put(None)

    async def _request(
        self,
        code: aiocoap.Code,
        hostinfo: str,
        path: str,
        payload: bytes = b"",
        content_format: ContentFormat | None = None,
    ) -> aiocoap.Message:
        if self._context is None:
            raise RuntimeError("bootstrap server is not started")
        request = aiocoap.Message(
            code=code,
            payload=payload,
            uri=f"coap://{hostinfo}{path}",
        )
        if content_format is not None:
            request.opt.content_format = content_format
        response = await self._context.request(request).response
        if code == aiocoap.DELETE and response.code != aiocoap.DELETED:
            raise RuntimeError(
                f"bootstrap DELETE {path} failed: {response.code}"
            )
        if code == aiocoap.PUT and response.code not in (
            aiocoap.CREATED,
            aiocoap.CHANGED,
        ):
            raise RuntimeError(f"bootstrap PUT {path} failed: {response.code}")
        if code == aiocoap.POST and response.code != aiocoap.CHANGED:
            raise RuntimeError(
                f"bootstrap POST {path} failed: {response.code}"
            )
        return response


class _RegistrationResource(resource.Resource):  # type: ignore[misc]
    """Handle LwM2M client registration requests."""

    def __init__(self, server: Lwm2mTestServer) -> None:
        """Bind the resource to its owning server."""
        super().__init__()
        self.server = server

    async def render_post(self, request: aiocoap.Message) -> aiocoap.Message:
        """Accept a client registration at /rd."""
        query = _query_dict(request.opt.uri_query)
        endpoint = query.get("ep", "")
        if (
            self.server._expected_endpoint is not None
            and endpoint != self.server._expected_endpoint
        ):
            return aiocoap.Message(code=aiocoap.BAD_REQUEST)

        location = f"/rd/{len(endpoint) or 1}"
        registration = Lwm2mRegistration(
            endpoint=endpoint,
            address=_remote_address(request.remote),
            location=location,
            links=request.payload.decode(errors="replace"),
            hostinfo=_remote_hostinfo(request.remote),
        )
        self.server.registration = registration
        self.server._registrations.put(registration)

        response = aiocoap.Message(code=aiocoap.CREATED)
        response.opt.location_path = ("rd", str(len(endpoint) or 1))
        return response


class _BootstrapResource(resource.Resource):  # type: ignore[misc]
    """Handle LwM2M client bootstrap requests."""

    def __init__(self, server: Lwm2mBootstrapServer) -> None:
        """Bind the resource to its owning server."""
        super().__init__()
        self.server = server

    async def render_post(self, request: aiocoap.Message) -> aiocoap.Message:
        """Accept a client bootstrap request at /bs."""
        query = _query_dict(request.opt.uri_query)
        hostinfo = _remote_hostinfo(request.remote)
        self.server._requests.put(
            Lwm2mBootstrapRequest(
                endpoint=query.get("ep", ""),
                address=_remote_address(request.remote),
                hostinfo=hostinfo,
            )
        )
        asyncio.create_task(self.server._provision_client(hostinfo))
        return aiocoap.Message(code=aiocoap.CHANGED)


def _tlv_record(type_bits: int, item_id: int, payload: bytes) -> bytes:
    header = type_bits
    id_bytes: bytes
    length_bytes = b""

    if item_id > 0xFF:
        header |= 0x20
        id_bytes = item_id.to_bytes(2, "big")
    else:
        id_bytes = bytes([item_id])

    length = len(payload)
    if length <= 7:
        header |= length
    elif length <= 0xFF:
        header |= 0x08
        length_bytes = bytes([length])
    elif length <= 0xFFFF:
        header |= 0x10
        length_bytes = length.to_bytes(2, "big")
    else:
        header |= 0x18
        length_bytes = length.to_bytes(3, "big")

    return bytes([header]) + id_bytes + length_bytes + payload


def _tlv_resource(resource_id: int, payload: bytes) -> bytes:
    return _tlv_record(0xC0, resource_id, payload)


def _tlv_int(resource_id: int, value: int) -> bytes:
    if value == 0:
        payload = b"\0"
    else:
        payload = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return _tlv_resource(resource_id, payload)


def _tlv_bool(resource_id: int, value: bool) -> bytes:
    return _tlv_resource(resource_id, b"\1" if value else b"\0")


def _tlv_str(resource_id: int, value: str) -> bytes:
    return _tlv_resource(resource_id, value.encode())


def _security_tlv(host: str, port: int, short_server_id: int) -> bytes:
    return b"".join(
        (
            _tlv_str(0, f"coap://{host}:{port}"),
            _tlv_bool(1, False),
            _tlv_int(2, 3),
            _tlv_int(10, short_server_id),
        )
    )


def _server_tlv(short_server_id: int, lifetime: int) -> bytes:
    return b"".join(
        (
            _tlv_int(0, short_server_id),
            _tlv_int(1, lifetime),
            _tlv_bool(6, False),
            _tlv_str(7, "U"),
        )
    )


def _query_dict(query: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in query:
        key, sep, value = item.partition("=")
        out[key] = value if sep else ""
    return out


def _remote_hostinfo(remote: Any) -> str:
    hostinfo = getattr(remote, "hostinfo", None)
    return str(hostinfo if hostinfo is not None else remote)


def _remote_address(remote: Any) -> tuple[str, int]:
    hostinfo = _remote_hostinfo(remote)
    host, sep, port = hostinfo.rpartition(":")
    if sep and port.isdigit():
        return host.strip("[]"), int(port)
    return hostinfo, 0
