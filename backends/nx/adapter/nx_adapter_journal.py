"""NX-side half of `recad nx ...` (runs INSIDE NX 2206 via run_journal.exe, Python 3.10).

    run_journal.exe nx_adapter_journal.py -args <request.json> <result.json>

request: {"cmd": "inspect" | "set-param", "native": <.prt>, "step": <.step or null>,
          "name": .., "value": .., "native_out": <.prt or null>}
  inspect    open the part (never saved), extract features + bodies (+ STEP export of the saved
             file when "step" is given)
  set-param  open the part, set the user expression, update, feature errors, save the part as
             native_out (a copy; the input is never written), export STEP, extract again
"""

import json
import sys
import traceback

REQ, RESULT = sys.argv[1:3]
with open(REQ, encoding="utf-8") as fh:
    req = json.load(fh)
sys.path.insert(0, req["adapter_dir"])

import nx_measure as M  # noqa: E402

res = {"ok": False}
try:
    part = M.open_part(req["native"])
    M.update()
    errs = M.feature_errors(part)
    res["feature_errors"] = errs
    if req["cmd"] == "set-param":
        res["volume_before_mm3"] = round(M.volume(part, M.solids(part)), 6)
        res["old_formula"] = M.set_parameter(part, req["name"], req["value"])
        M.update()
        errs = M.feature_errors(part)
        res["feature_errors"] = errs
        M.save_as(part, req["native_out"])
        part = M.S.Parts.Work
    res.update(M.bodies_summary(part))
    res["features"] = M.extract(part, req.get("native_out") or req["native"])
    if errs:
        res["error"] = "feature errors: {}".format(
            "; ".join("{}: {}".format(k, " / ".join(v)) for k, v in errs.items())
        )
    elif req.get("step"):
        if res["bodies"] != 1:
            res["error"] = (
                f"canonical export needs exactly one solid body, part has {res['bodies']}"
            )
        else:
            res.update(M.step_export(part, req["step"]))
            res["ok"] = res.get("unit") == "mm"
            if not res["ok"]:
                res["error"] = "STEP written without mm units"
    else:
        res["ok"] = True
except Exception as e:  # noqa: BLE001
    res["ok"] = False
    res["error"] = f"{type(e).__name__}: {e}"
    res["traceback"] = traceback.format_exc()
M.write_json(RESULT, res)
