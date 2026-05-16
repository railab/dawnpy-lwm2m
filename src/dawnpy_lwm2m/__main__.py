"""Standalone CLI entry point for dawnpy-lwm2m."""

from dawnpy_lwm2m.commands.cmd_lwm2m import cmd_lwm2m


def main() -> None:
    """Run the LwM2M CLI."""
    cmd_lwm2m(prog_name="dawnpy-lwm2m")


if __name__ == "__main__":
    main()
