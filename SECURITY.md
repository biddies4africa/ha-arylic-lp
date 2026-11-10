# Security policy

## Supported versions

Security fixes are made in the latest release only. Please make sure you're running it before
reporting a problem.

## Reporting a vulnerability

Please report vulnerabilities privately rather than in a public issue. On this repository, go to
**Security** > **Report a vulnerability**, and describe the problem and how to reproduce it. You'll
receive a reply through the private advisory.

## Out of scope

The players' control port (TCP 2018) has no authentication, so any device on the same network can
change the volume or source. This is how the player's firmware works, and the integration cannot
change it. Keep the players on a network you trust.
