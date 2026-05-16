"""Interactive console for LwM2M resource access."""

from __future__ import annotations

from dawnpy_lwm2m.client import Lwm2mClient, format_value


class Lwm2mConsole:  # pragma: no cover
    """Small interactive console for a registered LwM2M client."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 5683,
        endpoint: str | None = None,
        timeout: float = 30.0,
        descriptor_path: str | None = None,
    ) -> None:
        """Initialize console state and its LwM2M client."""
        self.client = Lwm2mClient(
            host=host,
            port=port,
            endpoint=endpoint,
            timeout=timeout,
            descriptor_path=descriptor_path,
        )
        self.timeout = timeout
        self.running = True

    def run(self) -> None:
        """Wait for registration and run the command loop."""
        reg = self.client.connect(timeout=self.timeout)
        print(f"endpoint={reg.endpoint}")
        print(f"address={reg.address[0]}:{reg.address[1]}")
        print(f"location={reg.location}")
        print(reg.links)
        self.show_menu()

        try:
            while self.running:
                try:
                    line = input("\nEnter LwM2M command (h for help): ")
                except EOFError:
                    break
                self.handle(line.strip())
        finally:
            self.client.close()

    def show_menu(self) -> None:
        """Print available console commands."""
        print("\nLwM2M Console - Commands")
        print("  d: Show registration links")
        print("  l: List descriptor IO bindings")
        print("  r <path|io_id>: Read resource")
        print("  w <path|io_id> <value>: Write resource")
        print("  m <path|io_id>[,<path|io_id>...]: Monitor resources")
        print("  h: Show help")
        print("  q: Quit")

    def handle(self, line: str) -> None:
        """Handle one console command line."""
        if not line:
            return
        cmd, _, args = line.partition(" ")
        if cmd in ("q", "quit", "exit"):
            self.running = False
        elif cmd == "h":
            self.show_menu()
        elif cmd == "d":
            if self.client.registration is not None:
                print(self.client.registration.links)
        elif cmd == "l":
            self.list_bindings()
        elif cmd == "r":
            self.read(args)
        elif cmd == "w":
            self.write(args)
        elif cmd == "m":
            self.monitor(args)
        else:
            print(f"ERROR: Unknown command: {cmd}")

    def list_bindings(self) -> None:
        """Print descriptor-backed IO bindings."""
        bindings = self.client.list_bindings()
        if not bindings:
            print("No descriptor bindings loaded")
            return
        for binding in bindings:
            mode = "rw" if binding.rw else "ro"
            print(
                f"{binding.io_id}: {binding.path} dtype={binding.dtype} {mode}"
            )

    def read(self, args: str) -> None:
        """Read one resource."""
        target = args.strip()
        if not target:
            print("ERROR: Usage: r <path|io_id>")
            return
        try:
            path = self.client.resolve_path(target)
            value = self.client.read_typed(path)
        except (RuntimeError, TimeoutError, ValueError) as exc:
            print(f"ERROR: {exc}")
            return
        print(f"{path}: {format_value(value)}")

    def write(self, args: str) -> None:
        """Write one resource."""
        parts = args.split(maxsplit=1)
        if len(parts) != 2:
            print("ERROR: Usage: w <path|io_id> <value>")
            return
        try:
            path = self.client.resolve_path(parts[0])
            self.client.write_typed(path, parts[1])
            value = self.client.read_typed(path)
        except (RuntimeError, TimeoutError, ValueError) as exc:
            print(f"ERROR: {exc}")
            return
        print(f"{path}: {format_value(value)}")

    def monitor(self, args: str) -> None:
        """Monitor one or more resources."""
        targets = [item.strip() for item in args.split(",") if item.strip()]
        if not targets:
            print("ERROR: Usage: m <path|io_id>[,<path|io_id>...]")
            return
        try:
            self.client.monitor(targets)
        except (RuntimeError, TimeoutError, ValueError) as exc:
            print(f"ERROR: {exc}")


def run_console(
    *,
    host: str = "0.0.0.0",
    port: int = 5683,
    endpoint: str | None = None,
    timeout: float = 30.0,
    descriptor_path: str | None = None,
) -> None:  # pragma: no cover
    """Run the interactive LwM2M console."""
    console = Lwm2mConsole(
        host=host,
        port=port,
        endpoint=endpoint,
        timeout=timeout,
        descriptor_path=descriptor_path,
    )
    console.run()
