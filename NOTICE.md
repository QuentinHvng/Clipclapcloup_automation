# Third-party notices

ClipClapCloup's own source is MIT licensed (see `LICENSE`). The released
executable also carries the following, each under its own terms.

## FFmpeg

The Windows build published at
<https://github.com/BtbN/FFmpeg-Builds/releases> (`ffmpeg-master-latest-win64-gpl`)
is bundled and run as a separate program. It is licensed under the **GNU
General Public License version 3 or later**, because it includes GPL-licensed
encoders such as libx264.

FFmpeg is not linked into this application and is not modified in any way;
this app merely invokes it as an external executable.

* FFmpeg source: <https://git.ffmpeg.org/ffmpeg.git>
* Exact build and its source references: <https://github.com/BtbN/FFmpeg-Builds>
* GPL v3 text: <https://www.gnu.org/licenses/gpl-3.0.html>

## Twemoji

Emoji images fetched for burned-in clip text come from Twemoji
(<https://github.com/twitter/twemoji>), copyright Twitter, Inc and other
contributors, licensed **CC-BY 4.0**. They are downloaded on demand and cached
locally; none are redistributed inside the executable.

## Poppins

The Poppins typeface, by Indian Type Foundry and Jonny Pinhorn, is bundled for
rendering text onto clips and is licensed under the **SIL Open Font License
1.1** (<https://openfontlicense.org/>).

## Python libraries

The build bundles yt-dlp (Unlicense), Requests (Apache-2.0), NumPy (BSD-3),
Pillow (MIT-CMU), qrcode (BSD) and pywebview (BSD-3), each under its own
licence.
