# Solverr documentation

New to Solverr? Start with the [quick start](../README.md#quick-start) in the main README, then come back here when you need more.

| Guide | Read it when you want to |
| ----- | ------------------------ |
| [Installation](installation.md) | install Solverr with Docker or from source, check it is healthy, or fix a browser that will not start on Debian |
| [How it works](how-it-works.md) | understand engines, fallback, sessions and cleanup, and why your IP address matters most |
| [API](api.md) | write your own client, or see every request parameter and the response |
| [Passthrough proxy](passthrough.md) | make an indexer app such as Prowlarr or Jackett work with a Cloudflare-protected site |
| [Configuration](configuration.md) | look up any setting |
| [Language and timezone](language-and-timezone.md) | control the language and timezone the browser reports, or choose the IP lookup services |
| [Troubleshooting](troubleshooting.md) | fix a site that keeps failing, an indexer that gives up, or a browser that runs out of memory |

Ready-to-use Docker Compose files are in [examples/](../examples/):

- [docker-compose.yml](../examples/docker-compose.yml): Solverr on its own. Recommended for most people, and for any app with a FlareSolverr setting.
- [docker-compose.indexers.yml](../examples/docker-compose.indexers.yml): Solverr with the passthrough, plus Prowlarr, for indexers behind Cloudflare. One line to change.

Developer records (how the code is built and what came from which upstream) live in [dev/](dev/). You do not need them to run Solverr.
