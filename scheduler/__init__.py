"""Task scheduler and orchestration layer."""
from typing import Dict, Any, Optional, List, Callable, AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import asyncio
import uuid
import structlog
import json

from src.models import Task, TaskStatus, TaskPriority, TaskEvent, EventType

logger = structlog.get_logger(__name__)


@dataclass
class QueueEntry:
    """An entry in the task queue."""
    task: Task
    priority: int
    created_at: datetime
    scheduled_at: Optional[datetime] = None
    
    def __lt__(self, other):
        """Priority queue ordering (higher priority first, then earlier created_at)."""
        if self.priority != other.priority:
            return self.priority > other.priority
        return self.created_at < other.created_at


class TaskQueue:
    """
    Priority-based task queue with scheduling support.
    
    Tasks are processed in priority order (higher priority first).
    """
    
    def __init__(self, max_size: int = 10000):
        self._queue: List[QueueEntry] = []
        self._tasks: Dict[str, Task] = {}  # task_id -> Task
        self._max_size = max_size
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Condition(self._lock)
    
    async def enqueue(self, task: Task) -> bool:
        """Add a task to the queue."""
        async with self._lock:
            if len(self._queue) >= self._max_size:
                logger.warning("Queue full, rejecting task", task_id=task.id)
                return False
            
            entry = QueueEntry(
                task=task,
                priority=task.priority.value,
                created_at=task.created_at,
            )
            
            # Insert in priority order
            inserted = False
            for i, existing in enumerate(self._queue):
                if entry < existing:
                    self._queue.insert(i, entry)
                    inserted = True
                    break
            
            if not inserted:
                self._queue.append(entry)
            
            self._tasks[task.id] = task
            task.status = TaskStatus.QUEUED
            
            self._not_empty.notify()
            
            logger.info("Task enqueued", task_id=task.id, priority=task.priority)
            return True
    
    async def dequeue(self, timeout: Optional[float] = None) -> Optional[Task]:
        """
        Get the next task from the queue.
        
        Args:
            timeout: Maximum time to wait, None for indefinite
            
        Returns:
            Next task or None if timeout reached
        """
        async with self._not_empty:
            while not self._queue:
                try:
                    await asyncio.wait_for(self._not_empty.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    return None
            
            entry = self._queue.pop(0)
            task = entry.task
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.utcnow()
            
            logger.info("Task dequeued", task_id=task.id)
            return task
    
    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self._tasks.get(task_id)
    
    async def update_task(self, task: Task) -> None:
        """Update a task."""
        async with self._lock:
            self._tasks[task.id] = task
    
    async def remove_task(self, task_id: str) -> Optional[Task]:
        """Remove a task from the queue."""
        async with self._lock:
            task = self._tasks.pop(task_id, None)
            if task:
                self._queue = [e for e in self._queue if e.task.id != task_id]
            return task
    
    async def list_tasks(self, status: Optional[TaskStatus] = None) -> List[Task]:
        """List all tasks, optionally filtered by status."""
        async with self._lock:
            tasks = list(self._tasks.values())
            if status:
                tasks = [t for t in tasks if t.status == status]
            return tasks
    
    def size(self) -> int:
        """Get the current queue size."""
        return len(self._queue)


class EventBus:
    """
    In-memory event bus for task events.
    
    Provides pub/sub for real-time event streaming.
    """
    
    def __init__(self):
        self._subscribers: Dict[str, asyncio.Queue] = {}
        self._all_events: asyncio.Queue = asyncio.Queue()
        self._lock = asyncio.Lock()
    
    async def subscribe(self, subscriber_id: str) -> AsyncIterator[TaskEvent]:
        """
        Subscribe to events for a specific task or all tasks.
        
        Args:
            subscriber_id: Unique subscriber identifier
            
        Yields:
            TaskEvent objects as they occur
        """
        queue: asyncio.Queue = asyncio.Queue()
        
        async with self._lock:
            self._subscribers[subscriber_id] = queue
        
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60.0)
                    yield event
                except asyncio.TimeoutError:
                    # Send heartbeat
                    yield TaskEvent(
                        task_id="",
                        event_type=EventType.HEARTBEAT.value,
                        data={"timestamp": datetime.utcnow().isoformat()},
                    )
        except GeneratorExit:
            pass
        finally:
            async with self._lock:
                self._subscribers.pop(subscriber_id, None)
    
    async def publish(self, event: TaskEvent) -> None:
        """Publish an event to all subscribers."""
        # Publish to global queue
        try:
            self._all_events.put_nowait(event)
        except asyncio.QueueFull:
            pass
        
        # Publish to task-specific subscribers
        async with self._lock:
            for subscriber_id, queue in self._subscribers.items():
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass
    
    async def subscribe_task(self, task_id: str) -> AsyncIterator[TaskEvent]:
        """Subscribe to events for a specific task."""
        async for event in self.subscribe(f"task-{task_id}"):
            if not event.task_id or event.task_id == task_id:
                yield event


