# Privacy

sfx-skill is a skill: instructions and Python scripts that your agent runs. It has no server, no account and no telemetry.

- **What it reads**: the project files and sounds you point it at, its sample library, and its own files. If you name a music-production configuration, it reads that too and the tools it names. Nothing else on your disk is looked for.
- **What it writes**: renders and exports, in the folders you name; the takes of Surge layers, in `surge_takes` beside the project file; the recordings you import or download, in its library (`~/.sfx-skill/library` unless you name another) and the folder you name for downloads. Python also keeps its bytecode cache next to the scripts.
- **What it sends**: nothing. The only network access is the download of the recordings, when you agree to it: from this project's GitHub release, or from the dataset's record on Zenodo. Those requests carry none of your data. NumPy comes through pip when you or your agent install it.
- **What it keeps**: nothing. There is no sfx-skill service to keep anything. The audition page keeps your notes and the language you chose in your own browser's local storage. A music track you play on it stays in the page and is not kept.

Where the scripts run depends on your agent: on your computer in Claude Code or Codex, or in the agent's own sandbox in a chat. What you send to the agent is covered by that agent's privacy policy.

Questions: open an issue at https://github.com/owoevan-dev/sfx-skill/issues.
