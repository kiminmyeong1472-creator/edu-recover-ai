import unittest

from app.services import code_execution_runtime as rt


class Fake:
    def __init__(self, destroy, gets=()):
        self.destroy, self.gets, self.calls = destroy, list(gets), []

    def __call__(self, query, variables):
        self.calls.append(query)
        if query == rt._DESTROY:
            return {"sandboxDestroy": self.destroy}
        if query == rt._GET:
            return {"sandbox": self.gets.pop(0) if self.gets else None}
        raise AssertionError(query)


def session(fake):
    s = rt.RailwayCodeSession(graphql=fake, environment_id="env", sleep=lambda _: None)
    s.sandbox_id = "sb1"
    return s


class CleanupTests(unittest.TestCase):
    def test_null_destroy_reply_counts_as_destroyed(self):
        s = session(Fake(None))
        s._destroy()
        self.assertTrue(s.cleanup_status["confirmed"])

    def test_destroying_then_missing_counts_as_destroyed(self):
        s = session(Fake({"status": "DESTROYING"}, [{"status": "DESTROYING"}, None]))
        s._destroy()
        self.assertTrue(s.cleanup_status["confirmed"])

    def test_first_failure_is_not_hidden_by_cleanup(self):
        class Broken(Fake):
            def __call__(self, query, variables):
                raise rt.CodeExecutionError("SERVICE_UNAVAILABLE")
        s = session(Broken(None))
        first = rt.CodeExecutionError("TOOLS_MISSING")
        # __exit__ must not raise its own cleanup error over the first cause.
        self.assertFalse(s.__exit__(type(first), first, None))
        self.assertFalse(s.cleanup_status["confirmed"])


if __name__ == "__main__":
    unittest.main()


class RefusedDestroyTests(unittest.TestCase):
    def test_refused_destroy_of_gone_vm_counts_as_destroyed(self):
        class Refuse(Fake):
            def __call__(self, query, variables):
                if query == rt._DESTROY:
                    raise rt.CodeExecutionError("REQUEST_REJECTED")
                return super().__call__(query, variables)
        s = session(Refuse(None))
        s._destroy()
        self.assertTrue(s.cleanup_status["confirmed"])
