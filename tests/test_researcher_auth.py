import unittest

from researcher_auth import (
    AuthConfigurationError,
    COOKIE_NAME,
    DEFAULT_SESSION_SECONDS,
    ResearcherAccessDenied,
    ResearcherAuth,
)


class TestResearcherAuth(unittest.TestCase):
    def test_default_session_lasts_five_hours(self):
        self.assertEqual(DEFAULT_SESSION_SECONDS, 5 * 60 * 60)

    def make_auth(self, *, clock=lambda: 1_000, session_label="researcher"):
        return ResearcherAuth(
            shared_password="s3cr3t-phrase",
            session_label=session_label,
            cookie_secret="x" * 32,
            clock=clock,
        )

    def test_correct_password_receives_valid_signed_session(self):
        auth = self.make_auth()
        label = auth.verify_password("s3cr3t-phrase")
        header = auth.session_cookie_header()
        self.assertEqual(
            auth.email_from_cookie_header(header.split(";", 1)[0]),
            label,
        )

    def test_wrong_password_is_rejected(self):
        auth = self.make_auth()
        with self.assertRaises(ResearcherAccessDenied):
            auth.verify_password("guess")

    def test_cookie_expiry_and_tampering_are_rejected(self):
        now = [1_000]
        auth = self.make_auth(clock=lambda: now[0])
        value = auth.create_session_value()
        self.assertEqual(
            auth.email_from_cookie_header(f"{COOKIE_NAME}={value}"),
            "researcher",
        )
        self.assertIsNone(
            auth.email_from_cookie_header(f"{COOKIE_NAME}={value[:-1]}x")
        )
        now[0] += auth.session_seconds + 1
        self.assertIsNone(
            auth.email_from_cookie_header(f"{COOKIE_NAME}={value}")
        )

    def test_unrelated_cookie_does_not_hide_researcher_session(self):
        auth = self.make_auth()
        value = auth.create_session_value()
        cookie_header = (
            'g_state={"i_l":0,"i_ll":1721740000}; '
            f"{COOKIE_NAME}={value}"
        )
        self.assertEqual(
            auth.email_from_cookie_header(cookie_header),
            "researcher",
        )

    def test_missing_configuration_fails_closed(self):
        auth = ResearcherAuth(shared_password="", cookie_secret="")
        with self.assertRaises(AuthConfigurationError):
            auth.verify_password("anything")


if __name__ == "__main__":
    unittest.main()
