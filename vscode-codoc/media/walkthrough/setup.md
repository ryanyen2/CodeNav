## Set up codoc

**Set up codoc** does the following, with no terminal and no manual `pip`:

1. Installs Python and the codoc core in an isolated environment with `uv`. No system
   Python is needed.
2. Asks which model provider codoc's own passes use: your existing **Claude Code**
   login (no API key), an **OpenAI** API key or an **Anthropic** API key. The choice is
   written to the repository's `.env`, and `.env` is added to `.gitignore` before a key
   goes into it.
3. Indexes the repository and builds the first feature tree (`codoc init`), and
   installs the Claude Code hooks, the MCP server and the `/codoc:*` commands.
4. Starts the `codoc watch` daemon, which keeps the tree and the code in step. The
   extension keeps it running.

> Setup needs a **trusted workspace**, because it installs and runs the Python core. An
> untrusted workspace can open a tree the daemon has already built, but cannot set up or
> run the daemon.

[Set up codoc](command:codoc.setup)
