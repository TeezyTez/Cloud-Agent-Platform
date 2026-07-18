#!/usr/bin/env python3
"""
Cloud Agent Platform - 快速演示脚本

无需任何配置即可运行的演示，展示平台的核心功能。
"""
import asyncio
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sandbox import LocalSandbox
from scheduler import TaskScheduler, EventBus
from observability import get_metrics_collector, get_task_metrics
from src.models import Task, TaskPriority


async def demo_sandbox():
    """演示沙箱隔离功能"""
    print("\n" + "=" * 60)
    print("🎯 Demo 1: 沙箱隔离执行")
    print("=" * 60)
    
    # 创建两个独立沙箱
    sandboxes = []
    
    for i in range(2):
        sandbox = LocalSandbox()
        await sandbox.start()
        sandboxes.append(sandbox)
        
        # 每个沙箱有独立的文件系统
        await sandbox.write_file(f"message_{i}.txt", f"Hello from sandbox {i}!")
        await sandbox.execute_command(f"echo 'Sandbox {i} initialized'")
    
    print("\n📂 沙箱1 文件:")
    files1 = await sandboxes[0].list_directory(".")
    for f in files1:
        print(f"  - {f}")
    
    print("\n📂 沙箱2 文件:")
    files2 = await sandboxes[1].list_directory(".")
    for f in files2:
        print(f"  - {f}")
    
    # 在各自沙箱执行命令
    for i, sandbox in enumerate(sandboxes):
        result = await sandbox.execute_command(f"cat message_{i}.txt")
        print(f"\n🔧 沙箱{i+1} 执行结果: {result.stdout.strip()}")
    
    # 清理
    for sandbox in sandboxes:
        await sandbox.stop()
    
    print("\n✅ 沙箱完全隔离，互不影响！")


async def demo_scheduler():
    """演示任务调度器"""
    print("\n" + "=" * 60)
    print("🎯 Demo 2: 任务调度器")
    print("=" * 60)
    
    scheduler = TaskScheduler(max_workers=2)
    await scheduler.start()
    
    # 任务处理函数
    async def handle_task(task, event_bus):
        print(f"  📥 正在处理: {task.description[:40]}...")
        await asyncio.sleep(1)  # 模拟工作
        task.result = {"status": "done"}
        print(f"  ✅ 完成: {task.id[:8]}")
    
    # 提交多个任务
    tasks = [
        Task(description=f"任务 {i+1}", priority=TaskPriority.NORMAL)
        for i in range(3)
    ]
    
    print("\n📤 提交 3 个任务...")
    for task in tasks:
        await scheduler.submit_task(task, handle_task)
    
    print("⏳ 等待任务完成...")
    await asyncio.sleep(4)
    
    # 检查状态
    print("\n📊 任务状态:")
    for task in tasks:
        status = await scheduler.get_task_status(task.id)
        print(f"  {task.id[:8]}...: {status.status if status else 'unknown'}")
    
    await scheduler.stop()
    print("\n✅ 调度器演示完成！")


async def demo_metrics():
    """演示指标收集"""
    print("\n" + "=" * 60)
    print("🎯 Demo 3: 指标追踪")
    print("=" * 60)
    
    metrics = get_metrics_collector()
    task_metrics = get_task_metrics()
    
    # 模拟一些指标
    print("\n📈 记录模拟指标...")
    task_metrics.record_task_start("task-001", "coder")
    task_metrics.record_llm_call("gpt-4o", 1500, 0.045, 200.0, True)
    task_metrics.record_tool_call("terminal", 85.0, True)
    task_metrics.record_tool_call("file_editor", 45.0, True)
    task_metrics.record_task_complete("task-001", "coder", 5.2, True)
    
    # 获取指标
    all_metrics = metrics.get_all_metrics()
    
    print("\n📊 当前指标:")
    for m in all_metrics.get("metrics", []):
        name = m["name"]
        value = m["value"]
        labels = m.get("labels", {})
        
        if isinstance(value, dict):
            print(f"  {name}: count={value.get('count', 0)}, "
                  f"mean={value.get('mean', 0):.2f}")
        else:
            print(f"  {name}: {value}")
    
    print("\n✅ 指标系统工作正常！")


async def demo_llm_config():
    """演示 LLM 配置"""
    print("\n" + "=" * 60)
    print("🎯 Demo 4: LLM 提供商配置")
    print("=" * 60)
    
    from llm import LLMFactory
    
    # 列出支持的提供商
    print("\n🔧 支持的 LLM 提供商: OpenAI")
    
    # OpenAI 配置示例
    openai = LLMFactory.create(
        provider="openai",
        api_key="demo-key"
    )
    print(f"\n📋 OpenAI 支持的模型:")
    for model in openai.supported_models:
        print(f"  - {model}")
    
    print("\n✅ LLM 配置示例完成！")
    print("\n💡 要实际运行 Agent，需要设置 OPENAI_API_KEY")


async def main():
    print("\n" + "🌟" * 30)
    print("   Cloud Agent Platform - 快速演示")
    print("🌟" * 30)
    
    # 运行所有演示
    await demo_sandbox()
    await demo_scheduler()
    await demo_metrics()
    await demo_llm_config()
    
    print("\n" + "=" * 60)
    print("🎉 所有演示完成！")
    print("=" * 60)
    
    print("\n📚 下一步:")
    print("  1. 查看 USAGE_GUIDE.md 了解完整使用方法")
    print("  2. 查看 examples/usage.py 了解更多场景")
    print("  3. 设置 API Key 后运行 Agent:")
    print("     export OPENAI_API_KEY='your-key'")
    print("     python -m api  # 启动 API 服务")


if __name__ == "__main__":
    import structlog
    
    # 配置日志
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger("INFO"),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    
    asyncio.run(main())
