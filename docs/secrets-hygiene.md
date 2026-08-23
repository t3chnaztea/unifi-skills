# Secrets hygiene for agent-driven ops

These skills have an agent operate the device that runs your whole network. The
agent needs the *capability*. It never needs the *credential*. Those are
different things, and most setups hand over both without noticing.

An agent transcript is a log file. Anything the agent reads or prints lands in
it, gets summarized into future context, and may sync to whatever service hosts
the session. A key that appears in a transcript once is not a secret anymore.

## The exposure ladder

From worst to best. Each rung removes a place the key can leak.

1. **Key pasted into the chat.** Never. It is now in the transcript forever,
   and in every summary of that transcript.
2. **Key in a file the agent opens.** The common default, and barely better:
   `cat ~/.config/unifi/env` puts the key in context just as surely as pasting
   it. If the agent can read the file, assume it will, once, on the wrong day.
3. **Key in the environment, injected by the tool.** The agent runs
   `udm.py devices`; the script reads `UNIFI_API_KEY` from the env file
   itself. The agent sees devices, not the key. This is the floor these
   skills assume, and it costs nothing: the env-file pattern in the README is
   already this.
4. **Key held by a broker the agent cannot read.** The credential lives with a
   separate user or host; the agent calls a wrapper, the wrapper injects, the
   agent gets output only. Even a compromised or confused agent has nothing to
   exfiltrate. [HomelabHero](https://github.com/serversathome/homelabhero)
   productized this shape for SSH-driven homelab ops and deserves the credit;
   the same idea works here with nothing fancier than a sudo rule to a
   locked-down user that owns the env file.

Rung 3 is the sane default for a home network. Reach for rung 4 when the agent
runs unattended, or when the controller is one you answer for but do not own.

## Rules that hold at any rung

- **Env file outside the repo, `chmod 600`, never committed.** The README's
  `~/.config/unifi/env` location is deliberate: no repo history, no accidental
  `git add`.
- **Mint the agent its own named key.** Named keys are individually revocable,
  and when a policy appears that nobody admits writing, the key name answers
  who. Covered in `unifi-connect`, restated here because it is a hygiene rule,
  not just an auth step.
- **Default to the read-only brake.** `UNIFI_READONLY=1` in the env file makes
  every write hard-fail. Lift it per session, on purpose, not permanently.
- **Never echo the variable.** Not to debug, not to "check it is set". Use
  `test -n "$UNIFI_API_KEY" && echo set` if you must confirm existence.
- **Watch your own logs.** Shell history, `curl -v` output, and audit trails
  can all reproduce a header that carries the key. Redact before anything
  leaves the machine.

## The test

Ask the agent what the API key is. The right answer is that it cannot know.
If it can answer, you are on rung 2, and one bad prompt away from rung 1.