class TaskScheduler:
    """
    Main task scheduler coordinating queue processing and worker execution.
    
    Manages:
    - Task queuing and scheduling
    - Worker pool for parallel execution
    - Event broadcasting
    - Task lifecycle management
    """
    
    def __init__(
        self,
        task_queue: Optional[TaskQueue] = None,
        event_bus: Optional[EventBus] = None,
        max_workers: int = 4,
    ):
        self.queue = task_queue or TaskQueue()
        self.event_bus = event_bus or EventBus()
        self.max_workers = max_workers
        self._workers: List[asyncio.Task] = []
        self._running = False
        self._worker_status: Dict[int, str] = {}  # task_id -> status
        self._task_handlers: Dict[str, Callable] = {}
        self._lock = asyncio.Lock()
    
    def register_handler(self, task_id: str, handler: Callable) -> None:
        """Register a handler for a specific task."""
        self._task_handlers[task_id] = handler
    
    async def start(self) -> None:
        """Start the scheduler workers."""
        if self._running:
            return
        
        self._running = True
        
        # Start worker tasks
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)
        
        logger.info("Task scheduler started", max_workers=self.max_workers)
    
    async def stop(self) -> None:
        """Stop the scheduler and all workers."""
        self._running = False
        
        # Cancel all workers
        for worker in self._workers:
            worker.cancel()
        
        # Wait for workers to finish
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        
        logger.info("Task scheduler stopped")
    
    async def submit_task(
        self,
        task: Task,
        handler: Optional[Callable] = None,
    ) -> str:
        """
        Submit a task for execution.
        
        Args:
            task: Task to execute
            handler: Optional custom handler function
            
        Returns:
            Task ID
        """
        # Register handler if provided
        if handler:
            self._task_handlers[task.id] = handler
        
        # Publish task created event
        await self.event_bus.publish(TaskEvent(
            task_id=task.id,
            event_type=EventType.TASK_STARTED.value,
            data={"task": task.model_dump()},
        ))
        
        # Add to queue
        await self.queue.enqueue(task)
        
        return task.id
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running or queued task."""
        task = await self.queue.get_task(task_id)
        if not task:
            return False
        
        if task.status == TaskStatus.COMPLETED or task.status == TaskStatus.FAILED:
            return False
        
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.utcnow()
        
        await self.queue.update_task(task)
        
        await self.event_bus.publish(TaskEvent(
            task_id=task_id,
            event_type=EventType.TASK_CANCELLED.value,
            data={"task_id": task_id},
        ))
        
        logger.info("Task cancelled", task_id=task_id)
        return True
    
    async def get_task_status(self, task_id: str) -> Optional[Task]:
        """Get current task status."""
        return await self.queue.get_task(task_id)
    
    async def _worker(self, worker_id: int) -> None:
        """Worker coroutine that processes tasks from the queue."""
        logger.info("Worker started", worker_id=worker_id)
        
        while self._running:
            try:
                # Get next task
                task = await self.queue.dequeue(timeout=5.0)
                
                if not task:
                    continue
                
                logger.info(
                    "Worker processing task",
                    worker_id=worker_id,
                    task_id=task.id,
                )
                
                self._worker_status[worker_id] = task.id
                
                # Get handler or use default
                handler = self._task_handlers.get(task.id)
                
                if handler:
                    try:
                        await handler(task, self.event_bus)
                    except Exception as e:
                        logger.error(
                            "Task handler error",
                            task_id=task.id,
                            error=str(e),
                        )
                        task.status = TaskStatus.FAILED
                        task.error = str(e)
                        task.completed_at = datetime.utcnow()
                else:
                    # No handler registered
                    logger.warning("No handler for task", task_id=task.id)
                    task.status = TaskStatus.FAILED
                    task.error = "No handler registered"
                    task.completed_at = datetime.utcnow()
                
                # Update task in queue
                await self.queue.update_task(task)
                
                # Publish completion event
                event_type = (
                    EventType.TASK_COMPLETED.value
                    if task.status == TaskStatus.COMPLETED
                    else EventType.TASK_FAILED.value
                )
                
                await self.event_bus.publish(TaskEvent(
                    task_id=task.id,
                    event_type=event_type,
                    data={
                        "task_id": task.id,
                        "status": task.status,
                        "result": task.result,
                        "error": task.error,
                    },
                ))
                
                # Clean up handler
                self._task_handlers.pop(task.id, None)
                self._worker_status.pop(worker_id, None)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Worker error", worker_id=worker_id, error=str(e))
        
        logger.info("Worker stopped", worker_id=worker_id)


# Global scheduler instance
_scheduler: Optional[TaskScheduler] = None


def get_scheduler() -> TaskScheduler:
    """Get the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler(max_workers=4)
    return _scheduler
