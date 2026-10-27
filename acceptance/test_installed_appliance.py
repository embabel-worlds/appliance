"""What must be true of an appliance somebody has just installed.

Every assertion here is a promise the product makes to the person who ran the
installer: setup closes for good, their account is the way in, nothing answers
without one, the console is served, and the doors the surfaces block advertises
are open. Black box over HTTP — no imports from embabel_setup, no reading of the
data volume, no knowledge of how the stack was started.

    APPLIANCE_BASE=http://localhost:11043 APPLIANCE_USER=… APPLIANCE_PASSWORD=… \
        python3 -m unittest discover -s acceptance -v

WHAT IS NOT HERE: anything that needs a model provider. A keyless appliance is a
supported shape and the only one that installs without money, a secret and
somebody else's uptime in the path, so the suite that gates a pull request stays
inside it. Conversation, embeddings and document conversion belong to the lane
that has a key.
"""

from __future__ import annotations

import os
import unittest

import appliance


class SetupIsClosed(unittest.TestCase):
    """The one-way door.

    `/api/v1/setup` is unauthenticated by necessity — it is how a fresh appliance
    is claimed — so "setup is complete" and "that API is shut" have to be the same
    fact. A 200 here on an installed appliance would mean anybody who can reach
    the port can create the operator account.
    """

    @classmethod
    def setUpClass(cls):
        appliance.wait_for_api()

    def test_the_setup_api_is_gone(self):
        status, _ = appliance.request("/api/v1/setup", token="probe")
        self.assertEqual(410, status, "setup must answer 410 Gone once it is complete")

    def test_it_is_gone_without_a_token_too(self):
        # 410 BEFORE 401, which is the server's own order: a finished install must
        # not become a place to test whether a guessed token is right.
        status, _ = appliance.request("/api/v1/setup")
        self.assertEqual(410, status)


class TheAccountIsTheWayIn(unittest.TestCase):
    """The credential the installer created, and nothing else, reads a world."""

    @classmethod
    def setUpClass(cls):
        appliance.wait_for_api()
        cls.user, cls.password = appliance.account()

    def test_the_created_account_signs_in(self):
        status, body = appliance.request("/api/v1/worlds", self.user, self.password)
        self.assertEqual(200, status, f"sign-in failed for {self.user}: {body[:200]!r}")
        self.assertIsNotNone(appliance.json_body(body), "worlds did not answer JSON")

    def test_the_wrong_password_is_refused(self):
        status, _ = appliance.request("/api/v1/worlds", self.user, "not-the-password")
        self.assertEqual(401, status)

    def test_no_credential_is_refused(self):
        status, _ = appliance.request("/api/v1/worlds")
        self.assertEqual(401, status)

    def test_an_unknown_user_is_refused(self):
        # The same 401 as a wrong password: which half was wrong is not a stranger's
        # business, and a different answer here is a username oracle.
        status, _ = appliance.request("/api/v1/worlds", "nobody-by-that-name", "whatever")
        self.assertEqual(401, status)


class TheSurfacesAnswer(unittest.TestCase):
    """Each thing the install's closing block told the operator to go to."""

    @classmethod
    def setUpClass(cls):
        appliance.wait_for_api()

    def test_the_console_is_served(self):
        status, _ = appliance.request(appliance.console_url() + "/")
        self.assertEqual(200, status, f"the console did not answer on {appliance.console_url()}")

    def test_mcp_refuses_an_unauthenticated_caller(self):
        # The surfaces block prints these two with "Authorization: Bearer" beside
        # them. Without one they must refuse: a coding agent's endpoint reachable
        # anonymously is every realm it can author.
        for path in ("/mcp/chat", "/mcp/code"):
            with self.subTest(path=path):
                status, _ = appliance.request(path)
                self.assertIn(status, (401, 403), f"{path} answered {status} with no bearer token")

    @unittest.skipUnless(os.environ.get("APPLIANCE_SQL_PORT"),
                         "APPLIANCE_SQL_PORT not set — the SQL door's host port is lane-specific")
    def test_the_sql_door_is_open(self):
        # The door moved from a sidecar into the server; what the operator is
        # promised is a Postgres URL on this port, and the weakest honest check of
        # that promise is that something accepts a connection there.
        port = int(os.environ["APPLIANCE_SQL_PORT"])
        self.assertTrue(appliance.port_open(port),
                        f"nothing is listening on the advertised SQL port {port}")


class PortsAreBoundWhereTheyShouldBe(unittest.TestCase):
    """Loopback for everything except Bolt, which is published on purpose.

    The appliance's whole security argument for its unauthenticated surfaces is
    `127.0.0.1` — Grafana says so in its own comment. Bolt (7687/7688) is the
    stated exception: it exists to be dialled by Neo4j Browser and the drivers
    from another machine, every session signs in, and BOLT_BIND pins it to
    loopback for anybody who would rather it did not. So this asserts the rule and
    its one exception, which is what makes a THIRD port appearing on 0.0.0.0 a
    finding rather than noise.
    """

    @classmethod
    def setUpClass(cls):
        cls.project = os.environ.get("COMPOSE_PROJECT") or ""
        cls.ports = appliance.published_ports(cls.project) if cls.project else {}
        if not cls.ports:
            raise unittest.SkipTest(
                "COMPOSE_PROJECT not set, or docker is not here — this check is about "
                "the host's bindings, which only docker can report")

    def test_only_bolt_is_published_beyond_loopback(self):
        offenders = []
        for name, ports in self.ports.items():
            for mapping in (p.strip() for p in ports.split(",") if p.strip()):
                if "->" not in mapping or mapping.startswith("127.0.0.1:"):
                    continue
                container_port = mapping.split("->", 1)[1].split("/")[0]
                if container_port in ("7687", "7688"):
                    continue  # Bolt, published deliberately
                offenders.append(f"{name}: {mapping}")
        self.assertEqual([], offenders,
                         "published beyond loopback and not Bolt: " + "; ".join(offenders))


if __name__ == "__main__":
    unittest.main()
