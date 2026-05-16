# tools/dawnpy-lwm2m/src/dawnpy_lwm2m/commands/cmd_lwm2m.py
#
# SPDX-License-Identifier: Apache-2.0
#

"""Module containing LwM2M command."""

import click
from dawnpy.cli.environment import Environment, pass_environment
from dawnpy.cli.options import configure_cli_logging

from dawnpy_lwm2m.console import run_console

###############################################################################
# Command: cmd_lwm2m
###############################################################################


@click.command(name="lwm2m")
@click.argument(
    "descriptor",
    type=click.Path(resolve_path=False),
    required=False,
)
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=5683, show_default=True, type=int)
@click.option("--endpoint", default=None, help="Expected LwM2M endpoint name")
@click.option(
    "--timeout",
    default=30.0,
    show_default=True,
    type=float,
    help="Registration and request timeout in seconds",
)
@click.option(
    "--debug/--no-debug",
    default=False,
    is_flag=True,
    envvar="DAWNPY_DEBUG",
)
@pass_environment
def cmd_lwm2m(
    ctx: Environment,
    descriptor: str | None,
    host: str,
    port: int,
    endpoint: str | None,
    timeout: float,
    debug: bool,
) -> bool:
    """Run LwM2M console for descriptor-driven Wakaama access."""
    ctx.debug = debug
    configure_cli_logging(debug)

    try:
        run_console(
            host=host,
            port=port,
            endpoint=endpoint,
            timeout=timeout,
            descriptor_path=descriptor,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    return True
