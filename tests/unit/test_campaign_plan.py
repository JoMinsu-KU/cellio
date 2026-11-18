from collections import Counter
import pytest
from cellio.io import CellIOError, digest
from cellio.runtime.campaign import create_plan, finite_rows, initial_state, read_plan, soak_row


PROVENANCE = {i+"_hash":digest(i) for i in ("assets","protocol","environment","code")}


def test_canonical_blocks_and_pairing(tmp_path):
    plan = create_plan("canonical-v1", PROVENANCE, tmp_path)
    rows = plan["runs"]
    assert len(rows) == len({r["run_id"] for r in rows}) == 820
    counts = Counter("reference" if r["controller_id"]=="reference" else r["arm"]=="healthy" for r in rows)
    assert counts=={"reference":640, True:60, False:120}
    assert read_plan(tmp_path/"campaign.plan.json")==plan
    assert rows == finite_rows("canonical-v1",PROVENANCE)
    assert len({digest(initial_state("cell_a",s)) for s in range(1000,1020)})==20


def test_separate_suites():
    reset = finite_rows("reset-v1",PROVENANCE)
    assert len(reset)==144 and len({r["chain_id"] for r in reset})==48
    assert [r["chain_slot"] for r in reset[:3]]==["control","predecessor","post_reset"]
    assert len(finite_rows("overhead-v1",PROVENANCE))==180
    assert len(finite_rows("boundaries-v1",PROVENANCE))==16
    assert len(finite_rows("clean-install",PROVENANCE))==10
    assert soak_row(PROVENANCE,0)["run_id"] != soak_row(PROVENANCE,10)["run_id"]
    assert soak_row(PROVENANCE,330)["repeat_index"]==1


def test_lock_required(tmp_path):
    with pytest.raises(CellIOError):
        create_plan("canonical-v1",{},tmp_path)
