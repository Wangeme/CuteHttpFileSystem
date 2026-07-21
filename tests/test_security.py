from __future__ import annotations

import unittest

from chfs.errors import AuthenticationError, PermissionDeniedError
from chfs.models import Permission, Principal
from chfs.security import Account, NetworkPolicy, SessionManager, hash_password, require, verify_password


class PasswordTests(unittest.TestCase):
    def test_password_round_trip_and_random_salt(self) -> None:
        first = hash_password("correct horse")
        second = hash_password("correct horse")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("correct horse", first))
        self.assertFalse(verify_password("wrong password", first))


class SessionTests(unittest.TestCase):
    def test_login_expiry_and_logout(self) -> None:
        now = [100.0]
        account = Account("alice", hash_password("correct horse"), frozenset({Permission.READ}))
        sessions = SessionManager([account], [], 60, clock=lambda: now[0])
        session = sessions.login("alice", "correct horse")
        self.assertEqual(sessions.resolve(session.token).name, "alice")
        now[0] = 161.0
        with self.assertRaises(AuthenticationError):
            sessions.resolve(session.token)

    def test_wrong_credentials_are_rejected(self) -> None:
        sessions = SessionManager([], [], 60)
        with self.assertRaises(AuthenticationError):
            sessions.login("nobody", "wrong password")

    def test_admin_implies_all_permissions(self) -> None:
        principal = Principal("root", frozenset({Permission.ADMIN}), True)
        require(principal, Permission.DELETE)
        with self.assertRaises(PermissionDeniedError):
            require(Principal("guest", frozenset(), False), Permission.READ)


class NetworkPolicyTests(unittest.TestCase):
    def test_deny_wins_and_allow_list_is_enforced(self) -> None:
        policy = NetworkPolicy(["192.168.0.0/16"], ["192.168.1.0/24"])
        self.assertTrue(policy.permits("192.168.2.3"))
        self.assertFalse(policy.permits("192.168.1.3"))
        self.assertFalse(policy.permits("10.0.0.1"))
        self.assertFalse(policy.permits("not-an-ip"))


if __name__ == "__main__":
    unittest.main()

