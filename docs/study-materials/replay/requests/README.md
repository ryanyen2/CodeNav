# The request the recorded agent was given

Each file holds the prompt the agent received, and nothing else. The prompt is
what the participant is told they wrote before lunch.

Three things are asked for in one request, so that no run of the transcript is
about one intent and no single file carries one intent. Settings threading
touches every policy module, the new output path touches the pipeline, and the
configuration file adds a layer above both.

The request is deliberately silent about what should happen by default. An
underspecified request is the normal case, and the point of the study is that
the agent fills the gaps with choices that constrain every later change. A
request that pinned the defaults down would answer the question we are asking.
