"""P1 placeholder test so CI has something to run; replace with real tests."""


from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "recadbench_spec"


def test_contracts_present() -> None:
    names = {p.name for p in (PKG / "schemas").glob("*.schema.json")}
    assert "re_plan_ir.schema.json" in names
    assert len(names) == 10
    assert (PKG / "feature_taxonomy.yaml").is_file()
