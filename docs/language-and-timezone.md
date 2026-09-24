# Language and timezone

_Doc map: [README.md](README.md)._

A website can compare the language and the clock a browser reports with the country its IP address is in. If they disagree, that is a sign of a bot. So Solverr gives both engines the same language and timezone, worked out together from the same place.

**Out of the box you need to set nothing.** Solverr looks up the IP address the browser connects from (through your proxy, if you use one), works out that address's country, and picks a matching language and timezone. It does this once and reuses the answer, so both engines always report the same country.

Set something here only when you need a specific result.

## The three settings

They layer on top of each other:

| You set | The browser gets | Looks up the IP? |
| ------- | ---------------- | ---------------- |
| nothing | Language and timezone from the IP address | once per proxy |
| `BROWSER_GEO=de-DE` | German, `Europe/Berlin` | no |
| `LANG=de-DE` | German, timezone still from the IP address | once per proxy |
| `BROWSER_TIMEZONE=Europe/Berlin` | `Europe/Berlin`, language still from the IP address | once per proxy, for the language |
| `BROWSER_TIMEZONE=Europe/Berlin` and `LANG=de-DE` | `Europe/Berlin`, German | no |
| `BROWSER_TIMEZONE=auto` | From the IP address, ignoring any `BROWSER_GEO` | once per proxy |

`LANG` and `BROWSER_TIMEZONE` each win over `BROWSER_GEO` for their own half. So `BROWSER_GEO=en-US` with `BROWSER_TIMEZONE=America/Chicago` gives American English on Chicago time. Set both halves and the language and timezone need no lookup; set one and only the other half is looked up.

"Once per proxy" means once per proxy account, not per proxy server. Residential proxy providers choose the exit country through the username, so two accounts on the same server each get their own country.

### `BROWSER_GEO`

The short way to match a proxy that always connects from the same country. It needs no lookup, which also makes it the right choice when Solverr has no internet access except through its proxy.

A country with several timezones gets its most populous one, and Solverr writes the one it picked to the log at startup: `BROWSER_GEO=en-US` gives `America/New_York`. Set `BROWSER_TIMEZONE` if that is not the one you want. A tag without a country, such as `fr`, sets the language only.

### `LANG`

Accepts both the Linux form and the language-tag form, and turns either into a tag before a browser sees it:

| You set | Both engines use |
| ------- | ---------------- |
| `en_US.UTF-8` | `en-US` |
| `de_DE@euro` | `de-DE` |
| `pt-BR` | `pt-BR` |
| `zh_Hans_CN` | `zh-Hans-CN` |
| `fr` | `fr` |
| `C`, `POSIX` | ignored: `BROWSER_GEO` is used, then the IP address |

A value that is not a language is ignored, with a warning in the log, instead of being passed on. A malformed value would reach the page's `navigator.languages` and the `Accept-Language` header as it is, which stands out more than setting nothing. `C.UTF-8` is ignored for the same reason, and because it is a container default nobody chose.

Whatever the language is, both engines report it the way a desktop browser does, as two entries: `de-DE` becomes `["de-DE", "de"]`.

### `BROWSER_TIMEZONE`

Takes any timezone name the container's timezone data lists, such as `Europe/Berlin`. Setting it skips the timezone lookup, but the language still comes from the IP address. On a machine with no internet access, set `LANG` too, or use `BROWSER_GEO`, to skip the lookup completely.

A name that is not in that data (a typo like `Europe/Stockholmm`) is ignored with a warning that names it, and the timezone goes back to `auto`. Passing a typo on would put the two engines in different timezones.

## Before you change anything

Forcing a language a country does not speak, or a timezone it is not in, is exactly the mismatch a website can see. Change one only if you know why.

Some countries share a timezone definition with a neighbour, which looks wrong but is not: Norway reports `Europe/Berlin` and the Netherlands `Europe/Brussels`. Those are the same zone, with the same offset and the same daylight-saving rules.

## When the lookup fails

If the IP address cannot be looked up, Solverr uses the container's `TZ` for the timezone and `en-US` for the language, logs a warning, and carries on. The request does not fail. Solverr remembers that fallback for only a minute, so the first request after that looks the IP address up again, and a short network outage does not leave the wrong country in place for long.

A SOCKS proxy needs PySocks installed for the lookup to work; without it you get the same fallback. When you use a SOCKS proxy, set `BROWSER_TIMEZONE` or `BROWSER_GEO`.

If the warning says every service failed in 0.0s with a name-resolution error, the container cannot resolve any hostname at all. Changing lookup services will not help; check the container's DNS. See [Troubleshooting](troubleshooting.md#the-log-says-every-ip-lookup-service-failed).

## Which services look up the IP address

Solverr asks `api.ipify.org`, then `icanhazip.com`, then `checkip.amazonaws.com`, stopping at the first that answers.

To use your own services, list them in `GEO_IP_LOOKUP_URLS`, separated by commas. They are tried first, and the built-in ones still follow them. Each must:

- be an `https` address. A lookup travels through the proxy of the request that caused it, and a plain `http` one could be read by that proxy.
- answer with nothing but the IP address, as plain text. `https://ifconfig.co/ip` works; its JSON form does not.
- answer quickly. All the services share one 15-second limit per lookup, so one of yours that hangs uses up time the built-in ones would have had.

Behind a proxy, the stealth engine also looks up the IP address through the proxy every time it starts a browser, to give the browser's WebRTC the right address. That happens even when `BROWSER_GEO` or both halves are set. It uses the same list of services, and a failure there never stops the browser from starting.
