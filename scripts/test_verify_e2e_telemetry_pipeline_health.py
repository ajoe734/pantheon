"""Unit tests for the telemetry pipeline-health verifier's evaluate logic."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_telemetry_pipeline_health",
    os.path.join(_HERE,"verify_e2e_telemetry_pipeline_health.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

def _healthy():
    return {"backpressure":{"pressure_level":"normal","recent_errors":0,"recent_writes":5,
            "buffer_utilization_critical":0.9,"error_rate_critical":0.3},
            "buffer":{"utilization_pct":0.0,"size":0,"total_rejected":0,"total_enqueued":10,"total_dequeued":10}}

class TestEvaluate(unittest.TestCase):
    def test_healthy_ok(self):
        ok,p=mod.evaluate(_healthy(),10000); self.assertTrue(ok); self.assertEqual(p,[])
    def test_rejected_fails(self):
        s=_healthy(); s["buffer"]["total_rejected"]=5
        ok,p=mod.evaluate(s,10000); self.assertFalse(ok)
    def test_critical_pressure_fails(self):
        s=_healthy(); s["backpressure"]["pressure_level"]="critical"
        self.assertFalse(mod.evaluate(s,10000)[0])
    def test_buffer_saturation_fails(self):
        s=_healthy(); s["buffer"]["utilization_pct"]=95.0
        self.assertFalse(mod.evaluate(s,10000)[0])
    def test_backlog_fails(self):
        s=_healthy(); s["buffer"]["size"]=20000
        self.assertFalse(mod.evaluate(s,10000)[0])
    def test_error_rate_fails(self):
        s=_healthy(); s["backpressure"]["recent_errors"]=10; s["backpressure"]["recent_writes"]=10
        self.assertFalse(mod.evaluate(s,10000)[0])

if __name__=="__main__": unittest.main()
