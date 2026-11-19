

# The shipped block kinds register their layouts with the calculus (which
# Text fields they attribute, whether they are exported and whether they sit in
# the reading flow). Importing them here means every process gives a new block
# of a shipped kind the same layout, whoever imported what: a `comment` added
# by the daemon, the CLI, a test or the hub is out of the flow alike. Once
# added, the block carries its layout, so a replay does not need the registry.
from codoc import kinds as _kinds  # noqa: E402,F401
