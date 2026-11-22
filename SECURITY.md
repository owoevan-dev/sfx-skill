# Security

sfx-skill runs on your own machine, with your rights. Its scripts read the project and audio files they are given and write their results into folders they are given. A render or an export goes into a new folder and never overwrites an earlier one.

- **Input files are data.** Projects, recordings, archives and the text files that come with them are read within limits, validated and never executed, also when they come from someone else.
- **Importing a folder or a zip.** Absolute paths, `..`, links and other unsafe member names refuse the whole archive. An import reads at most 20,000 files, 256 MiB each and 8 GiB in all, refuses an archive member compressed more than a thousand to one, and takes recordings of up to ten minutes. Links inside the library folder are refused. Files are written to new names and moved into place, so an existing file is never written through.
- **Downloads** happen only when you agree to them, over HTTPS, from this project's GitHub release or the dataset's record on Zenodo. Every file must match its published checksum before it is used: SHA-256 for the release, whose index is pinned by the skill, and MD5 for Zenodo's own files. Archives are listed before they are read.
- **External programs.** FFmpeg, if you have it, encodes Ogg, MP3 and FLAC exports and decodes recordings that are not WAV. It is started without a shell, is told each file's format and may open local files only, so a file posing as audio cannot make it fetch anything. With a music-production configuration, the skill also runs the programs that configuration names, so use the configuration its setup wrote on your machine, not one that arrived with a project or a download.
- **The audition page** is one HTML file with no network access. Text from a project is written into it as data and cannot close its script.

## Reporting a vulnerability

If you find a way to make a script do something it should not, such as writing outside the paths it was given, fetching from another host or running code from an input file, please report it privately: on this repository, open **Security**, then **Report a vulnerability** (or go straight to https://github.com/owoevan-dev/sfx-skill/security/advisories/new). Please don't open a public issue for it.

A fix goes into the next release, and the changelog says what was fixed.

Ordinary bugs are welcome as [issues](https://github.com/owoevan-dev/sfx-skill/issues).
