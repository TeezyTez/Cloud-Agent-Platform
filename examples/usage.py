#!/usr/bin/env python3
"""
Example: Using the Cloud Agent Platform programmatically.

This example demonstrates how to:
1. Configure the agent platform
2. Submit tasks via the API
3. Stream events in real-time
4. Handle task completion
"""
import asyncio
import json
import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models import Task, AgentType, TaskPriority, EventType
from agent import AgentEngine, AgentConfig, SubAgentRunner
from agent.tools import get_default_tools, ToolContext
from llm import LLMFactory, Message
from sandbox import LocalSandbox
from scheduler import TaskScheduler, EventBus
from observability import get_metrics_collector, get_task_metrics
import structlog

logger = structlog.get_logger(__name__)


async def example_standalone_agent():
    """
    Example 1: Run a standalone agent directly.
    
    This is the simplest way to use the agent - just create an engine
    and run it on a task.
    """
    print("\n" + "=" * 60)
    print("Example 1: Standalone Agent")
    print("=" * 60)
    
    # Get API key from environment
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("⚠️  OPENAI_API_KEY not set, skipping LLM call")
        return
    
    # Create LLM provider
    llm = LLMFactory.create(
        provider="openai",
        api_key=api_key,
        default_model="gpt-4o",
    )
    
    # Create agent config
    config = AgentConfig(
        agent_type=AgentType.DEFAULT,
        max_iterations=10,
        tools=["terminal", "file_editor", "task_tracker"],
    )
    
    # Create sandbox
    sandbox = LocalSandbox()
    await sandbox.start()
    
    # Create tool context
    context = ToolContext(
        workspace_id="example-workspace",
        task_id="example-task",
        working_directory=sandbox.working_directory,
        sandbox=sandbox,
    )
    
    # Create agent
    tools = get_default_tools()
    engine = AgentEngine(llm, tools, config)
    
    # Add event callback
    async def on_event(event):
        event_type = event.get("type", "unknown")
        data = event.get("data", {})
        
        if event_type == EventType.THOUGHT.value:
            print(f"🤔 Thinking: {data.get('thought', '')[:100]}...")
        elif event_type == EventType.TOOL_CALL.value:
            print(f"🔧 Calling tool: {data.get('tool_name')}()")
        elif event_type == EventType.TOOL_RESULT.value:
            success = "✅" if data.get("success") else "❌"
            print(f"{success} Tool result received")
        elif event_type == EventType.AGENT_MESSAGE.value:
            print(f"💬 {data.get('content', '')}")
    
    engine.add_callback(on_event)
    
    # Create task
    task = Task(
        description="Create a Python file called hello.py that prints 'Hello, World!' and then run it."
    )
    
    # Run agent
    print(f"\n📋 Task: {task.description}")
    print("-" * 60)
    
    try:
        state = await engine.run(task, context)
        
        print("\n" + "-" * 60)
        if state.is_complete:
            print(f"✅ Task completed in {state.iteration} iterations")
            print(f"📄 Result: {state.final_result}")
        else:
            print(f"❌ Task failed: {state.error_message}")
            
    finally:
        await sandbox.stop()


async def example_task_scheduler():
    """
    Example 2: Use the task scheduler for async task processing.
    
    This demonstrates how to use the scheduler for background task
    processing with queue management.
    """
    print("\n" + "=" * 60)
    print("Example 2: Task Scheduler")
    print("=" * 60)
    
    # Create scheduler
    scheduler = TaskScheduler(max_workers=2)
    event_bus = EventBus()
    
    # Task handler
    async def handle_task(task, event_bus):
        print(f"📥 Processing task: {task.id}")
        
        # Publish started event
        await event_bus.publish({
            "task_id": task.id,
            "event_type": EventType.TASK_STARTED.value,
            "data": {},
        })
        
        # Simulate work
        await asyncio.sleep(2)
        
        # Update task
        task.status = "completed"
        task.result = {"message": "Task completed successfully!"}
        
        # Publish completion
        await event_bus.publish({
            "task_id": task.id,
            "event_type": EventType.TASK_COMPLETED.value,
            "data": {"result": task.result},
        })
    
    # Start scheduler
    await scheduler.start()
    
    try:
        # Create and submit tasks
        tasks = [
            Task(description=f"Background task {i+1}", priority=TaskPriority.NORMAL)
            for i in range(3)
        ]
        
        for task in tasks:
            await scheduler.submit_task(task, handle_task)
        
        print(f"📤 Submitted {len(tasks)} tasks to queue")
        
        # Wait for all tasks to complete
        await asyncio.sleep(5)
        
        # Get task statuses
        for task in tasks:
            status = await scheduler.get_task_status(task.id)
            print(f"  Task {task.id[:8]}: {status.status if status else 'unknown'}")
            
    finally:
        await scheduler.stop()


