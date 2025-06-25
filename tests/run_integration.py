#!/usr/bin/env python3
"""Integration test runner script.

This script:
1. Starts fresh Docker containers using compose.yml
2. Waits for services to be ready
3. Runs integration tests
4. Cleans up containers afterward
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets


PROJECT_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = PROJECT_ROOT / "compose.yml"


class Colors:
    """ANSI color codes for terminal output."""

    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"
    END = "\033[0m"


def log(message: str, color: str = Colors.WHITE) -> None:
    """Print colored log message."""
    print(f"{color}{message}{Colors.END}")


def run_command(
    cmd: list[str], check: bool = True, capture_output: bool = False
) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    log(f"Running: {' '.join(cmd)}", Colors.CYAN)
    return subprocess.run(
        cmd, check=check, capture_output=capture_output, text=True, cwd=PROJECT_ROOT
    )


async def wait_for_mint(url: str, timeout: int = 60) -> bool:
    """Wait for mint to be ready."""
    log(f"Waiting for mint at {url}...", Colors.YELLOW)

    start_time = time.time()
    async with httpx.AsyncClient() as client:
        while time.time() - start_time < timeout:
            try:
                response = await client.get(f"{url}/v1/info", timeout=5.0)
                if response.status_code == 200:
                    info = response.json()
                    if info.get("name"):
                        log(f"✅ Mint ready: {info.get('name')}", Colors.GREEN)
                        return True
            except Exception:
                pass

            await asyncio.sleep(2)

    log(f"❌ Mint at {url} not ready after {timeout}s", Colors.RED)
    return False


async def wait_for_relay(url: str, timeout: int = 30) -> bool:
    """Wait for Nostr relay to be ready."""
    log(f"Waiting for relay at {url}...", Colors.YELLOW)

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            async with websockets.connect(url, ping_timeout=5) as ws:
                # Send a test REQ message
                test_req = ["REQ", "test", {"kinds": [1], "limit": 1}]
                await ws.send(json.dumps(test_req))

                # Wait for any response
                response = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(response)

                # Should get EOSE or EVENT
                if data[0] in ["EOSE", "EVENT"]:
                    log("✅ Relay ready", Colors.GREEN)
                    return True
        except Exception:
            pass

        await asyncio.sleep(2)

    log(f"❌ Relay at {url} not ready after {timeout}s", Colors.RED)
    return False


def cleanup_docker():
    """Clean up Docker containers and volumes."""
    log("🧹 Cleaning up Docker containers and volumes...", Colors.YELLOW)

    try:
        # Stop and remove containers
        run_command(
            ["docker-compose", "-f", str(COMPOSE_FILE), "down", "-v"], check=False
        )

        # Remove any orphaned containers
        run_command(["docker", "container", "prune", "-f"], check=False)

        # Remove unused volumes (be careful with this)
        run_command(["docker", "volume", "prune", "-f"], check=False)

        log("✅ Docker cleanup completed", Colors.GREEN)
    except Exception as e:
        log(f"⚠️  Docker cleanup failed: {e}", Colors.YELLOW)


def start_services():
    """Start Docker services with fresh state."""
    log("🚀 Starting Docker services...", Colors.BLUE)

    # Ensure we start with clean state
    cleanup_docker()

    # Start services
    run_command(
        [
            "docker-compose",
            "-f",
            str(COMPOSE_FILE),
            "up",
            "-d",
            "--force-recreate",  # Recreate containers even if config hasn't changed
            "--renew-anon-volumes",  # Recreate anonymous volumes
        ]
    )

    log("✅ Docker services started", Colors.GREEN)


async def wait_for_services():
    """Wait for all services to be ready."""
    log("⏳ Waiting for services to be ready...", Colors.BLUE)

    # Wait for mint
    mint_ready = await wait_for_mint("http://localhost:3338")

    # Wait for relay
    relay_ready = await wait_for_relay("ws://localhost:8080")

    if not mint_ready or not relay_ready:
        raise RuntimeError("Services failed to start properly")

    log("✅ All services ready", Colors.GREEN)


def run_tests():
    """Run the integration tests."""
    log("🧪 Running integration tests...", Colors.BLUE)

    env = os.environ.copy()
    env["RUN_INTEGRATION_TESTS"] = "1"

    # Run only integration tests
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/integration/",
        "-v",
        "--tb=short",
        "--color=yes",
    ]

    try:
        result = subprocess.run(cmd, env=env, cwd=PROJECT_ROOT)
        if result.returncode == 0:
            log("✅ Integration tests passed", Colors.GREEN)
            return True
        else:
            log("❌ Integration tests failed", Colors.RED)
            return False
    except Exception as e:
        log(f"❌ Failed to run tests: {e}", Colors.RED)
        return False


def check_dependencies():
    """Check that required dependencies are available."""
    log("🔍 Checking dependencies...", Colors.BLUE)

    # Check Docker
    try:
        run_command(["docker", "--version"], capture_output=True)
        log("✅ Docker found", Colors.GREEN)
    except (subprocess.CalledProcessError, FileNotFoundError):
        log("❌ Docker not found. Please install Docker.", Colors.RED)
        return False

    # Check Docker Compose
    try:
        run_command(["docker-compose", "--version"], capture_output=True)
        log("✅ Docker Compose found", Colors.GREEN)
    except (subprocess.CalledProcessError, FileNotFoundError):
        log("❌ Docker Compose not found. Please install Docker Compose.", Colors.RED)
        return False

    # Check pytest
    try:
        run_command([sys.executable, "-m", "pytest", "--version"], capture_output=True)
        log("✅ pytest found", Colors.GREEN)
    except (subprocess.CalledProcessError, FileNotFoundError):
        log("❌ pytest not found. Please install pytest.", Colors.RED)
        return False

    # Check compose file exists
    if not COMPOSE_FILE.exists():
        log(f"❌ Compose file not found: {COMPOSE_FILE}", Colors.RED)
        return False
    else:
        log("✅ Compose file found", Colors.GREEN)

    return True


async def main():
    """Main function."""
    log("🎯 Starting integration test runner", Colors.BOLD + Colors.BLUE)

    try:
        # Check dependencies
        if not check_dependencies():
            sys.exit(1)

        # Start services
        start_services()

        # Wait for services to be ready
        await wait_for_services()

        # Run tests
        success = run_tests()

        if success:
            log(
                "🎉 Integration tests completed successfully!",
                Colors.BOLD + Colors.GREEN,
            )
            return 0
        else:
            log("💥 Integration tests failed!", Colors.BOLD + Colors.RED)
            return 1

    except KeyboardInterrupt:
        log("⏹️  Interrupted by user", Colors.YELLOW)
        return 1

    except Exception as e:
        log(f"💥 Unexpected error: {e}", Colors.RED)
        return 1

    finally:
        # Always cleanup
        cleanup_docker()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
