"""Sandbox management for isolated agent execution."""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime
import asyncio
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class CommandResult:
    """Result of executing a command."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    started_at: datetime
    completed_at: datetime
    
    @property
    def duration_ms(self) -> float:
        """Get command duration in milliseconds."""
        return (self.completed_at - self.started_at).total_seconds() * 1000


@dataclass
class SandboxInfo:
    """Information about a running sandbox."""
    sandbox_id: str
    sandbox_type: str
    status: str
    host_port: Optional[int] = None
    created_at: datetime = None
    expires_at: Optional[datetime] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.utcnow()


class SandboxInterface(ABC):
    """Abstract interface for sandbox implementations."""
    
    @abstractmethod
    async def start(self) -> SandboxInfo:
        """Start the sandbox and return info."""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop and cleanup the sandbox."""
        pass
    
    @abstractmethod
    async def execute_command(
        self,
        command: str,
        timeout: int = 60,
        working_directory: Optional[str] = None,
    ) -> CommandResult:
        """Execute a command in the sandbox."""
        pass
    
    @abstractmethod
    async def read_file(self, path: str) -> str:
        """Read a file from the sandbox."""
        pass
    
    @abstractmethod
    async def write_file(self, path: str, content: str) -> None:
        """Write a file to the sandbox."""
        pass
    
    @abstractmethod
    async def delete_file(self, path: str) -> None:
        """Delete a file from the sandbox."""
        pass
    
    @abstractmethod
    async def list_directory(self, path: str) -> List[str]:
        """List contents of a directory."""
        pass


class DockerSandbox(SandboxInterface):
    """
    Docker-based sandbox implementation.
    
    Provides isolated container execution with resource limits.
    """
    
    def __init__(
        self,
        image: str = "ghcr.io/openhands/agent-server:latest-python",
        cpu_limit: str = "2",
        memory_limit: str = "4g",
        disk_limit: str = "10g",
        timeout_seconds: int = 3600,
        environment: Optional[Dict[str, str]] = None,
        host_port: Optional[int] = None,
    ):
        self.image = image
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self.disk_limit = disk_limit
        self.timeout_seconds = timeout_seconds
        self.environment = environment or {}
        self.host_port = host_port
        
        self._client = None
        self._container = None
        self._sandbox_id: Optional[str] = None
        self._working_directory = "/workspace"
    
    async def _get_client(self):
        """Lazy initialization of Docker client."""
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client
    
    async def start(self) -> SandboxInfo:
        """Start a Docker container sandbox."""
        import uuid
        import docker
        from datetime import timedelta
        
        client = await self._get_client()
        
        # Pull image if needed
        try:
            client.images.pull(self.image)
        except docker.errors.APIError as e:
            logger.warning("Failed to pull image, trying existing", image=self.image, error=str(e))
        
        # Create container
        container_name = f"agent-{uuid.uuid4().hex[:8]}"
        
        env = [
            f"{k}={v}" for k, v in self.environment.items()
        ]
        
        try:
            container = client.containers.run(
                self.image,
                command="sleep infinity",
                name=container_name,
                detach=True,
                mem_limit=self.memory_limit,
                cpu_period=100000,
                cpu_quota=int(self.cpu_limit) * 100000,
                working_dir=self._working_directory,
                environment=env,
                volumes={},
                network_mode="bridge",
            )
        except docker.errors.APIError as e:
            logger.error("Failed to create container", error=str(e))
            raise
        
        self._container = container
        self._sandbox_id = container.id[:12]
        
        # Wait for container to be running
        container.reload()
        
        logger.info("Docker sandbox started", sandbox_id=self._sandbox_id, container=container_name)
        
        return SandboxInfo(
            sandbox_id=self._sandbox_id,
            sandbox_type="docker",
            host_port=self.host_port,
            expires_at=datetime.utcnow() + timedelta(seconds=self.timeout_seconds),
        )
    
    async def stop(self) -> None:
        """Stop and remove the Docker container."""
        if self._container:
            try:
                self._container.stop(timeout=10)
                self._container.remove(force=True)
                logger.info("Docker sandbox stopped", sandbox_id=self._sandbox_id)
            except Exception as e:
                logger.error("Error stopping container", sandbox_id=self._sandbox_id, error=str(e))
            finally:
                self._container = None
                self._sandbox_id = None
    
    async def execute_command(
        self,
        command: str,
        timeout: int = 60,
        working_directory: Optional[str] = None,
    ) -> CommandResult:
        """Execute a command in the container."""
        from datetime import datetime as dt
        
        if not self._container:
            raise RuntimeError("Sandbox not started")
        
        workdir = working_directory or self._working_directory
        
        started_at = dt.utcnow()
        
        try:
            result = self._container.exec_run(
                cmd=f"bash -c 'cd {workdir} && {command}'",
                workdir=workdir,
            )
            
            completed_at = dt.utcnow()
            
            return CommandResult(
                command=command,
                exit_code=result.exit_code,
                stdout=result.output.decode("utf-8", errors="replace"),
                stderr="",
                started_at=started_at,
                completed_at=completed_at,
            )
        except Exception as e:
            completed_at = dt.utcnow()
            return CommandResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                started_at=started_at,
                completed_at=completed_at,
            )
    
    async def read_file(self, path: str) -> str:
        """Read a file from the container."""
        if not self._container:
            raise RuntimeError("Sandbox not started")
        
        try:
            bits, stat = self._container.get_archive(path)
            content = b"".join(bits)
            return content.decode("utf-8", errors="replace")
        except docker.errors.APIError:
            raise FileNotFoundError(f"File not found: {path}")
    
    async def write_file(self, path: str, content: str) -> None:
        """Write a file to the container."""
        if not self._container:
            raise RuntimeError("Sandbox not started")
        
        import tarfile
        import io
        
        # Create tar archive with file content
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            content_bytes = content.encode("utf-8")
            tarinfo = tarfile.TarInfo(name=path.lstrip("/"))
            tarinfo.size = len(content_bytes)
            tar.addfile(tarinfo, io.BytesIO(content_bytes))
        
        tar_buffer.seek(0)
        
        # Extract to container
        self._container.put_archive("/", tar_buffer)
    
    async def delete_file(self, path: str) -> None:
        """Delete a file from the container."""
        result = await self.execute_command(f"rm -rf {path}")
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to delete file: {result.stderr}")
    
    async def list_directory(self, path: str) -> List[str]:
        """List contents of a directory."""
        result = await self.execute_command(f"ls -la {path}")
        if result.exit_code != 0:
            raise FileNotFoundError(f"Directory not found: {path}")
        return result.stdout.split("\n")