async def example_sandbox_isolation():
    """
    Example 3: Demonstrate sandbox isolation.
    
    Each sandbox runs in complete isolation from others.
    """
    print("\n" + "=" * 60)
    print("Example 3: Sandbox Isolation")
    print("=" * 60)
    
    # Create multiple sandboxes
    sandboxes = []
    
    for i in range(2):
        sandbox = LocalSandbox()
        await sandbox.start()
        sandboxes.append(sandbox)
        
        # Write a file to this sandbox
        await sandbox.write_file(f"file_{i}.txt", f"Content from sandbox {i}")
        
        print(f"✅ Sandbox {i+1} created at {sandbox.working_directory}")
    
    # Each sandbox has its own isolated file system
    for i, sandbox in enumerate(sandboxes):
        files = await sandbox.list_directory(".")
        print(f"  Sandbox {i+1} files: {files}")
    
    # Run commands in each sandbox
    for i, sandbox in enumerate(sandboxes):
        result = await sandbox.execute_command(f"echo 'Hello from sandbox {i+1}'")
        print(f"  Sandbox {i+1} says: {result.stdout.strip()}")
    
    # Cleanup
    for sandbox in sandboxes:
        await sandbox.stop()
    
    print("🧹 All sandboxes cleaned up")


async def example_metrics():
    """
    Example 4: Using the observability module.
    
    Track metrics for tasks and LLM usage.
    """
    print("\n" + "=" * 60)
    print("Example 4: Observability")
    print("=" * 60)
    
    metrics = get_metrics_collector()
    task_metrics = get_task_metrics()
    
    # Record some fake metrics
    task_metrics.record_task_start("task-1", "coder")
    task_metrics.record_llm_call("gpt-4o", 1000, 0.03, 150.0, True)
    task_metrics.record_tool_call("terminal", 50.0, True)
    
    # Get all metrics
    all_metrics = metrics.get_all_metrics()
    
    print("📊 Current Metrics:")
    for metric in all_metrics.get("metrics", []):
        name = metric["name"]
        value = metric.get("value", {})
        if isinstance(value, dict):
            print(f"  {name}: {json.dumps(value, indent=4)}")
        else:
            print(f"  {name}: {value}")


async def example_llm_providers():
    """
    Example 5: Using different LLM providers.
    
    The platform supports multiple LLM backends.
    """
    print("\n" + "=" * 60)
    print("Example 5: LLM Providers")
    print("=" * 60)
    
    api_key = os.getenv("OPENAI_API_KEY", "")
    
    if not api_key:
        print("⚠️  API keys not configured, showing available providers")
    else:
        print("✅ OpenAI provider configured")
    
    # List supported models for OpenAI
    llm = LLMFactory.create(
        provider="openai",
        api_key=api_key,
    )
    
    print(f"📋 Supported OpenAI models: {llm.supported_models}")
    
    # Test a simple completion
    if api_key:
        messages = [
            Message(role="user", content="Say 'Hello, Cloud Agent Platform!' in exactly those words.")
        ]
        
        print("\n🔄 Testing LLM completion...")
        response = await llm.complete(messages, max_tokens=50)
        
        print(f"✅ Response: {response.content}")
        print(f"📊 Tokens used: {response.usage.get('total_tokens', 0)}")
        print(f"💰 Cost: ${response.cost:.4f}")


async def main():
    """Run all examples."""
    print("\n" + "🚀" * 30)
    print("Cloud Agent Platform - Usage Examples")
    print("🚀" * 30)
    
    # Run examples
    await example_llm_providers()
    await example_sandbox_isolation()
    await example_metrics()
    await example_task_scheduler()
    
    # Standalone agent requires API key
    if os.getenv("OPENAI_API_KEY"):
        await example_standalone_agent()
    else:
        print("\n⚠️  Skipping standalone agent example (OPENAI_API_KEY not set)")
    
    print("\n" + "=" * 60)
    print("All examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    # Configure logging
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger("INFO"),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    
    asyncio.run(main())
