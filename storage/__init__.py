"""Storage layer for task persistence and state management."""
from typing import Dict, Any, Optional, List
from datetime import datetime
import asyncio
import json
import structlog

logger = structlog.get_logger(__name__)


class TaskStorage:
    """
    Persistent storage for tasks and results.
    
    Uses a simple file-based storage for development.
    In production, use Redis or a database.
    """
    
    def __init__(self, storage_path: str = "/tmp/agent-tasks"):
        self.storage_path = storage_path
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
    
    async def initialize(self) -> None:
        """Initialize storage."""
        import os
        os.makedirs(self.storage_path, exist_ok=True)
        await self._load_cache()
    
    async def _load_cache(self) -> None:
        """Load tasks from disk into cache."""
        import os
        
        async with self._lock:
            for filename in os.listdir(self.storage_path):
                if filename.endswith(".json"):
                    filepath = os.path.join(self.storage_path, filename)
                    try:
                        with open(filepath, "r") as f:
                            task_data = json.load(f)
                            task_id = task_data.get("id")
                            if task_id:
                                self._cache[task_id] = task_data
                    except Exception as e:
                        logger.error("Failed to load task", filepath=filepath, error=str(e))
    
    def _get_filepath(self, task_id: str) -> str:
        """Get file path for a task."""
        import os
        return os.path.join(self.storage_path, f"{task_id}.json")
    
    async def save_task(self, task: Dict[str, Any]) -> None:
        """Save a task to storage."""
        async with self._lock:
            self._cache[task["id"]] = task
            filepath = self._get_filepath(task["id"])
            
            with open(filepath, "w") as f:
                json.dump(task, f, indent=2, default=str)
    
    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a task by ID."""
        return self._cache.get(task_id)
    
    async def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        import os
        
        async with self._lock:
            if task_id in self._cache:
                del self._cache[task_id]
            
            filepath = self._get_filepath(task_id)
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
            return False
    
    async def list_tasks(
        self,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """List all tasks."""
        tasks = list(self._cache.values())
        
        if status:
            tasks = [t for t in tasks if t.get("status") == status]
        
        # Sort by created_at descending
        tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        
        return tasks[:limit]
    
    async def update_task_field(
        self,
        task_id: str,
        field: str,
        value: Any,
    ) -> bool:
        """Update a specific field in a task."""
        task = self._cache.get(task_id)
        if not task:
            return False
        
        task[field] = value
        await self.save_task(task)
        return True


class EventLog:
    """
    Persistent log of all events.
    
    Useful for debugging and auditing.
    """
    
    def __init__(self, log_path: str = "/tmp/agent-events"):
        self.log_path = log_path
        self._buffer: List[Dict[str, Any]] = []
        self._buffer_size = 100
        self._flush_task: Optional[asyncio.Task] = None
    
    async def initialize(self) -> None:
        """Initialize the event log."""
        import os
        os.makedirs(self.log_path, exist_ok=True)
        
        # Start background flush task
        self._flush_task = asyncio.create_task(self._flush_loop())
    
    async def close(self) -> None:
        """Close the event log and flush remaining events."""
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        await self._flush()
    
    async def log_event(self, event: Dict[str, Any]) -> None:
        """Log an event."""
        import os
        
        event_with_time = {
            "timestamp": datetime.utcnow().isoformat(),
            **event,
        }
        
        self._buffer.append(event_with_time)
        
        if len(self._buffer) >= self._buffer_size:
            await self._flush()
    
    async def _flush(self) -> None:
        """Flush buffer to disk."""
        if not self._buffer:
            return
        
        import os
        
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        filename = f"events-{timestamp}.jsonl"
        filepath = os.path.join(self.log_path, filename)
        
        with open(filepath, "a") as f:
            for event in self._buffer:
                f.write(json.dumps(event) + "\n")
        
        self._buffer.clear()
        logger.debug("Event log flushed", count=len(self._buffer), filepath=filepath)
    
    async def _flush_loop(self) -> None:
        """Background flush loop."""
        while True:
            try:
                await asyncio.sleep(5)  # Flush every 5 seconds
                await self._flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in flush loop", error=str(e))
    
    async def get_events(
        self,
        task_id: Optional[str] = None,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Query events from the log."""
        import os
        from datetime import datetime
        
        events = []
        
        for filename in os.listdir(self.log_path):
            if not filename.endswith(".jsonl"):
                continue
            
            filepath = os.path.join(self.log_path, filename)
            
            try:
                with open(filepath, "r") as f:
                    for line in f:
                        try:
                            event = json.loads(line)
                            
                            # Apply filters
                            if task_id and event.get("task_id") != task_id:
                                continue
                            if event_type and event.get("event_type") != event_type:
                                continue
                            if since:
                                event_time = datetime.fromisoformat(event["timestamp"])
                                if event_time < since:
                                    continue
                            
                            events.append(event)
                        except json.JSONDecodeError:
                            continue
            except Exception as e:
                logger.error("Error reading event log", filepath=filepath, error=str(e))
        
        # Sort by timestamp descending
        events.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        
        return events[:limit]


# Global storage instances
_task_storage: Optional[TaskStorage] = None
_event_log: Optional[EventLog] = None


def get_task_storage() -> TaskStorage:
    """Get the global task storage."""
    global _task_storage
    if _task_storage is None:
        _task_storage = TaskStorage()
    return _task_storage


def get_event_log() -> EventLog:
    """Get the global event log."""
    global _event_log
    if _event_log is None:
        _event_log = EventLog()
    return _event_log
