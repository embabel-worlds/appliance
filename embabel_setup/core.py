"""The two things every other module needs: where the appliance is, and how it
fails. Kept apart so nothing has to import a large module to raise an error."""
import os

# Every path is absolute rather than relying on a chdir, because the Me app and
# the CLI both call in from their own working directories.
APPLIANCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SetupError(Exception):
    """Anything the operator can act on. Printed as a sentence, never a traceback."""


class AlreadySetUp(SetupError):
    """The appliance is up and configured — not a failure, and handled as such."""


class Unreachable(SetupError):
    """The door did not answer. Distinct from a refusal, which is an answer."""


class TokenRejected(SetupError):
    """The setup token was wrong."""
