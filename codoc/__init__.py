

# The shipped block kinds register themselves with the calculus (which Text
# fields they attribute, whether they are exported and whether they sit in the
# reading flow). Importing them here means any use of codoc sees the same
# document vocabulary, whoever imported what: a `comment` block is out of the
# flow in the daemon, the CLI, a test and the hub alike.
from codoc import kinds as _kinds  # noqa: E402,F401
