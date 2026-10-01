import unittest

from app.platform_core.domain_scope import DomainScope, require_domain


class DomainScopeTests(unittest.TestCase):
    def test_same_domain_is_allowed(self):
        require_domain(DomainScope.MEDICAL, DomainScope.MEDICAL)

    def test_cross_domain_is_fail_closed(self):
        with self.assertRaises(PermissionError):
            require_domain(DomainScope.MEDICAL, DomainScope.EDUCATION)

    def test_legal_does_not_cross_into_life(self):
        with self.assertRaises(PermissionError):
            require_domain(DomainScope.LEGAL, DomainScope.LIFE)


if __name__ == "__main__":
    unittest.main()
