# Passthrough proxy

_Doc map: [README.md](README.md)._

The passthrough is an optional second port that hands your app the solved page directly. Use it when an app still fails on a Cloudflare-protected site even though Solverr is set up as its FlareSolverr. Indexer managers such as Prowlarr and Jackett are the usual case.

## Why an app can still fail

Many apps do not use the page Solverr sends back. They take the `cf_clearance` cookie from it and fetch the URL again with their own HTTP client. Cloudflare can tell that second request comes from a different program than the browser that solved the challenge (its TLS and HTTP/2 details and its headers differ), so it challenges again, and the app fails even though the solve worked.

The passthrough removes that second fetch. Your app asks Solverr's passthrough port for the page instead of asking the site. Solverr solves it the same way as any other request (with fallback, sessions, and the memory of which engine cleared the site) and returns the page as a plain `200` response. The app never sees a challenge, so it never fetches again.

Each allowed site gets its own session, so once a site has been cleared, its next request reuses the same browser and cookies.

## Turn it on

Add these to Solverr's `environment:` and restart it:

```yaml
    environment:
      - PASSTHROUGH_ENABLED=true
      - PASSTHROUGH_ALLOWED_HOSTS=example-site.tld,mirror.example.tld
```

`PASSTHROUGH_ALLOWED_HOSTS` is the list of sites the passthrough may fetch. It fetches nothing else, so it can never be used as an open proxy. With the list empty, every request gets a `404`.

The passthrough listens on port `8888` (`PASSTHROUGH_PORT`). An app in the same Docker network reaches it by Solverr's container name and needs nothing published. To reach it from another machine, publish the port as well:

```yaml
    ports:
      - "8191:8191"
      - "8888:8888"
```

## How the address works

The site goes in the first part of the path. A request to:

```
http://<solverr-host>:8888/example-site.tld/some/path/1/
```

is solved as `https://example-site.tld/some/path/1/`.

`<solverr-host>` is wherever Solverr runs. If your app runs in the same Docker network, that is Solverr's container name (`solverr` in the examples in these docs). Otherwise it is the IP address or hostname of the machine running Solverr.

Write the site as a bare host (`example-site.tld`), never as `https://example-site.tld`. Some apps tidy up a double slash inside a path, which would break an address that has a scheme inside it.

### Links inside the site, and the default mirror

A site's pages link to other pages on the same site with paths like `/details/...` or `/download.php?id=1`. When your app follows one of those, the first part of the path is not a site from your list. The passthrough sends those requests to the **default mirror**: the first host in `PASSTHROUGH_ALLOWED_HOSTS`. That is what makes detail pages, downloads and further result pages work.

Three things follow from that:

- **List mirrors of one site only**, never unrelated sites, because all of those links go to the first one.
- **Put the mirror you want those links served by first.** Searches work on any listed mirror, but the follow-up links always go to the first. If that mirror goes down, move a working one to the front: otherwise searches keep working and every download fails.
- **A mirror you forget to list is not refused.** The passthrough cannot tell it apart from one of those site-internal links, so the default mirror serves it.

## Add an indexer to Prowlarr or Jackett

You do not need a special indexer file. Take the site's existing definition and change where it points:

1. Download the site's definition file from the [Prowlarr Indexers repository](https://github.com/Prowlarr/Indexers) (or Jackett's). Download the raw file instead of copying and pasting its text: a few definitions contain invisible characters that pasting strips out, and the indexer then fails with "No title provided" on every result.
2. Change `id:` and `name:` to something new (for example, add `-passthrough` to the end), so it can sit next to the original.
3. Replace the `links:` list with one line per mirror, each pointing through the passthrough:

   ```yaml
   links:
     - http://solverr:8888/example-site.tld/
     - http://solverr:8888/mirror.example.tld/
   ```

4. Add every one of those mirror hosts to `PASSTHROUGH_ALLOWED_HOSTS`, and restart Solverr.
5. Save the file in your app's custom definitions folder, creating the folder if it does not exist, and restart the app:
   - **Prowlarr**: `/config/Definitions/Custom/`. Prowlarr ignores files placed directly in `Definitions/`, so the `Custom` folder matters.
   - **Jackett**: the custom definitions folder Jackett prints in its startup log, commonly `/config/Jackett/Indexers/custom/` on the linuxserver image.
6. Add the indexer in the app and pick one of the mirrors as its **Base URL**.
7. **Do not give it a FlareSolverr or proxy tag.** The passthrough already does the solving, and a proxy tag would send the requests around it.

Everything else in the definition (search paths, selectors, categories) stays as it was.

## What it does and does not do

- **`GET` and `HEAD` only.** Request bodies are not passed on. Most indexer definitions only use `GET`.
- **Static files get an instant `404`.** A path ending in a script, stylesheet, image, font or video extension is never worth starting a browser for. Only the path is checked, so a page whose query string happens to end that way is still fetched.
- **It uses `DEFAULT_ENGINE`.** A passthrough request cannot choose an engine. That matters for PDFs, which come back as the real file only from the stealth engine (see [PDFs](api.md#pdfs)), so set `DEFAULT_ENGINE=stealth` if the site serves them.
- **It is bound by IP reputation**, like every solve. If a site blocks your IP address, a residential `PROXY_URL` applies to passthrough requests too. See [Why your IP address matters most](how-it-works.md#why-your-ip-address-matters-most).
- **Each request gets 90 seconds** (`PASSTHROUGH_TIMEOUT_MS`). That stays under the roughly 100 seconds an indexer app waits before it records a failure and backs off from the indexer.

## The cache

The passthrough keeps successful pages for an hour (`PASSTHROUGH_CACHE_TTL`), so asking for the same page again does not cost another solve. It never keeps a challenge page or an error status, so a moment of blocking is retried instead of stuck.

A site sometimes answers a bad moment with its own error page and a normal `200` status. The passthrough cannot tell that from a real page. If you set `PASSTHROUGH_CACHE_REQUIRES` to something every real page contains (for example, the link prefix the site's result rows use), a page without it is kept for only a minute. Only HTML pages are checked this way; a PDF or an image keeps the full hour.

The cache holds at most 256 MiB in total (`PASSTHROUGH_CACHE_MAX_BYTES`). When it is full, the pages closest to expiring go first, and a single page bigger than a quarter of the limit is served but not kept.

All of these settings are in [Configuration](configuration.md#passthrough-proxy).
