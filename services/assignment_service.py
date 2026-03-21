from models.crm import Executor


def choose_best_executor(executors: list[Executor], priority: int) -> tuple[Executor | None, int]:
    """Auto assignment formula: score = -load + priority."""
    winner: Executor | None = None
    winner_score = -10**9

    for executor in executors:
        load = int(executor.current_active_tasks or 0)
        score = -load + int(priority or 0)

        if score > winner_score:
            winner = executor
            winner_score = score

    return winner, winner_score
