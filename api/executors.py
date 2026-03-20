from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from models.crm import Executor, Task
from services.push_service import push_service

router = APIRouter(prefix="/api/v1/executors", tags=["executors"])


@router.get("")
async def list_executors(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    executors = (await db.execute(select(Executor).order_by(Executor.current_active_tasks.asc()))).scalars().all()
    return [
        {
            "id": ex.id,
            "name": ex.name,
            "is_active": ex.is_active,
            "active_tasks": ex.current_active_tasks,
            "max_active_tasks": ex.max_active_tasks,
            "skills": [{"category": s.service_category, "level": s.level} for s in ex.skills],
        }
        for ex in executors
    ]


@router.post("/tasks/{task_id}/assign")
async def assign_task(task_id: int, payload: dict, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    executor_id = int(payload.get("executor_id", 0) or 0)
    if not executor_id:
        raise HTTPException(400, "executor_id is required")

    task = (await db.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if not task:
        raise HTTPException(404, "Task not found")

    executor = (await db.execute(select(Executor).where(Executor.id == executor_id))).scalar_one_or_none()
    if not executor:
        raise HTTPException(404, "Executor not found")

    task.executor_id = executor.id
    task.status = "assigned"
    executor.current_active_tasks = int(executor.current_active_tasks or 0) + 1
    await db.commit()

    await push_service.notify(
        token=str(payload.get("push_token", "")),
        title="Назначена задача",
        body=f"Задача #{task.id} назначена исполнителю {executor.name}",
        data={"task_id": str(task.id), "executor_id": str(executor.id)},
    )

    return {"ok": True, "task_id": task.id, "executor_id": executor.id}