class LocalSandbox(SandboxInterface):
    """
    Local sandbox implementation for development/testing.
    
    Executes commands directly on the host with basic isolation.
    WARNING: Not suitable for production - provides minimal isolation.
    """
    
    def __init__(
        self,
        working_directory: str = "/tmp/agent-workspace",
        environment: Optional[Dict[str, str]] = None,
    ):
        self.working_directory = working_directory
        self.environment = environment or {}
        self._sandbox_id = "local-sandbox"
        self._started = False
        self._temp_dir = None
    
    async def start(self) -> SandboxInfo:
        """Create a temporary working directory."""
        import tempfile
        import os
        
        self._temp_dir = tempfile.mkdtemp(prefix="agent-workspace-")
        self.working_directory = self._temp_dir
        self._started = True
        
        logger.info("Local sandbox started", directory=self._temp_dir)
        
        return SandboxInfo(
            sandbox_id=self._sandbox_id,
            sandbox_type="local",
            status="running",
            created_at=datetime.utcnow(),
        )
    
    async def stop(self) -> None:
        """Remove the temporary directory."""
        import shutil
        import os
        
        if self._temp_dir and os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            logger.info("Local sandbox stopped")
        
        self._started = False
    
    async def execute_command(
        self,
        command: str,
        timeout: int = 60,
        working_directory: Optional[str] = None,
    ) -> CommandResult:
        """Execute a command using asyncio.create_subprocess_shell."""
        import asyncio
        import os
        from datetime import datetime as dt
        
        workdir = working_directory or self.working_directory
        started_at = dt.utcnow()
        
        env = os.environ.copy()
        env.update(self.environment)
        
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                env=env,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                completed_at = dt.utcnow()
                return CommandResult(
                    command=command,
                    exit_code=-1,
                    stdout="",
                    stderr=f"Command timed out after {timeout} seconds",
                    started_at=started_at,
                    completed_at=completed_at,
                )
            
            completed_at = dt.utcnow()
            
            return CommandResult(
                command=command,
                exit_code=proc.returncode,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                started_at=started_at,
                completed_at=completed_at,
            )
        except Exception as e:
            completed_at = dt.utcnow()
            return CommandResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                started_at=started_at,
                completed_at=completed_at,
            )
    
    async def read_file(self, path: str) -> str:
        """Read a file."""
        import os
        
        full_path = os.path.join(self.working_directory, path.lstrip("/"))
        
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {path}")
        
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    
    async def write_file(self, path: str, content: str) -> None:
        """Write a file."""
        import os
        
        full_path = os.path.join(self.working_directory, path.lstrip("/"))
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
    
    async def delete_file(self, path: str) -> None:
        """Delete a file."""
        import os
        
        full_path = os.path.join(self.working_directory, path.lstrip("/"))
        os.remove(full_path)
    
    async def list_directory(self, path: str) -> List[str]:
        """List directory contents."""
        import os
        
        full_path = os.path.join(self.working_directory, path.lstrip("/"))
        
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Directory not found: {path}")
        
        entries = os.listdir(full_path)
        return [f"{entry}" for entry in entries]


