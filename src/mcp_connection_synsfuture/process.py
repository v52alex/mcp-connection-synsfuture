"""Small async process boundary for fixed, non-shell commands."""

import asyncio
import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class ProcessRunner:
    async def run(
        self,
        args: list[str],
        timeout_seconds: float,
        input_data: bytes | None = None,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run fixed arguments without invoking a shell."""

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **environment} if environment else None,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input_data), timeout=timeout_seconds
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise TimeoutError from error
        return CommandResult(
            returncode=process.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )
