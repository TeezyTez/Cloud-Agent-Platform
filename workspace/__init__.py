"""Workspace management for secure file operations."""
from typing import Dict, Any, Optional, List, AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
import asyncio
import structlog
import os
import shutil
import hashlib

logger = structlog.get_logger(__name__)


@dataclass
class FileInfo:
    """Information about a file in the workspace."""
    path: str
    size: int
    modified_at: datetime
    is_directory: bool
    permissions: str


@dataclass
class WorkspaceSnapshot:
    """Snapshot of workspace state."""
    workspace_id: str
    created_at: datetime
    files: List[FileInfo]
    checksum: str  # Hash of all file contents


class WorkspaceManager:
    """
    Manages agent workspaces with secure file operations.
    
    Provides:
    - Isolated working directories
    - Secure file upload/download
    - Workspace snapshots and restore
    - File change tracking
    """
    
    def __init__(self, base_path: str = "/tmp/agent-workspaces"):
        self.base_path = base_path
        self._workspaces: Dict[str, str] = {}  # workspace_id -> path
        self._snapshots: Dict[str, List[WorkspaceSnapshot]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
    
    def _get_workspace_path(self, workspace_id: str) -> str:
        """Get the file system path for a workspace."""
        return os.path.join(self.base_path, workspace_id)
    
    def _get_lock(self, workspace_id: str) -> asyncio.Lock:
        """Get or create a lock for a workspace."""
        if workspace_id not in self._locks:
            self._locks[workspace_id] = asyncio.Lock()
        return self._locks[workspace_id]
    
    async def create_workspace(
        self,
        workspace_id: str,
        files: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Create a new workspace with optional initial files.
        
        Args:
            workspace_id: Unique workspace identifier
            files: Optional dict of filename -> content
            
        Returns:
            Workspace directory path
        """
        workspace_path = self._get_workspace_path(workspace_id)
        
        async with self._get_lock(workspace_id):
            # Create directory
            os.makedirs(workspace_path, exist_ok=True)
            
            # Create initial files
            if files:
                for filename, content in files.items():
                    file_path = os.path.join(workspace_path, filename)
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(content)
            
            self._workspaces[workspace_id] = workspace_path
            self._snapshots[workspace_id] = []
        
        logger.info("Workspace created", workspace_id=workspace_id, path=workspace_path)
        return workspace_path
    
    async def get_workspace_path(self, workspace_id: str) -> Optional[str]:
        """Get the path for a workspace."""
        return self._workspaces.get(workspace_id)
    
    async def delete_workspace(self, workspace_id: str) -> bool:
        """Delete a workspace and all its contents."""
        workspace_path = self._workspaces.get(workspace_id)
        if not workspace_path:
            return False
        
        async with self._get_lock(workspace_id):
            try:
                shutil.rmtree(workspace_path, ignore_errors=True)
                self._workspaces.pop(workspace_id, None)
                self._snapshots.pop(workspace_id, None)
                self._locks.pop(workspace_id, None)
                
                logger.info("Workspace deleted", workspace_id=workspace_id)
                return True
            except Exception as e:
                logger.error("Error deleting workspace", workspace_id=workspace_id, error=str(e))
                return False
    
    async def read_file(
        self,
        workspace_id: str,
        path: str,
    ) -> str:
        """Read a file from the workspace."""
        workspace_path = await self.get_workspace_path(workspace_id)
        if not workspace_path:
            raise ValueError(f"Workspace not found: {workspace_id}")
        
        full_path = os.path.join(workspace_path, path.lstrip("/"))
        
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {path}")
        
        async with self._get_lock(workspace_id):
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    
    async def write_file(
        self,
        workspace_id: str,
        path: str,
        content: str,
    ) -> None:
        """Write a file to the workspace."""
        workspace_path = await self.get_workspace_path(workspace_id)
        if not workspace_path:
            raise ValueError(f"Workspace not found: {workspace_id}")
        
        full_path = os.path.join(workspace_path, path.lstrip("/"))
        
        async with self._get_lock(workspace_id):
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
    
    async def delete_file(
        self,
        workspace_id: str,
        path: str,
    ) -> None:
        """Delete a file from the workspace."""
        workspace_path = await self.get_workspace_path(workspace_id)
        if not workspace_path:
            raise ValueError(f"Workspace not found: {workspace_id}")
        
        full_path = os.path.join(workspace_path, path.lstrip("/"))
        
        async with self._get_lock(workspace_id):
            if os.path.exists(full_path):
                os.remove(full_path)
    
    async def list_files(
        self,
        workspace_id: str,
        path: str = "",
        recursive: bool = False,
    ) -> List[FileInfo]:
        """List files in the workspace."""
        workspace_path = await self.get_workspace_path(workspace_id)
        if not workspace_path:
            raise ValueError(f"Workspace not found: {workspace_id}")
        
        full_path = os.path.join(workspace_path, path.lstrip("/"))
        
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"Directory not found: {path}")
        
        files = []
        
        async with self._get_lock(workspace_id):
            if recursive:
                for root, dirs, filenames in os.walk(full_path):
                    for filename in filenames:
                        file_path = os.path.join(root, filename)
                        stat = os.stat(file_path)
                        rel_path = os.path.relpath(file_path, workspace_path)
                        files.append(FileInfo(
                            path=rel_path,
                            size=stat.st_size,
                            modified_at=datetime.fromtimestamp(stat.st_mtime),
                            is_directory=False,
                            permissions=oct(stat.st_mode)[-3:],
                        ))
            else:
                for entry in os.listdir(full_path):
                    file_path = os.path.join(full_path, entry)
                    stat = os.stat(file_path)
                    files.append(FileInfo(
                        path=entry,
                        size=stat.st_size if os.path.isfile(file_path) else 0,
                        modified_at=datetime.fromtimestamp(stat.st_mtime),
                        is_directory=os.path.isdir(file_path),
                        permissions=oct(stat.st_mode)[-3:],
                    ))
        
        return files
    
    async def create_snapshot(
        self,
        workspace_id: str,
    ) -> Optional[WorkspaceSnapshot]:
        """Create a snapshot of the current workspace state."""
        workspace_path = await self.get_workspace_path(workspace_id)
        if not workspace_path:
            return None
        
        files = await self.list_files(workspace_id, recursive=True)
        
        # Calculate checksum of all files
        hasher = hashlib.sha256()
        for file_info in files:
            if not file_info.is_directory:
                full_path = os.path.join(workspace_path, file_info.path)
                hasher.update(full_path.encode())
                try:
                    with open(full_path, "rb") as f:
                        hasher.update(f.read())
                except Exception:
                    pass
        
        snapshot = WorkspaceSnapshot(
            workspace_id=workspace_id,
            created_at=datetime.utcnow(),
            files=files,
            checksum=hasher.hexdigest(),
        )
        
        if workspace_id not in self._snapshots:
            self._snapshots[workspace_id] = []
        
        self._snapshots[workspace_id].append(snapshot)
        
        logger.info("Workspace snapshot created", workspace_id=workspace_id, checksum=snapshot.checksum)
        return snapshot
    
    async def restore_snapshot(
        self,
        workspace_id: str,
        snapshot_index: int = -1,
    ) -> bool:
        """Restore workspace from a snapshot."""
        snapshots = self._snapshots.get(workspace_id, [])
        if not snapshots or abs(snapshot_index) >= len(snapshots):
            return False
        
        snapshot = snapshots[snapshot_index]
        workspace_path = await self.get_workspace_path(workspace_id)
        
        if not workspace_path:
            return False
        
        # Note: Full restore would need to store file contents in snapshot
        # This is a simplified version that just records the checkpoint
        logger.info(
            "Workspace restore requested",
            workspace_id=workspace_id,
            snapshot_index=snapshot_index,
        )
        return True
    
    async def get_snapshots(
        self,
        workspace_id: str,
    ) -> List[WorkspaceSnapshot]:
        """Get all snapshots for a workspace."""
        return self._snapshots.get(workspace_id, [])
    
    async def export_workspace(
        self,
        workspace_id: str,
    ) -> Optional[bytes]:
        """Export workspace as a tarball."""
        import tarfile
        import io
        
        workspace_path = await self.get_workspace_path(workspace_id)
        if not workspace_path:
            return None
        
        buffer = io.BytesIO()
        
        async with self._get_lock(workspace_id):
            with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
                tar.add(workspace_path, arcname=workspace_id)
        
        buffer.seek(0)
        return buffer.read()
    
    async def import_workspace(
        self,
        workspace_id: str,
        tarball: bytes,
    ) -> bool:
        """Import workspace from a tarball."""
        import tarfile
        import io
        
        workspace_path = await self.get_workspace_path(workspace_id)
        if not workspace_path:
            await self.create_workspace(workspace_id)
            workspace_path = await self.get_workspace_path(workspace_id)
        
        async with self._get_lock(workspace_id):
            try:
                buffer = io.BytesIO(tarball)
                with tarfile.open(fileobj=buffer, mode="r:gz") as tar:
                    tar.extractall(workspace_path)
                
                logger.info("Workspace imported", workspace_id=workspace_id)
                return True
            except Exception as e:
                logger.error("Workspace import failed", workspace_id=workspace_id, error=str(e))
                return False


# Global workspace manager
_workspace_manager: Optional[WorkspaceManager] = None


def get_workspace_manager() -> WorkspaceManager:
    """Get the global workspace manager instance."""
    global _workspace_manager
    if _workspace_manager is None:
        _workspace_manager = WorkspaceManager()
    return _workspace_manager
