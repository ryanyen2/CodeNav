# Changelog

## Unreleased

- The extension now runs on the op-log document model. The tree is a document
  of identified blocks. The editor records each edit as an operation on
  character identities and sends it to the daemon, and it draws the page from
  the projection the daemon publishes. Who wrote each word, what an agent
  proposes and what the code changed are read from that projection.
- A tree made by an earlier version is imported the first time setup runs.
