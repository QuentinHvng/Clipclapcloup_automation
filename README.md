# ClipClapCloup

Turn a YouTube video into vertical clips that are ready to post on TikTok.

Paste a link. The app downloads the video, listens to it to find the liveliest
stretch, reframes that stretch to 9:16, splits it into numbered parts and
drafts a caption for each one. You check them, then post — from your phone in
two taps, or straight through TikTok's API if you've set that up.

Everything runs on your own machine. Nothing leaves it unless you ask it to.

## Install

Download `ClipClapCloup.exe` from the [latest release](../../releases/latest)
and run it. There is nothing to install: ffmpeg and everything else is inside
the executable.

Windows will warn about an unknown publisher the first time, because the app
isn't code-signed. Choose **More info → Run anyway**.

## How it works

**Making clips.** Give it a link, pick how many parts you want and how long
each should be. Progress is real, not a spinner: the download is the long part
because the whole source video has to arrive before anything can be cut.

**Picking the moment** is a loudness heuristic — the app measures the audio
once per second and takes the densest window. It has no idea what is being
said, which is exactly why every clip waits for you in **Ready to post**
instead of going out on its own.

**Posting by hand** is the simple path and has no setup at all. Each clip has
a *Send to phone* button that shows a QR code; scan it with your phone on the
same Wi-Fi, save the video, post it from the TikTok app. The caption is on that
page too, with a copy button. There's also *Open folder* if you'd rather move
the file yourself.

**Posting through the API** is optional and takes a few minutes to set up —
Settings walks you through creating your own TikTok developer app. Read the
caveat below before bothering.

**Captions.** By default each clip gets the video's real title, its part
number, a follow prompt and a few generic hashtags — nothing invented about
what is in the clip. Add your own Anthropic API key in Settings and captions
get written properly instead. Either way you can rewrite them.

### The TikTok API caveat

Until TikTok reviews and approves *your* developer app, everything posted
through their API arrives **private, visible only to you**. That is TikTok's
rule for unaudited apps and no setting in this app can change it. Getting
audited means submitting your app to TikTok with a verified domain you own.

Posting by hand has none of these limits, which is why it is the default.

## Where things are kept

| What | Where |
| --- | --- |
| Your clips | `Videos\ClipClapCloup\<video>\` (changeable in Settings) |
| Settings, clip library, TikTok tokens | `%APPDATA%\ClipClapCloup\` |

Settings and tokens are plain files. Anyone who can use your Windows account
can read them, so treat the client secret the way you'd treat a password.

## Running from source

```bash
git clone <this repo>
cd clipclapcloup
pip install -r requirements.txt
python run.py
```

You need `ffmpeg` on your `PATH` when running this way — the bundled copy only
exists in the packaged build.

## Building the executable

Pushing a tag starting with `v` builds it on GitHub and attaches the result to
a release. The workflow is in `.github/workflows/build.yml`; it can also be run
by hand from the Actions tab. To build locally on Windows, drop an `ffmpeg.exe`
in `bin/` and run `pyinstaller clipclapcloup.spec`.

## Licence and notices

The app's own code is MIT licensed — see `LICENSE`.

The released executable bundles **FFmpeg**, which is separate software under
the GPL, invoked as an external program. See `NOTICE.md` for the build used
and where to get its source.

Emoji artwork used in burned-in text comes from **Twemoji** by Twitter, CC-BY
4.0. The bundled **Poppins** typeface is under the SIL Open Font License.
