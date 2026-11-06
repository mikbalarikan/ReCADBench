"""NX-side wrapper of `recadbench nx-run` (runs INSIDE NX 2206 via run_journal.exe, Python 3.10).

    run_journal.exe nx_wrapper.py -args <journal.py> <part.prt> <result.json> <adapter_dir>
                                        [<lib_dir>]

1. open <part.prt> (create it from the plain mm template when absent) as work + display part;
2. run the agent's journal with runpy (``__name__ == "__main__"``, ``sys.argv == [journal]``);
   the journal gets the part with ``NXOpen.Session.GetSession().Parts.Work``;
   the journal's own folder and <lib_dir> (the run dir's ``lib/``) are on ``sys.path``;
3. update with failure action AcceptAll, collect feature errors;
4. only if the journal raised nothing, left the same work part and no feature reports an error:
   save the part (TRANSACTION: a failed run leaves part.prt as it was before the run);
5. write <result.json>: {ok, error, traceback, saved, features[], count, unmapped[], bodies,
   sheets, volume, faces, feature_errors, syslog} - measured here, never self-reported.
"""

import os
import runpy
import sys
import time
import traceback

T0 = time.time()
JOURNAL, PART, RESULT, ADAPTER_DIR = sys.argv[1:5]
LIB_DIR = sys.argv[5] if len(sys.argv) > 5 else ""
sys.path.insert(0, ADAPTER_DIR)

import nx_measure as M  # noqa: E402
import NXOpen  # noqa: E402

S = NXOpen.Session.GetSession()
res = {
    "ok": False,
    "error": None,
    "traceback": None,
    "saved": False,
    "journal": JOURNAL,
    "part": PART,
    "created": not os.path.exists(PART),
}
try:
    res["syslog"] = S.LogFile.FileName
    res["syslog_offset"] = os.path.getsize(res["syslog"])  # excerpt = what this run wrote
except Exception:  # noqa: BLE001
    res["syslog"] = None

part = None
try:
    os.makedirs(os.path.dirname(os.path.abspath(PART)), exist_ok=True)
    part = M.new_part(PART) if res["created"] else M.open_part(PART)
except Exception as e:  # noqa: BLE001
    res["error"] = "cannot {} part {}: {}".format("create" if res["created"] else "open", PART, e)
    res["traceback"] = traceback.format_exc()

if part is not None:
    jdir = os.path.dirname(os.path.abspath(JOURNAL))
    for d in (LIB_DIR, jdir):
        if d and d not in sys.path:
            sys.path.insert(0, d)
    saved_argv = sys.argv
    try:
        sys.argv = [JOURNAL]
        runpy.run_path(JOURNAL, run_name="__main__")
        journal_ok = True
    except SystemExit as e:  # a journal calling sys.exit(0) has finished normally
        journal_ok = e.code in (None, 0)
        if not journal_ok:
            res["error"] = f"journal called sys.exit({e.code!r})"
    except BaseException as e:  # noqa: BLE001
        journal_ok = False
        res["error"] = f"{type(e).__name__}: {e}"
        res["traceback"] = traceback.format_exc()
    finally:
        sys.argv = saved_argv
    try:
        work = S.Parts.Work
        if work is None or work.Tag != part.Tag:
            journal_ok = False
            res["error"] = (
                res["error"] or "the journal changed the work part (must model in cad/part.prt)"
            )
        M.update()
        errs = M.feature_errors(part)
        res["feature_errors"] = errs
        if errs and journal_ok:
            journal_ok = False
            res["error"] = "feature errors after update: {}".format(
                "; ".join("{}: {}".format(k, " / ".join(v)) for k, v in errs.items())
            )
        if journal_ok:
            M.save(part)
            res["saved"] = True
        ext = M.extract(part, PART)
        res.update(
            features=[
                {
                    "name": f["native_name"],
                    "type": f["native_type"],
                    "taxonomy_type": f["taxonomy_type"],
                    "counted": f["counted"],
                }
                for f in ext["features"]
            ],
            count=ext["count"],
            unmapped=ext["unmapped"],
            parameters=ext["parameters"],
        )
        if ext.get("not_in_final_part"):
            res["not_in_final_part"] = ext["not_in_final_part"]
        res.update(M.bodies_summary(part))
        res["ok"] = journal_ok
    except Exception as e:  # noqa: BLE001
        res["ok"] = False
        res["error"] = res["error"] or f"measurement failed: {e}"
        res["traceback"] = res["traceback"] or traceback.format_exc()

res["nx_elapsed_s"] = round(time.time() - T0, 3)
M.write_json(RESULT, res)
