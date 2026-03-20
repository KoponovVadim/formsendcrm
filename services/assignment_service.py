from models.crm import Executor


def choose_best_executor(executors: list[Executor], service_category: str, priority: int) -> tuple[Executor | None, int]:
    """Auto assignment formula: score = skill_match - load + priority."""
    winner: Executor | None = None
    winner_score = -10**9

    for executor in executors:
        skill_match = 0
        for skill in executor.skills:
            if skill.service_category == service_category:
                skill_match = max(skill_match, int(skill.level or 0))

        load = int(executor.current_active_tasks or 0)
        score = skill_match - load + int(priority or 0)

        if score > winner_score:
            winner = executor
            winner_score = score

    return winner, winner_score
