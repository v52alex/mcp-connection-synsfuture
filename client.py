"""Cliente MCP de desarrollo para mcp-connection-synsfuture mediante stdio."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters, stdio_client

PROJECT_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cliente MCP de desarrollo")
    parser.add_argument(
        "tool",
        nargs="?",
        choices=("list", "connect_connection_profile"),
        default="list",
        help="Acción MCP a ejecutar; por defecto lista las herramientas.",
    )
    parser.add_argument(
        "profile_id",
        nargs="?",
        help="Identificador explícito del perfil para connect_connection_profile.",
    )
    parser.add_argument(
        "--profiles-file",
        type=Path,
        help="Archivo TOML local de perfiles no secretos.",
    )
    return parser.parse_args()


def server_parameters(profiles_file: Path | None) -> StdioServerParameters:
    environment = dict(os.environ)
    if profiles_file is not None:
        environment["MCP_CONNECTION_PROFILES_FILE"] = str(profiles_file.expanduser().resolve())
    return StdioServerParameters(
        command=sys.executable,
        args=[str(PROJECT_DIR / "main.py")],
        cwd=PROJECT_DIR,
        env=environment,
    )


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


async def run(args: argparse.Namespace) -> None:
    async with (
        stdio_client(server_parameters(args.profiles_file)) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools_result = await session.list_tools()
        available_tools = [tool.name for tool in tools_result.tools]
        if args.tool == "list":
            print_json({"tools": available_tools})
            return
        if args.tool not in available_tools:
            raise RuntimeError(f"Tool {args.tool!r} is not exposed by the MCP server")
        arguments = {} if args.profile_id is None else {"profile_id": args.profile_id}
        result = await session.call_tool(args.tool, arguments=arguments)
        print_json(
            {
                "tool": args.tool,
                "is_error": result.is_error,
                "result": result.structured_content,
            }
        )
        if result.is_error:
            raise SystemExit(1)


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
