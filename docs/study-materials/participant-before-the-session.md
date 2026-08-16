# Before the session

Thank you for taking part. Please read this page and set up your machine before
we meet. Setup takes about 15 minutes, and most of it runs on its own.

Start from the link the researcher sent you, not from this page. The link opens
a page that walks you through everything in order, including the consent form,
and it tells you when to come back here. If you have lost the link, ask for it
again. It carries your participant code, and nothing here works without it.

## What the session is

You will do two short programming tasks, one after the other, on two small
codebases you have not seen before. In each task a coding agent is available and
you can use it as much or as little as you like. We are studying how people and
coding agents share the written description of a codebase, so we are interested
in how you work, not in how fast you are.

The session runs for about 105 minutes on a video call. We will ask you to think
out loud while you work, and we will ask you questions about the code afterwards.

We record the call, your screen, and your voice. We also save the files you
change and the conversation you have with the agent. During the session we will
ask you to run two short scripts from the bundle, one that saves your work every
20 seconds and one at the end that packs it all into a single zip for you to send
us. You can open both and read them first if you want to.

We label everything with a code rather than your name, and we remove your name
before anyone analyses the data. You can stop at any time, and we will delete
your recording if you ask.

## What you need

You need a Mac or a Linux machine. On Windows, everything below works inside
WSL, so install WSL first if you do not have it.

You also need two programs that ask you to sign in, so please install them
yourself:

- Claude Code. Install it with `curl -fsSL https://claude.ai/install.sh | bash`.
  You do not need to sign in, and you do not need to buy a plan. We provide the
  account.
- Visual Studio Code, from https://code.visualstudio.com. Open it once, press
  Cmd+Shift+P (Ctrl+Shift+P on Linux), and run the command
  "Shell Command: Install 'code' command in PATH".

You do not need Python. The setup script installs everything else.

## About the two keys

We pay for the models, so nothing in the session costs you anything. We will
give you two keys, separately from this bundle, and the setup script asks for
them. They are not shown as you type, so they will not appear on screen while
you are sharing it.

They are written only into the four project folders, so deleting those folders
removes them. Nothing is added to your shell, and nothing changes for your own
projects.

If you already use Claude Code with your own account, that keeps working
everywhere except these four folders. Inside them the study's account is used
instead, so your own plan is never spent on this.

Please do not use these keys for anything else, and tell us if you think one has
ended up somewhere it should not have. We turn them off after the study.

## Setting up

Unzip the bundle we sent you and open a terminal in the unzipped folder. Then run
the command from your study page. It looks like this, with your own code in it:

```
./setup.sh p-abcdefghjkmn codoc-first
```

Copy it from the page rather than typing it. The code is how your work is filed,
and a machine without it records nothing. If you run `./setup.sh` with nothing
after it, it will ask you for the code before it starts.

It prints a line for each thing it does, and it takes about 10 minutes. When it
finishes it either says "Everything is ready" or lists what is still missing. If
anything is missing, fix it and then run this from the same folder:

```
./setup.sh --check
```

Send us the last few lines of the output either way, so we can sort out any
problem before the session rather than during it.

## Two requests

Please do not open the two project folders in `~/codoc-study` before we meet,
and please do not look at their code. The session measures what you can work out
during it, so a head start would make your data unusable.

Please also close anything you do not want on camera, because you will be
sharing your whole screen.

## If setup fails

Send us the output and we will fix it with you. Common problems:

- The `code` command is not found. Open VS Code and run the "Shell Command"
  step above.
- `claude` is not found. Close and reopen your terminal after installing it,
  because the installer changes your PATH.
- The script stops on the Python step. Your machine may block downloads from
  `astral.sh`. Tell us and we will send you a different bundle.
