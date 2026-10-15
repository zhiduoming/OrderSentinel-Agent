"""Regression for partial setup failure invalidating an old configuration binding."""
import asyncio
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException


class LabSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_new_setup_and_controller_restart_invalidate_old_proposal(self):
        state=Path(tempfile.mkdtemp(prefix="incident-lab-controller-"))
        with patch.dict(os.environ,{"INCIDENT_LAB_STATE":str(state)}):
            from lab import controller as c
        with patch.object(c,"STATE",state),patch.object(c,"DB",state/"lab.sqlite3"),patch.object(c,"lock",asyncio.Lock()):
            async with c.lifespan(c.app):
                cid="LIVE-"+"b"*32
                case={"case_id":cid,"revision":10,"boot_ids":{"order":"boot-order","inventory":"boot-inventory"}}
                with closing(c.connect()) as db,db:
                    db.execute("INSERT INTO experiments VALUES(?,?,?)",(cid,json.dumps(case),"test"))
                    db.execute("UPDATE state SET active_case=?,revision=10 WHERE id=1",(cid,))
                calls=AsyncMock(return_value={"status":"ok"})
                with patch.object(c,"call",calls),patch.object(c,"load",AsyncMock(return_value={"metrics":{"error_rate":.2}})):
                    with self.assertRaises(HTTPException) as failed:
                        await c.prepare(c.Experiment(scenario="pool"))
                    self.assertEqual(failed.exception.status_code,503)
                    self.assertEqual(calls.await_count,2)  # Both configs already changed before baseline failed.
                with closing(c.connect()) as db:
                    self.assertEqual(db.execute("SELECT active_case,revision FROM state").fetchone(),(None,11))
                with patch.object(c,"health_pair",AsyncMock(return_value={"order":{"boot_id":"boot-order"},"inventory":{"boot_id":"boot-inventory"}})):
                    with self.assertRaises(HTTPException) as stale:
                        await c.execute(c.Execution(operation_id="c"*32,case_id=cid,expected_revision=10,action="restore_order_pool"))
                    self.assertEqual(stale.exception.status_code,409)
                with closing(c.connect()) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM executions").fetchone()[0],0)
                with closing(c.connect()) as db,db:
                    db.execute("UPDATE state SET active_case=? WHERE id=1",(cid,))
            async with c.lifespan(c.app):
                with closing(c.connect()) as db:
                    self.assertEqual(db.execute("SELECT active_case,revision FROM state").fetchone(),(None,12))


if __name__=="__main__":unittest.main()
