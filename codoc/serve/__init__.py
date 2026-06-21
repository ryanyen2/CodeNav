"""codoc serve — the home-hub web server (Tier 1).

A separate-process web server, peer to the VS Code extension and the MCP
server, that exposes the intent tree to GitHub-authorized remote users over a
tunnel. It supervises a single ``codoc watch`` daemon and is a file-channel
client: it reads ``.codoc/*`` to derive the browser UI and writes only the
verdict/draft channels (never ``tree.codoc``).

Unit U1 lands the supervision + server skeleton; auth (U4), SSE (U3), command
handlers (U5), the tunnel (U6), and the sandboxed realize→PR flow (U7/U8/U11)
land in later units.
"""