class SandboxManager:
    """
    Manager for creating and lifecycle of sandbox environments.
    
    Supports multiple sandbox backends (Docker, Kubernetes, local).
    """
    
    def __init__(self):
        self._sandboxes: Dict[str, SandboxInterface] = {}
        self._sandbox_infos: Dict[str, SandboxInfo] = {}
        self._max_concurrent = 10
    
    async def create_sandbox(
        self,
        sandbox_type: str = "docker",
        **kwargs,
    ) -> tuple[str, SandboxInterface, SandboxInfo]:
        """
        Create a new sandbox instance.
        
        Returns:
            Tuple of (sandbox_id, sandbox_interface, sandbox_info)
        """
        import uuid
        
        sandbox_id = str(uuid.uuid4())[:12]
        
        # Create sandbox based on type
        if sandbox_type == "docker":
            sandbox = DockerSandbox(**kwargs)
        elif sandbox_type == "local":
            sandbox = LocalSandbox(**kwargs)
        else:
            raise ValueError(f"Unknown sandbox type: {sandbox_type}")
        
        # Start sandbox
        info = await sandbox.start()
        info.sandbox_id = sandbox_id
        
        # Store references
        self._sandboxes[sandbox_id] = sandbox
        self._sandbox_infos[sandbox_id] = info
        
        logger.info("Sandbox created", sandbox_id=sandbox_id, sandbox_type=sandbox_type)
        
        return sandbox_id, sandbox, info
    
    async def get_sandbox(self, sandbox_id: str) -> Optional[SandboxInterface]:
        """Get a sandbox by ID."""
        return self._sandboxes.get(sandbox_id)
    
    async def get_sandbox_info(self, sandbox_id: str) -> Optional[SandboxInfo]:
        """Get sandbox info by ID."""
        return self._sandbox_infos.get(sandbox_id)
    
    async def destroy_sandbox(self, sandbox_id: str) -> bool:
        """Destroy a sandbox instance."""
        sandbox = self._sandboxes.get(sandbox_id)
        if not sandbox:
            return False
        
        try:
            await sandbox.stop()
            del self._sandboxes[sandbox_id]
            if sandbox_id in self._sandbox_infos:
                del self._sandbox_infos[sandbox_id]
            logger.info("Sandbox destroyed", sandbox_id=sandbox_id)
            return True
        except Exception as e:
            logger.error("Error destroying sandbox", sandbox_id=sandbox_id, error=str(e))
            return False
    
    async def list_sandboxes(self) -> List[SandboxInfo]:
        """List all active sandboxes."""
        return list(self._sandbox_infos.values())
    
    async def destroy_all(self):
        """Destroy all sandboxes."""
        sandbox_ids = list(self._sandboxes.keys())
        for sandbox_id in sandbox_ids:
            await self.destroy_sandbox(sandbox_id)
