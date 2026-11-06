"""recadbench-eval: the ReCADBench evaluator (gate, alignment, S1-S5, plan score, report).

    from recadbench_eval.score import score_run, write_result
    res = score_run("runs/<run_id>", part_id="r385_hs01_motor_mount")
    write_result(res, "runs/<run_id>/result.json")

Ground truth is read only from ``RECADBENCH_GT_DIR`` (``recadbench_eval.gtstore``); backend
code is never imported. Implements 03_SCORING.md with the ADR 0002 scan rules.
"""

__version__ = "0.1.0"
