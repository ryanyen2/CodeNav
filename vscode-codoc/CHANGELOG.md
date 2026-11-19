# Changelog

## Unreleased

- The extension runs on the op-log document model. The editor records each edit
  as an op on character identities, sends it to the daemon, and draws the page
  from the projection the daemon publishes.
- Descriptions can hold bullet and numbered lists, tables, code blocks and
  diagrams, and inline code.
- A tree made by an earlier version is not imported. `codoc init` builds a new
  one from the code.
