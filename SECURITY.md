# Security policy

## Reporting a problem

Please report security problems privately, not in a public issue: on this repository, open
**Security → Report a vulnerability**. You'll get a reply there.

Only the latest release is supported, so please check the problem still happens on it.

## What this integration can't fix

The players' control port (TCP 2018) has no authentication: anything on the same network can
change volume or source. That's how the firmware works, not something this integration can
change. Keep the players on a network you trust (see the README's **Security note**).
